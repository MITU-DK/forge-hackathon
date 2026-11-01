"""'Use my location': coordinates -> a place the other tools resolve. Network replaced by response-shape fixtures."""
import pytest
from fastapi.testclient import TestClient

import app.tools.location as location
import app.tools.reverse_geo as rg
from app.api import ratelimit
from app.main import app
from app.tools import SourceUnavailable
from app.tools.http import Fetched


@pytest.fixture
def client():
    ratelimit._hits.clear()
    with TestClient(app) as c:
        yield c
    ratelimit._hits.clear()


def install(monkeypatch, reverse, geocode=None):
    """reverse: Nominatim `address` dict (or None); geocode: fn(name) -> list of geocoder results."""
    def fake(url, params=None, ttl=None, timeout=None):
        if "nominatim" in url:
            if reverse is None:
                raise SourceUnavailable("down")
            return Fetched({"address": reverse}, url, "2026-10-31T10:00:00Z", False, 0)
        return Fetched({"results": (geocode or (lambda n: [{"name": n, "latitude": 20.0, "longitude": 74.0, "admin1": "Maharashtra", "admin2": "Nashik", "population": 1}]))(params["name"])}, url, "t", False, 0)

    monkeypatch.setattr(rg, "get_json", fake)
    monkeypatch.setattr(location, "get_json", fake)


YEOLA = {"town": "Yeola", "county": "Yeola Taluka", "state_district": "Nashik District", "state": "Maharashtra", "country_code": "in"}


def test_a_town_resolves_to_town_district_state(monkeypatch):
    install(monkeypatch, YEOLA)
    r = rg.locate(20.04, 74.49)
    assert r["place"] == "Yeola, Nashik, Maharashtra" and r["state"] == "Maharashtra" and r["source"]["name"] == "OpenStreetMap Nominatim"


def test_a_village_the_geocoder_does_not_know_falls_back_to_the_taluka_then_the_district(monkeypatch):
    rural = {"county": "Niphad Taluka", "state_district": "Nashik District", "state": "Maharashtra", "country_code": "in"}
    install(monkeypatch, rural, geocode=lambda n: [] if n == "Niphad" else [{"name": n, "latitude": 1, "longitude": 2, "admin1": "Maharashtra", "admin2": "Nashik", "population": 1}])
    assert rg.locate(20.14, 74.24)["place"] == "Nashik, Maharashtra"  # taluka unknown to the geocoder -> the district town


def test_coordinates_outside_india_are_refused_without_a_lookup(monkeypatch):
    called = []
    monkeypatch.setattr(rg, "get_json", lambda *a, **k: called.append(1))
    for lat, lon in ((51.5, -0.1), (0.0, 0.0), (28.6, 120.0)):
        try:
            rg.locate(lat, lon)
            raise AssertionError("expected PlaceNotFound")
        except rg.PlaceNotFound:
            pass
    assert not called


def test_a_point_nominatim_places_in_another_country_is_refused(monkeypatch):
    install(monkeypatch, {"city": "Kathmandu", "state": "Bagmati", "country_code": "np"})
    try:
        rg.locate(27.7, 85.3)  # inside the rough India box, but Nepal
        raise AssertionError("expected PlaceNotFound")
    except rg.PlaceNotFound:
        pass


def test_route_returns_a_place_or_a_friendly_error_and_never_a_500(client, monkeypatch):
    install(monkeypatch, YEOLA)
    ok = client.get("/api/locate", params={"lat": 20.04, "lon": 74.49})
    assert ok.status_code == 200 and ok.json()["place"] == "Yeola, Nashik, Maharashtra"
    abroad = client.get("/api/locate", params={"lat": 51.5, "lon": -0.1}).json()
    assert "outside India" in abroad["error"] and "place" not in abroad
    install(monkeypatch, None)  # the reverse geocoder is down
    down = client.get("/api/locate", params={"lat": 20.04, "lon": 74.49})
    assert down.status_code == 200 and "not reachable" in down.json()["error"] and "nominatim" not in down.text.lower()
    assert client.get("/api/locate", params={"lat": "abc", "lon": 74}).status_code == 422
    assert client.get("/api/locate", params={"lat": 200, "lon": 74}).status_code == 422
