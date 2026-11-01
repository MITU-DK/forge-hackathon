"""The geocoder's top hit is not always what was typed ("Nasik" -> a village called Vani): old names are mapped, and a hit that
does not look like the typed name is replaced by a better-named candidate or refused, never used silently."""
import pytest

from app.tools import PlaceNotFound
from app.tools import location as loc
from app.tools.http import Fetched


def geo(results_by_query):
    def fake(url, params=None, ttl=None, timeout=None):
        return Fetched({"results": results_by_query.get(params["name"], [])}, url, "2026-10-31T00:00:00Z", False, 0)
    return fake


def rec(name, admin1="Maharashtra", admin2="Nashik", pop=None, i=1):
    return {"id": i, "name": name, "admin1": admin1, "admin2": admin2, "latitude": 19.9, "longitude": 73.8, "population": pop}


def test_old_spelling_is_mapped_to_the_name_the_geocoder_knows(monkeypatch):
    seen = []
    monkeypatch.setattr(loc, "get_json", lambda url, params=None, **k: (seen.append(params["name"]), Fetched({"results": [rec("Nashik", pop=1486053)]}, url, "t", False, 0))[1])
    assert loc.resolve_location({"place": "Nasik"})["name"] == "Nashik" and seen == ["Nashik"]


def test_a_top_hit_with_a_different_name_is_replaced_by_the_one_named_like_the_query(monkeypatch):
    monkeypatch.setattr(loc, "get_json", geo({"Pimpri": [rec("Vani", i=1), rec("Pimpri", "Maharashtra", "Pune", 500, i=2)]}))
    assert loc.resolve_location({"place": "Pimpri"})["name"] == "Pimpri"


def test_a_place_that_only_matches_something_else_is_refused_not_guessed(monkeypatch):
    monkeypatch.setattr(loc, "get_json", geo({"Nasikk": [rec("Vani")]}))
    with pytest.raises(PlaceNotFound):
        loc.resolve_location({"place": "Nasikk"})


@pytest.mark.parametrize("typed,hit", [("Sindhudurg", rec("Sindhudurg Airport", admin2="Sindhudurg District")), ("Kolhapur", rec("Kolhāpur", admin2="Kolhapur")), ("Belagavi", rec("Belagavi", "Karnataka", "Belgaum district"))])
def test_legitimate_variants_are_still_accepted(monkeypatch, typed, hit):
    monkeypatch.setattr(loc, "get_json", geo({typed: [hit]}))
    assert loc.resolve_location({"place": typed})["lat"] == 19.9


def nominatim(results):
    def fake(url, params=None, ttl=None, timeout=None):
        if "geocoding" in url:
            return Fetched({"results": []}, url, "t", False, 0)
        return Fetched(results, url, "t", False, 0)
    return fake


def hit(name, type_, state, district, lat="19.9", lon="74.4", imp=0.16):
    return {"name": name, "type": type_, "importance": imp, "lat": lat, "lon": lon, "address": {"country_code": "in", "state": state, "state_district": district}}


def test_a_devanagari_town_the_first_finder_lacks_is_found_by_the_second(monkeypatch):
    monkeypatch.setattr(loc, "get_json", nominatim([hit("Manmad", "town", "Maharashtra", "Nashik District")]))
    r = loc.resolve_location({"place": "मनमाड"})
    assert (r["name"], r["district"], r["state"]) == ("Manmad", "Nashik", "Maharashtra") and r["source"]["name"] == "OpenStreetMap Nominatim"


def test_the_second_finder_never_accepts_a_village_or_another_state_than_the_one_typed(monkeypatch):
    monkeypatch.setattr(loc, "get_json", nominatim([hit("Kalwana", "village", "Rajasthan", "Udaipur")]))
    with pytest.raises(PlaceNotFound):
        loc.resolve_location({"place": "कळवण"})  # only a hamlet in another state: ask for a nearer town instead
    monkeypatch.setattr(loc, "get_json", nominatim([hit("Kalwan", "town", "Rajasthan", "Udaipur", imp=0.3), hit("Kalwan", "town", "Maharashtra", "Nashik District", imp=0.1)]))
    assert loc.resolve_location({"place": "कळवण, Maharashtra"})["state"] == "Maharashtra"  # the typed state wins over importance


def test_english_names_never_use_the_second_finder(monkeypatch):
    monkeypatch.setattr(loc, "get_json", nominatim([hit("Manmad", "town", "Maharashtra", "Nashik District")]))
    with pytest.raises(PlaceNotFound):
        loc.resolve_location({"place": "Zzzzzz"})
