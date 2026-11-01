"""Live weather + soil moisture for the farm location (Open-Meteo, free, no key)."""
from __future__ import annotations

import datetime as dt

from app.decision.weather_rules import analyze, days_from_daily, irrigation_plan, spray_windows
from app.tools import SourceUnavailable, ToolResult, guarded
from app.tools.http import get_json
from app.tools.location import resolve_location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,et0_fao_evapotranspiration"


SOIL_HOURLY = ("soil_moisture_3_to_9cm", "soil_moisture_9_to_27cm", "soil_moisture_27_to_81cm", "soil_temperature_6cm", "soil_temperature_18cm")
SPRAY_HOURLY = ("wind_speed_10m", "precipitation_probability", "precipitation")  # for the best spray hours
CURRENT = "temperature_2m,relative_humidity_2m,wind_speed_10m"
FORECAST_DAYS = 7
SURFACE_MOISTURE_NOTE = (
    " (model estimate, next-24h average): {value} m3/m3 (no verified dry/wet threshold exists for it: "
    "state the number only, do not call it low, high, dry or wet)"
)
DEEP_MOISTURE_NOTE = (
    " m3/m3. The surface layer dries first and the deeper layers hold the water roots use, so report all layers, "
    "never only the surface one, and do not call the soil dry or wet."
)


def _day_mean(hourly: dict, key: str, digits: int = 3) -> float | None:
    vals = [v for v in (hourly.get(key) or [])[:24] if v is not None]
    return round(sum(vals) / len(vals), digits) if vals else None


def _soil_moisture(hourly: dict) -> float | None:
    return _day_mean(hourly, "soil_moisture_3_to_9cm")


def soil_profile(hourly: dict) -> dict:
    """Next-24h means per depth. The surface dries first while the deeper layers hold the water roots use, so the surface figure
    alone can mislead (3-9 cm 0.17 but 9-27 cm 0.30 on 31 Oct 2026, Nashik). Only layers the model returned are included."""
    means = {
        "moisture_3_9cm": _day_mean(hourly, "soil_moisture_3_to_9cm"),
        "moisture_9_27cm": _day_mean(hourly, "soil_moisture_9_to_27cm"),
        "moisture_27_81cm": _day_mean(hourly, "soil_moisture_27_to_81cm"),
        "temp_6cm_c": _day_mean(hourly, "soil_temperature_6cm", 1),
        "temp_18cm_c": _day_mean(hourly, "soil_temperature_18cm", 1),
    }
    return {k: v for k, v in means.items() if v is not None}


def _soil_lines(soil: float | None, depths: dict) -> list[str]:
    lines = []
    if soil is not None:
        lines.append("SOIL MOISTURE 3-9 cm" + SURFACE_MOISTURE_NOTE.format(value=soil))
    deeper = [f"{label} {depths[key]}" for key, label in (("moisture_9_27cm", "9-27 cm"), ("moisture_27_81cm", "27-81 cm"))
              if key in depths]
    if deeper:
        lines.append("SOIL MOISTURE DEEPER (root zone, model estimate, next-24h average): " + ", ".join(deeper) + DEEP_MOISTURE_NOTE)
    temps = [f"{label} {depths[key]} C" for key, label in (("temp_6cm_c", "6 cm"), ("temp_18cm_c", "18 cm")) if key in depths]
    if temps:
        lines.append("SOIL TEMPERATURE (model estimate, next-24h average): " + ", ".join(temps))
    return lines


def _risk_lines(a: dict) -> list[str]:
    if not a["flags"]:
        return ["RISK FLAGS: none in the next 7 days"]
    return [f"RISK FLAG {fl['flag']} on {fl['day']}: {fl['value']} {fl['unit']} ({fl['rule']})" for fl in a["flags"]]


def _decision_lines(a: dict) -> list[str]:
    lines = [
        "SPRAY-SAFE DAYS (wind <= 15 km/h, rain chance <= 40 %, rain < 2.5 mm): " + (", ".join(a["spray_ok_days"]) or "none"),
        f"IRRIGATION: rain expected in next 48 h = {a['rain_next_48h_mm']} mm -> {a['irrigation']} "
        "(applies to a growing crop; it does not apply to a crop that is mature or being harvested, and the crop stage is not known here)",
    ]
    wb = a["water_balance"]
    if wb:
        lines.append(
            f"WATER BALANCE next {wb['days']} days: forecast rain {wb['rain_mm']} mm vs reference crop water demand "
            f"(ET0, grass reference, not your crop's exact need) {wb['et0_mm']} mm -> {wb['label']} (rain minus demand = {wb['balance_mm']} mm)"
        )
    if a["dry_spell"]:
        lines.append("DRY SPELL: no rainy day (>= 2.5 mm) in the next 7 days")
    return lines


