"""Deterministic weather risk rules. LLM never decides these; code does.

Every threshold below is tagged with its basis:
  IMD        = India Meteorological Department glossary definition (verified against the glossary text).
  HEURISTIC  = practical guidance WITHOUT a verified Indian primary source; justification given inline.
"""
from __future__ import annotations

IMD_HEAVY_RAIN_MM = 64.5  # IMD glossary: "Heavy rain" = 64.5 to 124.4 mm in a day (lower bound used)
IMD_RAINY_DAY_MM = 2.5  # IMD glossary: "Rainy Day" = 2.5 mm or more in a day
IMD_HEAT_C = 40.0  # IMD: a heat wave is declared only when Tmax reaches >= 40 C in plains (it ALSO needs a +4..+6 C departure from normal, which we do not check), so this is a heat-STRESS flag, not a heat-wave declaration
IMD_COLD_C = 4.0  # HEURISTIC low-temperature stress flag. NOT an IMD cold-wave criterion (IMD uses wind-chill minimum temperature and departure from normal); crop-specific limits come from crop_rules
SPRAY_MAX_WIND_KMH = 15.0  # HEURISTIC: no Indian limit found; sits just under the ~16 km/h upper end of the 3-16 km/h window in Canadian guidance (Saskatchewan/OMAFRA). Product label always governs
SPRAY_MAX_RAIN_PROB = 40  # HEURISTIC: practical wash-off risk cut-off on the daily max rain probability; conservative on purpose (a skipped spray day costs less than a washed-off one)
IRRIGATION_SKIP_RAIN_MM = 10.0  # HEURISTIC: FAO gives effective-rainfall formulas but no fixed skip limit; 10 mm in 48 h is roughly 2 days of typical crop water use (ET0 about 4-5 mm/day)


MIN_BALANCE_DAYS = 5  # a balance over fewer real days would be a guess


def water_balance(days: list[dict]) -> dict | None:
    """Forecast rain minus reference evapotranspiration (ET0, FAO-56 grass reference, from Open-Meteo) over the days that have both.

    ET0 is NOT crop-specific water use (crop coefficients vary by crop and stage), so this says whether the rain
    forecast covers the reference demand, and never how much water a particular crop needs. No threshold: the sign decides.
    """
    both = [d for d in days if d.get("rain_mm") is not None and d.get("et0_mm") is not None]
    if len(both) < MIN_BALANCE_DAYS:
        return None
    rain, et0 = round(sum(d["rain_mm"] for d in both), 1), round(sum(d["et0_mm"] for d in both), 1)
    gap = round(rain - et0, 1)
    return {"days": len(both), "rain_mm": rain, "et0_mm": et0, "balance_mm": gap, "label": "RAIN_COVERS_DEMAND" if gap >= 0 else "RAIN_SHORT_OF_DEMAND"}


DAILY_FIELDS = {  # day field -> Open-Meteo daily variable
    "tmax": "temperature_2m_max",
    "tmin": "temperature_2m_min",
    "rain_mm": "precipitation_sum",
    "rain_prob": "precipitation_probability_max",
    "wind_kmh": "wind_speed_10m_max",
    "et0_mm": "et0_fao_evapotranspiration",
}


def _val(lst, i):
    try:
        v = lst[i]
        return None if v is None else float(v)
    except (IndexError, TypeError, ValueError):
        return None


def days_from_daily(daily: dict) -> list[dict]:
    return [
        {"i": i, "date": date, **{field: _val(daily.get(var, []), i) for field, var in DAILY_FIELDS.items()}}
        for i, date in enumerate(daily.get("time") or [])
    ]


def _flag(name: str, day: dict, value_key: str, unit: str, rule: str) -> dict:
    return {"flag": name, "day": day["date"], "day_index": day["i"], "value": day[value_key], "unit": unit, "rule": rule}


def _risk_flags(days: list[dict]) -> list[dict]:
    flags = []
    for d in days:
        if d["rain_mm"] is not None and d["rain_mm"] >= IMD_HEAVY_RAIN_MM:
            flags.append(_flag("HEAVY_RAIN", d, "rain_mm", "mm", f">= {IMD_HEAVY_RAIN_MM} mm/24h (IMD heavy rain)"))
        if d["tmax"] is not None and d["tmax"] >= IMD_HEAT_C:
            flags.append(_flag("HEAT_STRESS", d, "tmax", "C", f"Tmax >= {IMD_HEAT_C} C (IMD minimum temperature for a plains heat wave; departure from normal not checked)"))
        if d["tmin"] is not None and d["tmin"] <= IMD_COLD_C:
            flags.append(_flag("COLD_STRESS", d, "tmin", "C", f"Tmin <= {IMD_COLD_C} C (heuristic low-temperature stress threshold)"))
    return flags


def _is_spray_day(d: dict) -> bool:
    return (
        d["wind_kmh"] is not None
        and d["rain_prob"] is not None
        and d["wind_kmh"] <= SPRAY_MAX_WIND_KMH
        and d["rain_prob"] <= SPRAY_MAX_RAIN_PROB
        and (d["rain_mm"] or 0) < IMD_RAINY_DAY_MM
    )


