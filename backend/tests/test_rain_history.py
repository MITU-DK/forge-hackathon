"""Rain history rules + tool, with the network replaced by response-SHAPE fixtures."""
import app.tools.location as location
import app.tools.rain_history as rh
from app.decision.weather_rules import analyze_rain_history as arh
from app.tools.http import Fetched
from tests.test_tools import GEO


def test_dry_spell_and_irrigation_urgent():
    a = arh([0.0] * 30, [0.5] * 7)
    assert "DRY_SPELL_ALERT" in a["alerts"] and "IRRIGATION_URGENT" in a["alerts"]


def test_waterlogging_risk():
    a = arh([0.0] * 23 + [20.0] * 7, [15.0, 10.0, 10.0, 0, 0, 0, 0])
    assert a["past_7d_mm"] == 140.0 and a["alerts"] == ["WATERLOGGING_RISK"]


def test_normal_rain_has_no_alerts_and_totals_add_up():
    a = arh([3.0] * 30, [4.0] * 7)
    assert a["alerts"] == [] and a["past_7d_mm"] == 21.0 and a["past_30d_mm"] == 90.0 and a["forecast_7d_mm"] == 28.0


def test_missing_days_never_trigger_alerts():
    assert arh([None] * 30, [None] * 7)["alerts"] == []


def _install(daily):
    def fake(url, params=None, ttl=None, timeout=None):
        data = GEO if "geocoding" in url else {"daily": daily}
        return Fetched(data, url, "2026-11-01T10:00:00Z", False, 0)

    location.get_json = rh.get_json = fake


def test_tool_builds_card_and_alerts():
    n = 37
    _install({"time": [f"d{i}" for i in range(n)], "precipitation_sum": [0.0] * n})
    r = rh.rain_history("", {"place": "Nashik, Maharashtra"})
    assert r.ok and "DRY_SPELL_ALERT means:" in r.text and r.cards[0]["type"] == "rainfall_history"
    assert len(r.cards[0]["daily"]) == n and r.sources[0]["name"] == "Open-Meteo rain history"


def test_tool_incomplete_response_is_source_unavailable():
    _install({"time": ["d1"], "precipitation_sum": [0.0]})
    r = rh.rain_history("", {"place": "Nashik, Maharashtra"})
    assert not r.ok and "SOURCE UNAVAILABLE" in r.text
