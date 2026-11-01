"""Past 30 days of rain + next 7 days (Open-Meteo, free, no key) -> dry-spell / waterlogging alerts.

Uses the forecast endpoint's `past_days` (one call, no multi-day archive lag) instead of the archive endpoint.
"""
from __future__ import annotations

from app.decision.weather_rules import analyze_rain_history
from app.tools import SourceUnavailable, ToolResult, guarded
from app.tools.http import get_json
from app.tools.location import resolve_location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ALERT_MEANING = {  # plain-language meaning so the LLM cannot invert an alert
    "DRY_SPELL_ALERT": "very little rain in the last 7 days AND little forecast for the next 7 days",
    "IRRIGATION_URGENT": "almost no rain in the last 14 days, so crops need irrigation soon unless the next days bring enough rain",
    "WATERLOGGING_RISK": "very heavy rain in the last 7 days and more forecast in the next 3 days; check drainage",
}
PAST_DAYS = 30
FUTURE_DAYS = 7


def _summary_lines(where: str, a: dict, f) -> list[str]:
    stale = f" [STALE: {f.age_s}s old]" if f.stale else ""
    return [
        f"RAIN HISTORY for {where} - Open-Meteo, fetched {f.fetched_at}{stale}",
        f"Rain last 7 days: {a['past_7d_mm']} mm; last 14 days: {a['past_14d_mm']} mm; last 30 days: {a['past_30d_mm']} mm",
        f"Rain forecast next 7 days: {a['forecast_7d_mm']} mm (next 3 days: {a['forecast_3d_mm']} mm)",
        "RAIN ALERTS: " + (", ".join(a["alerts"]) or "none"),
        *[f"{alert} means: {ALERT_MEANING[alert]}" for alert in a["alerts"]],
    ]


def _card(where: str, a: dict, dates: list, rain: list) -> dict:
    return {
        "type": "rainfall_history",
        "title": f"Rainfall history - {where}",
        "past_7d_mm": a["past_7d_mm"],
        "past_30d_mm": a["past_30d_mm"],
        "forecast_7d_mm": a["forecast_7d_mm"],
        "daily": [{"date": d, "rain_mm": r} for d, r in zip(dates, rain)],
        "alerts": a["alerts"],
    }


def get_rain_history(profile: dict) -> ToolResult:
    loc = resolve_location(profile)
    params = {"latitude": loc["lat"], "longitude": loc["lon"], "daily": "precipitation_sum", "past_days": PAST_DAYS, "forecast_days": FUTURE_DAYS, "timezone": "auto"}
    f = get_json(FORECAST_URL, params)
    daily = (f.data.get("daily") if isinstance(f.data, dict) else None) or {}
    dates, rain = daily.get("time") or [], daily.get("precipitation_sum") or []
    if len(dates) < PAST_DAYS + 1 or len(rain) != len(dates):
        raise SourceUnavailable("rain history response was incomplete")
    analysis = analyze_rain_history(rain[:PAST_DAYS], rain[PAST_DAYS:])
    sources = [{"name": "Open-Meteo rain history", "url": f.url, "fetched_at": f.fetched_at, "stale": f.stale}]
    if loc.get("source"):
        sources.append(loc["source"])
    where = f"{loc['name']}, {loc['state']}".strip(", ")
    text = "\n".join(_summary_lines(where, analysis, f))
    return ToolResult(text, True, sources, [_card(where, analysis, dates, rain)], {"rain_history": analysis})


rain_history = guarded("rain_history")(lambda text, profile: get_rain_history(profile))
