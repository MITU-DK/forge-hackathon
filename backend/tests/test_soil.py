"""Soil: the TIFF reader, the USDA/EcoCrop rules and the tool, on real ISRIC SoilGrids responses saved as fixtures (Nashik)."""
from pathlib import Path

import pytest

from app.decision import crop_fit as cf
from app.decision import soil_rules as sr
from app.tools import SourceUnavailable
from app.tools import soil as soil_tool
from app.tools.geotiff import TiffError, read_tiff
from app.tools.http import Fetched
from tests.test_tools import GEO, install

FIX = Path(__file__).parent / "fixtures"


def load(prop):
    return (FIX / f"soilgrids_nashik_{prop}.tif").read_bytes()


def test_the_tiff_reader_decodes_a_real_soilgrids_response():
    g = read_tiff(load("phh2o"))
    flat = [v for row in g for v in row]
    assert len(g) > 30 and len(g[0]) > 30 and 60 <= max(flat) <= 90 and 0 in flat  # pH x10 around 7; zero = masked built-up/water pixels
    assert all(0 <= v <= 120 for v in flat)


def test_the_tiff_reader_refuses_what_it_cannot_read_instead_of_guessing():
    for bad in (b"", b"not a tiff at all", b"II*\x00\x08\x00\x00\x00\x00\x00", load("phh2o")[:200]):
        with pytest.raises(TiffError):
            read_tiff(bad)


@pytest.mark.parametrize("sand,silt,clay,cls", [(92, 5, 3, "sand"), (60, 25, 15, "sandy loam"), (40, 40, 20, "loam"), (10, 70, 20, "silt loam"), (25, 30, 45, "clay"), (30, 35, 35, "clay loam"), (20, 15, 65, "clay")])
def test_usda_texture_classes(sand, silt, clay, cls):
    assert sr.usda_texture(sand, silt, clay) == cls


def test_texture_groups_and_crop_fit_use_the_ecocrop_words():
    crops = cf.load_crops()
    assert sr.texture_group("clay", 44) == "heavy" and sr.texture_group("sandy loam", 12) == "light" and sr.texture_group("loam", 20) == "medium"
    assert sr.texture_fit(crops["bajra"], "heavy") == "NOT_TOLERATED"  # bajra: medium, light
    assert sr.texture_fit(crops["wheat"], "medium") == "OPTIMAL" and sr.texture_fit(crops["wheat"], "heavy") == "TOLERATED"
    assert sr.texture_fit(crops["rice"], "light") == "OPTIMAL"  # "wide"
    assert sr.texture_fit(crops["maize"], "heavy") is None  # the database has no texture for maize: no verdict


def test_the_tool_reports_the_soil_and_the_farmers_crop_with_a_model_estimate_warning(monkeypatch):
    install(lambda url, params: GEO)

    def fake(url, params=None, ttl=None, timeout=None):
        prop = params["COVERAGEID"].split("_")[0]
        return Fetched(load(prop), url, "2026-10-31T00:00:00Z", False, 0)

    monkeypatch.setattr(soil_tool, "get_bytes", fake)
    r = soil_tool.soil("", {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert r.ok and "MODEL ESTIMATE" in r.text and "not a measurement of the field" in r.text and "USDA texture class: clay" in r.text
    assert r.facts["soil"]["texture_group"] == "heavy" and 6.5 <= r.facts["soil"]["ph"] <= 7.5 and 35 <= r.facts["soil"]["clay_pct"] <= 55
    assert "SOIL FIT for Onion" in r.text and "CROPS THIS SOIL DOES NOT SUIT" in r.text and r.cards[0]["type"] == "soil"
    assert r.sources[0]["name"].startswith("ISRIC SoilGrids")


def test_an_all_masked_area_is_said_not_filled_in(monkeypatch):
    install(lambda url, params: GEO)
    zeros = [[0] * 5 for _ in range(5)]
    assert soil_tool.summarise({p: zeros for p in soil_tool.SCALE}) is None
    monkeypatch.setattr(soil_tool, "_grid", lambda prop, lon, lat: (zeros, Fetched(b"", "u", "t", False, 0)))
    r = soil_tool.soil("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "has no soil data within about 5 km" in r.text and "give no soil advice" in r.text


def test_an_unreachable_soil_service_does_not_degrade_the_answer_and_is_said_plainly(monkeypatch):
    install(lambda url, params: GEO)

    def down(prop, lon, lat):
        raise SourceUnavailable("503")

    monkeypatch.setattr(soil_tool, "_grid", down)
    r = soil_tool.soil("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "did not answer" in r.text and "give no soil advice" in r.text and r.facts == {"soil": None}


def test_two_tools_asking_at_the_same_time_share_one_fetch_of_the_soil(monkeypatch):
    import threading
    import time

    calls = {"n": 0}
    lock = threading.Lock()

    def slow_grid(prop, lon, lat):
        with lock:
            calls["n"] += 1
        time.sleep(0.05)
        return read_tiff(load(prop)), Fetched(b"", "u", "t", False, 0)

    monkeypatch.setattr(soil_tool, "_grid", slow_grid)
    loc = {"lat": 19.99, "lon": 73.79}
    out = []
    threads = [threading.Thread(target=lambda: out.append(soil_tool.fetch_summary(loc))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert calls["n"] == 5 and len(out) == 4 and all(o[0] == out[0][0] for o in out)  # five layers once, not twenty
