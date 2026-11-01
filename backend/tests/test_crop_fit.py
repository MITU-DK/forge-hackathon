"""Crop suitability: EcoCrop limits applied to a climate. Synthetic climates make every expected score checkable by hand."""
import datetime as dt

import pytest

from app.decision import crop_fit as cf
from app.tools import crop_fit as tool
from app.tools.http import Fetched
from tests.test_tools import GEO, install as _install_old


def install(routes_fn):
    """The older helper patches only location/weather/mandi; the archive call lives in the crop_fit tool."""
    _install_old(routes_fn)
    tool.get_json = lambda url, params=None, ttl=None, timeout=None: Fetched(routes_fn(url, params or {}), url, "2026-11-01T10:00:00Z", False, 0)


HEAVY_CLAY = {"ph": 7.0, "sand_pct": 25, "silt_pct": 30, "clay_pct": 44, "organic_carbon_g_kg": 11.2, "texture_class": "clay", "texture_group": "heavy", "valid_pixels": 100}


@pytest.fixture(autouse=True)
def no_network_soil(monkeypatch):
    """The tool asks the soil service too; these tests must not reach it (the soil tests have their own)."""
    from app.tools import soil as soil_tool

    monkeypatch.setattr(soil_tool, "fetch_summary", lambda loc: (HEAVY_CLAY, Fetched(b"", "u", "t", False, 0)))


def flat(tmean=25.0, tmin=20.0, tmax=30.0, rain=2.0):
    return [{"tmean": tmean, "tmin": tmin, "tmax": tmax, "rain": rain} for _ in range(cf.YEAR)]


def test_the_table_is_built_from_the_database_and_every_crop_can_be_scored():
    crops = cf.load_crops()
    assert len(crops) == 30 and crops["wheat"].gmin == 90 and crops["onion"].topmn == 12 and crops["rice"].ropmn == 1500
    assert all(c.tmin < c.topmn <= c.topmx < c.tmax and c.gmin <= c.gmax for c in crops.values())


def test_a_crop_in_its_optimal_range_scores_100_and_one_far_outside_scores_0():
    c = cf.load_crops()
    assert cf.score_start(flat(25), 0, c["maize"])["temp"] == 100  # maize optimum 18-33
    cold = cf.score_start(flat(2, 0, 6), 0, c["maize"])
    assert cold["temp"] == 0 and cold["optimal_share"] == 0


def test_partial_suitability_follows_the_documented_formula():
    wheat = cf.load_crops()["wheat"]  # optimum 15-23, absolute 5-27, cycle 90-250: at 25 C each day is worth (27-25)/(27-23) = 0.5
    s = cf.score_start(flat(25, 20, 26), 0, wheat)
    assert s["cycle_days"] == 180 and s["temp"] == 44  # 90 suitable days need 180 days; 100 * (1 - (180-90)/(250-90)) = 43.75


def test_frost_kills_and_heat_days_are_counted_not_subtracted():
    pea = cf.load_crops()["pea"]  # killing temperature 0
    clim = flat(15, 15, 20)
    clim[10]["tmin"] = -2.0
    killed = cf.score_start(clim, 0, pea)
    assert killed["temp"] == 0 and killed["frost"] is True  # a daily minimum below the killing temperature inside the cycle
    ok = cf.score_start(flat(15, 10, 20), 0, pea)
    assert ok["temp"] == 100
    hot = cf.score_start(flat(15, 10, 33), 0, pea)  # daytime maximum above the crop's absolute limit (30 C)
    assert hot["temp"] == 100 and hot["hot_days"] == hot["cycle_days"]


def test_rain_score_and_labels_use_the_optimal_and_absolute_limits():
    c = cf.load_crops()["wheat"]  # optimum 750-900, absolute 300-1600
    assert cf._rain_score(800, c) == 100 and cf._rain_score(300, c) == 0 and cf._rain_score(1600, c) == 0
    assert 0 < cf._rain_score(500, c) < 100
    assert cf.rain_label(800, c) == "RAIN_COVERS_NEED" and cf.rain_label(500, c) == "NEEDS_IRRIGATION" and cf.rain_label(100, c) == "NEEDS_IRRIGATION_RAIN_FAR_BELOW_NEED"
    assert cf.rain_label(1000, c) == "WETTER_THAN_OPTIMUM" and cf.rain_label(2000, c) == "FAR_WETTER_THAN_CROP_TOLERATES"


