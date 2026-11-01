"""4.2 crop rules: cited, region-aware, silent when no verified rule exists."""
import datetime as dt

import app.tools.crop_stage as cs
from app.decision.crop_rules import crop_advice, has_rules
from app.tools import SourceUnavailable
from tests.test_tools import forecast, install, routes_ok


def day(date, tmax=30.0, tmin=20.0):
    return {"date": date, "tmax": tmax, "tmin": tmin}


def test_tomato_heat_and_cold_fire_with_source():
    a = crop_advice("tomato", "Maharashtra", [day("d1", 36.0), day("d2", 33.0, 8.0)])
    codes = {x["code"] for x in a}
    assert codes == {"TOMATO_HEAT_FRUIT_SET", "TOMATO_COLD"} and all("nhb.gov.in" in x["source"] for x in a)
    assert "36.0 C on d1" in a[0]["text"] and "2 day(s)" in a[0]["text"]


def test_tomato_boundaries_do_not_fire():
    assert crop_advice("Tomato", "Maharashtra", [day("d", 32.0, 10.0)]) == []  # rule is strictly above 32 / below 10


def test_wheat_is_punjab_only_and_onion_has_no_rule():
    d = [day("d")]
    assert crop_advice("Wheat", "Maharashtra", d, dt.date(2026, 11, 10)) == []
    assert crop_advice("Onion", "Maharashtra", d) == [] and not has_rules("Onion", "Maharashtra")
    assert has_rules("wheat", "punjab")


def test_wheat_sowing_window_dates():
    f = lambda m, dd: [x["code"] for x in crop_advice("Wheat", "Punjab", [day("d")], dt.date(2026, m, dd))]
    assert f(10, 24) == [] and f(10, 25) == ["WHEAT_SOWING_WINDOW_OPEN"] and f(11, 15) == ["WHEAT_SOWING_WINDOW_OPEN"]
    assert f(11, 16) == ["WHEAT_SOWING_LATE"] and f(12, 20) == ["WHEAT_SOWING_LATE"] and f(6, 1) == []


def test_wheat_terminal_heat_only_in_grain_fill_months_above_31_mean():
    hot = [day("2027-03-10", 40.0, 24.0), day("2027-03-11", 30.0, 20.0)]
    assert [x["code"] for x in crop_advice("Wheat", "Punjab", hot, dt.date(2027, 3, 10))] == ["WHEAT_TERMINAL_HEAT"]
    assert crop_advice("Wheat", "Punjab", hot, dt.date(2026, 7, 10)) == []
    assert crop_advice("Wheat", "Punjab", [day("d", 36.0, 26.0)], dt.date(2027, 3, 10)) == []  # mean exactly 31.0 is not above


def test_tool_uses_live_forecast_shape_and_cites():
    install(routes_ok)  # forecast(): tmax 33, tmin 20 every day
    r = cs.crop_stage("", {"place": "Nashik, Maharashtra", "crop": "Tomato"})
    assert r.ok and "TOMATO_HEAT_FRUIT_SET" in r.text and "Source: NHB" in r.text and "33.0 C" in r.text


def test_tool_says_no_rule_instead_of_inventing():
    install(routes_ok)
    r = cs.crop_stage("", {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert r.ok and "no verified crop-specific rule" in r.text and "Do not invent" in r.text
    assert "no crop selected" in cs.crop_stage("", {"place": "Nashik, Maharashtra"}).text


def test_tool_survives_forecast_outage_without_failing_the_turn():
    def down(url, params):
        if "geocoding" in url:
            return {"results": [{"name": "Nashik", "latitude": 1, "longitude": 2, "admin1": "Maharashtra", "admin2": "Nashik"}]}
        raise SourceUnavailable("timeout")

    install(down)
    r = cs.crop_stage("", {"place": "Nashik", "crop": "Tomato"})
    assert r.ok and "forecast is unavailable" in r.text