def analyze(days: list[dict]) -> dict:
    next48 = sum((d["rain_mm"] or 0) for d in days[:2])
    rainy = [d["date"] for d in days if (d["rain_mm"] or 0) >= IMD_RAINY_DAY_MM]
    return {
        "flags": _risk_flags(days),
        "spray_ok_days": [d["date"] for d in days if _is_spray_day(d)],
        "rain_next_48h_mm": round(next48, 1),
        "irrigation": "SKIP_IRRIGATION" if next48 >= IRRIGATION_SKIP_RAIN_MM else "IRRIGATE_IF_SOIL_DRY",
        "rainy_days": rainy,
        "dry_spell": len(days) >= 5 and not rainy,
        "water_balance": water_balance(days),
    }


# Rain-history rules (HEURISTIC: no verified source). 'Dry' = under one IMD rainy day's worth (2.5 mm) in 7 days.
DRY_PAST7_MM = 2.5
DRY_NEXT7_MM = 5.0
IRRIGATION_URGENT_PAST14_MM = 5.0
WATERLOG_PAST7_MM = 100.0
WATERLOG_NEXT3_MM = 30.0


def analyze_rain_history(past_mm: list[float | None], forecast_mm: list[float | None]) -> dict:
    """past_mm: oldest -> yesterday (>= 14 days to judge dryness). forecast_mm: today -> +6 days.
    Missing days count as 0 mm only for totals; alerts need enough real days, never guessed."""

    def total(xs):
        return round(sum(x for x in xs if x is not None), 1)

    def real_days(xs):
        return sum(1 for x in xs if x is not None)

    p7, p14, p30 = total(past_mm[-7:]), total(past_mm[-14:]), total(past_mm)
    f7, f3 = total(forecast_mm[:7]), total(forecast_mm[:3])
    alerts = []
    if real_days(past_mm[-7:]) >= 5 and real_days(forecast_mm[:7]) >= 5 and p7 < DRY_PAST7_MM and f7 < DRY_NEXT7_MM:
        alerts.append("DRY_SPELL_ALERT")
    if real_days(past_mm[-14:]) >= 10 and p14 < IRRIGATION_URGENT_PAST14_MM:
        alerts.append("IRRIGATION_URGENT")
    if p7 > WATERLOG_PAST7_MM and f3 > WATERLOG_NEXT3_MM:
        alerts.append("WATERLOGGING_RISK")
    return {"past_7d_mm": p7, "past_14d_mm": p14, "past_30d_mm": p30, "forecast_7d_mm": f7, "forecast_3d_mm": f3, "alerts": alerts}


# ---- hours and days: when exactly to spray, which days to skip irrigation
SPRAY_HOURS = range(6, 19)  # HEURISTIC: daylight working hours, 06:00-18:59 local time
SPRAY_MAX_HOURLY_RAIN_MM = 0.1  # HEURISTIC: a dry hour; any measurable rain washes a fresh spray off
SPRAY_MIN_WINDOW_H = 2  # a shorter gap is not worth the trip to the field
SPRAY_DAYS_AHEAD = 3  # hourly rain probability is not reliable beyond a few days


def spray_windows(hourly: dict, days_ahead: int = SPRAY_DAYS_AHEAD, now_local: str = "") -> list[dict]:
    """Best uninterrupted spray window per day from hourly wind, rain chance and rain. Same wind/chance limits as the daily rule.
    A missing hour breaks a window (never assumed fine). Hours before `now_local` (ISO, the farm's local time) are already gone and are skipped. Returns [] when the hourly data lacks any of the three variables."""
    times, wind, prob, rain = (hourly.get(k) or [] for k in ("time", "wind_speed_10m", "precipitation_probability", "precipitation"))
    if not (times and len(times) == len(wind) == len(prob) == len(rain)):
        return []
    by_day: dict[str, dict[int, bool]] = {}
    for t, w, p, r in zip(times, wind, prob, rain):
        day, hour = t[:10], int(t[11:13])
        if hour in SPRAY_HOURS and t[:13] >= now_local[:13]:
            ok = w is not None and p is not None and r is not None and w <= SPRAY_MAX_WIND_KMH and p <= SPRAY_MAX_RAIN_PROB and r < SPRAY_MAX_HOURLY_RAIN_MM
            by_day.setdefault(day, {})[hour] = ok
    out = []
    for day in sorted(by_day)[:days_ahead]:
        best, run_start = None, None
        for h in [*SPRAY_HOURS, SPRAY_HOURS.stop]:
            if h in SPRAY_HOURS and by_day[day].get(h):
                run_start = h if run_start is None else run_start
            elif run_start is not None:
                if h - run_start >= SPRAY_MIN_WINDOW_H and (best is None or h - run_start > best[1] - best[0]):
                    best = (run_start, h)
                run_start = None
        if best:
            out.append({"date": day, "start": f"{best[0]:02d}:00", "end": f"{best[1]:02d}:00", "hours": best[1] - best[0]})
    return out


def irrigation_plan(days: list[dict]) -> list[dict]:
    """Per day: SKIP when this day or the day before had at least IRRIGATION_SKIP_RAIN_MM (the soil is wet from it), otherwise CHECK_SOIL
    (irrigate only if the soil is dry). Rain and ET0 are shown beside it. No crop water need is claimed (ET0 is a grass reference)."""
    out = []
    for i, d in enumerate(days):
        rain, prev = d.get("rain_mm"), days[i - 1].get("rain_mm") if i else None
        wet = (rain or 0) >= IRRIGATION_SKIP_RAIN_MM or (prev or 0) >= IRRIGATION_SKIP_RAIN_MM
        out.append({"date": d["date"], "rain_mm": rain, "et0_mm": d.get("et0_mm"), "action": "SKIP" if wet else "CHECK_SOIL"})
    return out