def test_ranking_puts_crops_that_fit_first_and_sowing_windows_are_one_per_season():
    clim = flat(25)
    ranked = cf.rank_for_sowing(clim, dt.date(2026, 6, 1))
    assert ranked[0][1]["temp"] == 100 and ranked[-1][1]["temp"] <= ranked[0][1]["temp"]
    maize = cf.load_crops()["maize"]
    w = cf.sowing_windows(clim, maize, dt.date(2026, 6, 1), top=3)
    starts = [dt.date.fromisoformat(x["sow_on"]) for x in w]
    assert len(w) == 3 and all((b - a).days >= 45 for a, b in zip(starts, starts[1:]))


def test_harvest_status_against_the_usual_cycle():
    onion = cf.load_crops()["onion"]  # 85-175 days
    today = dt.date(2026, 10, 31)
    assert cf.harvest_status(onion, dt.date(2026, 9, 1), today)["status"] == "BEFORE_EARLIEST_HARVEST"
    h = cf.harvest_status(onion, dt.date(2026, 7, 1), today)  # 122 days
    assert h["status"] == "IN_HARVEST_WINDOW" and h["earliest"] == "2026-09-24" and h["latest"] == "2026-12-23"
    assert cf.harvest_status(onion, dt.date(2026, 1, 1), today)["status"] == "PAST_LATEST_CYCLE"
    assert cf.harvest_status(onion, dt.date(2026, 11, 20), today)["status"] == "NOT_SOWN_YET"


def test_ph_fit_labels():
    onion = cf.load_crops()["onion"]  # optimum 6-7, absolute 4.3-8.3
    assert cf.ph_fit(onion, 6.5)["label"] == "OPTIMAL" and cf.ph_fit(onion, 5.0)["label"] == "ACCEPTABLE" and cf.ph_fit(onion, 3.5)["label"] == "OUTSIDE_TOLERANCE"


def test_climatology_needs_every_day_of_the_year_and_folds_the_leap_day():
    days = [{"date": (dt.date(2023, 1, 1) + dt.timedelta(days=i)).isoformat(), "tmean": 20.0, "tmin": 15.0, "tmax": 25.0, "rain": 1.0} for i in range(365)]
    assert cf.climatology(days) is not None and len(cf.climatology(days)) == 365
    assert cf.climatology(days[:300]) is None
    assert cf.day_index(dt.date(2024, 3, 1)) == cf.day_index(dt.date(2023, 3, 1)) and cf.day_index(dt.date(2024, 12, 31)) == 364


# ------------------------------------------------------------------ the tool
def archive(rain=2.0, tmean=25.0):
    start = dt.date.today().year - tool.YEARS
    days = [dt.date(start, 1, 1) + dt.timedelta(days=i) for i in range((dt.date(dt.date.today().year, 1, 1) - dt.date(start, 1, 1)).days)]
    return {"daily": {"time": [d.isoformat() for d in days], "temperature_2m_mean": [tmean] * len(days), "temperature_2m_max": [tmean + 5] * len(days),
                      "temperature_2m_min": [tmean - 5] * len(days), "precipitation_sum": [rain] * len(days)}}


def routes(url, params):
    if "geocoding" in url:
        return GEO
    if "archive-api" in url:
        return archive()
    raise AssertionError(url)


def test_the_tool_lists_suitable_crops_with_sources_and_the_climate_only_warning():
    install(routes)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "CROP FIT for" in r.text and "CLIMATE-ONLY" in r.text and "not a profit ranking" in r.text and "SUITABLE CROPS" in r.text
    assert any(s["name"].startswith("Open-Meteo historical weather") for s in r.sources) and r.cards[0]["type"] == "crop_fit" and r.cards[0]["crops"]