def _local_now(data: dict) -> str:
    """The farm's local time as ISO text (the forecast says its UTC offset). Empty when unknown: then no hour is skipped."""
    try:
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(data["utc_offset_seconds"]))).strftime("%Y-%m-%dT%H:00")
    except (KeyError, TypeError, ValueError):
        return ""


def _hour_lines(windows: list[dict], plan: list[dict]) -> list[str]:
    lines = []
    if windows:
        lines.append("BEST SPRAY HOURS (computed in code from hourly wind, rain chance and rain; the product label always governs): "
                     + "; ".join(f"{w['date']} {w['start']}-{w['end']} ({w['hours']} h)" for w in windows))
    lines.append("IRRIGATION PLAN (rule: SKIP on a day when it or the day before had 10 mm or more of rain; otherwise CHECK_SOIL = irrigate only if the soil is dry; ET0 is a grass reference, not the crop's exact need): "
                 + "; ".join(f"{d['date']} rain {d['rain_mm']} mm, ET0 {d['et0_mm']} mm -> {d['action']}" for d in plan))
    return lines


def _text(loc: dict, where: str, f, days: list[dict], a: dict, cur: dict, soil: float | None, depths: dict, extra: list[str] = ()) -> str:
    stale = f" [STALE: {f.age_s}s old]" if f.stale else ""
    lines = [f"WEATHER for {where} ({loc['lat']}, {loc['lon']}) - Open-Meteo, fetched {f.fetched_at}{stale}"]
    if cur:
        lines.append(f"Now: {cur.get('temperature_2m')} C, humidity {cur.get('relative_humidity_2m')} %, wind {cur.get('wind_speed_10m')} km/h")
    lines += [
        f"{d['date']}: max {d['tmax']} C, min {d['tmin']} C, rain {d['rain_mm']} mm (chance {d['rain_prob']} %), wind up to {d['wind_kmh']} km/h"
        for d in days
    ]
    return "\n".join(lines + _soil_lines(soil, depths) + _risk_lines(a) + _decision_lines(a) + list(extra))


def _card(where: str, days: list[dict], a: dict, soil: float | None, depths: dict, windows: list[dict] = (), plan: list[dict] = ()) -> dict:
    return {
        "spray_windows": list(windows),
        "irrigation_plan": list(plan),
        "type": "forecast",
        "title": f"7-day forecast - {where}",
        "days": [{k: d[k] for k in ("date", "tmax", "tmin", "rain_mm", "rain_prob")} for d in days],
        "flags": [{"flag": x["flag"], "day": x["day"]} for x in a["flags"]],
        "soil_moisture": soil,
        "soil_profile": depths,
    }


def get_weather(profile: dict) -> ToolResult:
    loc = resolve_location(profile)
    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "daily": DAILY,
        "hourly": ",".join(SOIL_HOURLY + SPRAY_HOURLY),
        "current": CURRENT,
        "forecast_days": FORECAST_DAYS,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    f = get_json(FORECAST_URL, params)
    data = f.data if isinstance(f.data, dict) else {}
    days = days_from_daily(data.get("daily") or {})
    if not days:
        raise SourceUnavailable("forecast response had no daily data")
    a = analyze(days)
    hourly = data.get("hourly") or {}
    soil, depths = _soil_moisture(hourly), soil_profile(hourly)
    sources = [{"name": "Open-Meteo forecast", "url": f.url, "fetched_at": f.fetched_at, "stale": f.stale}]
    if loc.get("source"):
        sources.append(loc["source"])
    where = f"{loc['name']}, {loc['state']}".strip(", ")
    windows, plan = spray_windows(hourly, now_local=_local_now(data)), irrigation_plan(days)
    text = _text(loc, where, f, days, a, data.get("current") or {}, soil, depths, _hour_lines(windows, plan))
    facts = {
        "location": {k: loc[k] for k in ("name", "state", "district", "lat", "lon")},
        "analysis": a,
        "soil_moisture": soil,
        "soil_profile": depths,
        "days": days,
        "spray_windows": windows,
        "irrigation_plan": plan,
    }
    return ToolResult(text, True, sources, [_card(where, days, a, soil, depths, windows, plan)], facts)


weather = guarded("weather")(lambda text, profile: get_weather(profile))