def test_the_tool_gives_windows_for_the_farmers_crop_and_a_harvest_window_from_the_sowing_date():
    install(routes)
    sown = (dt.date.today() - dt.timedelta(days=100)).strftime("%d/%m/%Y")
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra", "crop": "Onion", "sowing_date": sown})
    assert "YOUR CROP Onion:" in r.text and "BEST SOWING WINDOWS for Onion" in r.text and "SOWN " in r.text and "STAGE: IN_HARVEST_WINDOW" in r.text
    assert r.facts["harvest"]["days_since_sowing"] == 100


def test_names_the_mandi_tool_uses_map_to_the_table_and_unknown_crops_get_no_invented_dates():
    install(routes)
    assert tool.table_key("Soyabean") == "soybean" and tool.table_key("Paddy(Common)") == "rice" and tool.table_key("Chana") == "gram"
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra", "crop": "Banana"})
    assert "YOUR CROP Banana: no EcoCrop limits are loaded" in r.text and "BEST SOWING WINDOWS" not in r.text


def test_an_unreadable_sowing_date_is_said_not_guessed():
    install(routes)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra", "crop": "Onion", "sowing_date": "last monsoon"})
    assert "could not be read as a date" in r.text and "STAGE" not in r.text
    assert tool.parse_sowing_date("2040-01-01", dt.date.today()) is None and tool.parse_sowing_date("31/12/2025", dt.date(2026, 6, 1)) == dt.date(2025, 12, 31)


def test_a_failed_climate_source_is_reported_not_replaced_by_a_guess(monkeypatch):
    from app.tools import SourceUnavailable
    from app.tools import http as http_mod

    def boom(url, params=None, ttl=None, timeout=None):
        if "archive-api" in url:
            raise SourceUnavailable("down")
        return Fetched(GEO, url, "t", False, 0)

    install(routes)
    monkeypatch.setattr(tool, "get_json", boom)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra"})
    assert not r.ok and "SOURCE UNAVAILABLE" in r.text and "Do NOT guess" in r.text


def test_crops_the_soil_rules_out_are_removed_from_the_suitable_list_and_named():
    install(routes)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra"})
    assert "SOIL used to filter the list: pH 7, clay (heavy texture)" in r.text
    listed = [ln.split(":")[0][2:] for ln in r.text.splitlines() if ln.startswith("- ")]
    assert "Bajra" not in listed and "Chilli" not in listed  # heavy texture is not tolerated by either (EcoCrop)
    line = next(ln for ln in r.text.splitlines() if "FIT THE CLIMATE BUT NOT THE SOIL" in ln)
    assert "Bajra (heavy soil texture is not tolerated)" in line and "Chilli (heavy soil texture is not tolerated)" in line


def test_if_the_soil_service_is_down_nothing_is_filtered_and_the_text_says_so(monkeypatch):
    from app.tools import SourceUnavailable
    from app.tools import soil as soil_tool

    def down(loc):
        raise SourceUnavailable("503")

    monkeypatch.setattr(soil_tool, "fetch_summary", down)
    install(routes)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "SOIL not checked" in r.text and "not filtered by soil" in r.text and "SUITABLE CROPS" in r.text


def test_the_calendar_card_has_harvest_ranges_from_the_cycle_length_and_the_farmers_own_sowing():
    install(routes)
    sown = dt.date.today() - dt.timedelta(days=100)
    r = tool.crop_fit("", {"place": "Nashik, Maharashtra", "crop": "Onion", "sowing_date": sown.strftime("%d/%m/%Y")})
    cal = next(c for c in r.cards if c["type"] == "crop_calendar")
    gmin, gmax = cal["cycle_days"]
    assert cal["spans"] and all((dt.date.fromisoformat(s["harvest_from"]) - dt.date.fromisoformat(s["sow_on"])).days == gmin for s in cal["spans"])
    assert cal["sown"]["sowing"] == sown.isoformat() and cal["sown"]["status"] == "IN_HARVEST_WINDOW" and "calendar" not in r.facts


def test_no_crop_or_unknown_crop_means_no_calendar():
    install(routes)
    assert all(c["type"] != "crop_calendar" for c in tool.crop_fit("", {"place": "Nashik, Maharashtra"}).cards)
    assert all(c["type"] != "crop_calendar" for c in tool.crop_fit("", {"place": "Nashik, Maharashtra", "crop": "Banana"}).cards)
