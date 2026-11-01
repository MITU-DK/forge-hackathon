"""Crop suitability for the farm's place: which crops fit now, when to sow the farmer's crop, whether it is ready.

Live part: five complete years of daily weather at the farm from the Open-Meteo historical archive (ERA5 reanalysis), averaged
by day of year. Reference part: the FAO EcoCrop limits in data/ecocrop_subset.csv. The scoring is in app/decision/crop_fit.py.
A climate guide only: never presented as a forecast, a profit ranking or a replacement for the local agriculture officer.
"""
from __future__ import annotations

import datetime as dt
import logging

from app.decision import crop_fit as cf
from app.decision import soil_rules as sr
from app.tools import SourceUnavailable, ToolResult, guarded
from app.tools.http import get_json
from app.tools.location import resolve_location
from app.tools.mandi import crop_key

log = logging.getLogger("tool.crop_fit")
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CLIMATE_TTL_S = 30 * 86400  # a five-year average does not move within a month
CLIMATE_TIMEOUT_S = 15.0
YEARS = 5
MAX_SUITABLE_LISTED = 6
ECOCROP_NOTE = "FAO EcoCrop limits (gaez.fao.org/pages/ecocrop; table rebuilt from the UKCEH copy by scripts/build_ecocrop_subset.py)"

# Names the farmer or the mandi tool use -> the key in the EcoCrop table.
CROP_ALIASES = {
    "soyabean": "soybean", "paddy": "rice", "paddy(common)": "rice", "chana": "gram", "arhar": "tur", "bhindi": "okra",
    "lady finger": "okra", "ladyfinger": "okra", "green gram": "moong", "black gram": "urad", "green chilli": "chilli",
    "bengal gram": "gram", "pearl millet": "bajra", "sorghum": "jowar", "peas": "pea", "lentils": "lentil",
}
SOWING_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")
LOCATION_KEYS = ("name", "state", "district", "lat", "lon")


def table_key(crop: str) -> str:
    k = crop_key(crop or "")
    return CROP_ALIASES.get(k, k)


def parse_sowing_date(raw: str, today: dt.date) -> dt.date | None:
    for fmt in SOWING_DATE_FORMATS:
        try:
            d = dt.datetime.strptime((raw or "").strip(), fmt).date()
        except ValueError:
            continue
        return d if dt.timedelta(days=-366) <= d - today <= dt.timedelta(days=366) else None
    return None


def fetch_climate(loc: dict, today: dt.date):
    """-> (365-day climatology, Fetched, first year, last year). Raises SourceUnavailable; never fills a gap with a guess."""
    last_year, first_year = today.year - 1, today.year - YEARS
    params = {
        "latitude": loc["lat"], "longitude": loc["lon"], "start_date": f"{first_year}-01-01", "end_date": f"{last_year}-12-31",
        "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "auto",
    }
    f = get_json(ARCHIVE_URL, params, ttl=CLIMATE_TTL_S, timeout=CLIMATE_TIMEOUT_S)
    daily = (f.data or {}).get("daily") if isinstance(f.data, dict) else None
    if not daily or not daily.get("time"):
        raise SourceUnavailable("the historical weather response had no daily data")
    columns = zip(daily["time"], daily["temperature_2m_mean"], daily["temperature_2m_max"], daily["temperature_2m_min"], daily["precipitation_sum"])
    days = [{"date": t, "tmean": mean, "tmax": hi, "tmin": lo, "rain": rain} for t, mean, hi, lo, rain in columns]
    climatology = cf.climatology(days)
    if climatology is None:
        raise SourceUnavailable("the historical weather response had gaps")
    return climatology, f, first_year, last_year


def _detail(c: cf.Crop, s: dict) -> str:
    heat = f"; {s['hot_days']} days of the cycle have a typical daytime maximum above the crop's limit of {c.tmax:g} C" if s["hot_days"] else ""
    return (
        f"temperature fit {s['temp']} ({s['label']}), sow about {s['sow_on']}, usual cycle {c.gmin}-{c.gmax} days, "
        f"harvest roughly {s['harvest_from']} to {s['harvest_to']}; rain over the cycle {s['rain_mm']} mm vs the crop's optimum "
        f"{c.ropmn:g}-{c.ropmx:g} mm -> {s['rain_label']}{heat}"
    )


def _line(c: cf.Crop, s: dict) -> str:
    return f"- {c.common}: {_detail(c, s)}"


def _soil_exclusions(c: cf.Crop, s: dict) -> list[str]:
    fit = sr.crop_soil_fit(c, s["ph"], s["texture_group"])
    reasons = []
    if fit["ph"] and fit["ph"]["label"] == "OUTSIDE_TOLERANCE":
        low, high = fit["ph"]["absolute"]
        reasons.append(f"soil pH {s['ph']:g} is outside its tolerated {low:g}-{high:g}")
    if fit["texture"] == "NOT_TOLERATED":
        reasons.append(f"{s['texture_group']} soil texture is not tolerated")
    return reasons


def _apply_soil(loc: dict, suitable: list) -> tuple[str, set[str]]:
    """Crops whose temperature fit is good but whose soil (pH or texture, ISRIC SoilGrids vs EcoCrop tolerance) is ruled out.
    Best effort: if the soil service does not answer, nothing is removed and the line says the soil was not checked."""
    from app.tools import soil as soil_tool  # lazy: soil.py imports table_key from this module

    try:
        s, _ = soil_tool.fetch_summary(loc)
    except SourceUnavailable:
        return "SOIL not checked: the ISRIC SoilGrids service did not answer, so these crops are not filtered by soil pH or texture. Say that.", set()
    if s is None:
        return "SOIL not checked: no soil data for this place (built-up or water), so these crops are not filtered by soil pH or texture. Say that.", set()
    blocked, notes = set(), []
    for c, _ in suitable:
        reasons = _soil_exclusions(c, s)
        if reasons:
            blocked.add(c.key)
            notes.append(f"{c.common} ({'; '.join(reasons)})")
    head = f"SOIL used to filter the list: pH {s['ph']:g}, {s['texture_class']} ({s['texture_group']} texture), ISRIC SoilGrids 250 m model estimate."
    tail = " FIT THE CLIMATE BUT NOT THE SOIL, so left out of the list above: " + "; ".join(notes) + "." if notes else " No crop in the list above is ruled out by the soil."
    return head + tail, blocked


def _intro_line(where: str, loc: dict, f, first_year: int, last_year: int) -> str:
    return (
        f"CROP FIT for {where} ({loc['lat']}, {loc['lon']}). A CLIMATE-ONLY guide: {ECOCROP_NOTE} applied to the average weather of "
        f"{first_year}-{last_year} at this place (Open-Meteo historical weather, ERA5 reanalysis, fetched {f.fetched_at}). "
        "It is not a forecast and not a profit ranking; soil, variety, water supply, pests and prices are NOT included. "
        f"Fit scores run 0-100 (GOOD is {cf.GOOD} or more; the cut-off is a heuristic). Always tell the farmer to confirm with the local "
        "agriculture officer or KVK. When citing, say \"FAO EcoCrop limits and Open-Meteo historical weather\"; "
        "the date in a citation is never a sowing or harvest date."
    )


def _suitable_line(today: dt.date, suitable: list) -> str:
    until = (today + dt.timedelta(days=cf.SOW_LOOKAHEAD_DAYS)).isoformat()
    head = f"SUITABLE CROPS to sow between {today.isoformat()} and {until}: "
    if not suitable:
        return head + "none has a GOOD temperature fit in this period."
    return head + (
        f"{len(suitable)} crops have a GOOD temperature fit; {min(len(suitable), MAX_SUITABLE_LISTED)} are listed, not ranked by profit. "
        "Name at most 5 of them in the answer, one short line each."
    )


def _rain_fed_line(rain_fed: list) -> str:
    if not rain_fed:
        return "RAIN-FED (no irrigation assumed): none: the rain over a crop cycle starting now is below what these crops need, so they would need irrigation."
    return "RAIN-FED (no irrigation assumed): " + ", ".join(c.common for c, _ in rain_fed[:6])


def _window_line(c: cf.Crop, windows: list[dict]) -> str:
    if not windows:
        return f"BEST SOWING WINDOWS for {c.common}: no sowing date in the next 12 months reaches a POSSIBLE temperature fit at this place."
    parts = "; ".join(f"about {w['sow_on']} (temperature fit {w['temp']}, {w['rain_mm']} mm rain over the cycle, {w['rain_label']})" for w in windows)
    return f"BEST SOWING WINDOWS for {c.common} over the next 12 months: " + parts


def _harvest_line(c: cf.Crop, sowing: dt.date, h: dict) -> str:
    stage = f"STAGE: {h['status']}"
    stage += f" ({h['days_to_earliest']} days to the earliest harvest)." if h["status"] == "BEFORE_EARLIEST_HARVEST" else "."
    return (
        f"SOWN {sowing.isoformat()}: {h['days_since_sowing']} days ago. The usual cycle of {c.common} is {c.gmin}-{c.gmax} days "
        f"(varieties differ), so harvest is expected from {h['earliest']} to {h['latest']}. {stage}"
    )


def _span(c, sow_on: str, label: str, kind: str) -> dict:
    day = dt.date.fromisoformat(sow_on)
    return {"kind": kind, "label": label, "sow_on": sow_on, "harvest_from": (day + dt.timedelta(days=c.gmin)).isoformat(), "harvest_to": (day + dt.timedelta(days=c.gmax)).isoformat()}


def _calendar_card(c, windows: list[dict], now: dict, harvest: dict | None, sowing: dt.date | None, today: dt.date) -> dict:
    """Timeline data for the farmer's own crop: the best sowing windows with their harvest ranges, and their own sowing if known.
    Dates only (no yields); the harvest range is the EcoCrop cycle length, so it is a range, not a promise."""
    spans = [_span(c, w["sow_on"], w["label"], "window") for w in windows]
    if now["temp"] >= cf.POSSIBLE and all(sp["sow_on"] != now["sow_on"] for sp in spans):
        spans.insert(0, _span(c, now["sow_on"], now["label"], "now"))
    card = {"type": "crop_calendar", "title": f"{c.common} - sowing and harvest calendar", "crop": c.common, "today": today.isoformat(), "spans": spans,
            "cycle_days": [c.gmin, c.gmax], "note": "Climate-based guide from the FAO EcoCrop cycle length and 5-year average weather. Varieties differ; confirm with your agriculture officer."}
    if sowing is not None and harvest is not None:
        card["sown"] = {"sowing": sowing.isoformat(), "harvest_from": harvest["earliest"], "harvest_to": harvest["latest"], "status": harvest["status"], "days_since_sowing": harvest["days_since_sowing"]}
    return card


def _your_crop(profile: dict, crops: dict, clim, today: dt.date, lines: list[str], facts: dict, card: dict) -> None:
    """Adds the lines, facts and card fields about the farmer's own crop (fit now, sowing windows, harvest status)."""
    c = crops.get(table_key(profile.get("crop", "")))
    if c is None:
        lines.append(
            f"YOUR CROP {profile['crop']}: no EcoCrop limits are loaded for it (the table covers annual field crops; perennials such as "
            "banana, mango or sugarcane are not scored). Say so; give no fit, sowing or harvest dates for it."
        )
        return
    now = cf.best_sowing_now(clim, c, today)
    lines.append(f"YOUR CROP {c.common}: {_detail(c, now)}")
    windows = cf.sowing_windows(clim, c, today)
    lines.append(_window_line(c, windows))
    facts.update({"crop": c.key, "fit_now": now, "windows": windows})
    card["your_crop"] = {"crop": c.common, "fit": now["temp"], "label": now["label"], "sow_on": now["sow_on"], "windows": [w["sow_on"] for w in windows]}
    facts["calendar"] = _calendar_card(c, windows, now, None, None, today)
    raw = (profile.get("sowing_date") or "").strip()
    if not raw:
        return
    sowing = parse_sowing_date(raw, today)
    if sowing is None:
        lines.append(f"SOWING DATE '{raw}' could not be read as a date within a year of today; ask the farmer for it as day/month/year.")
        return
    facts["harvest"] = cf.harvest_status(c, sowing, today)
    facts["calendar"] = _calendar_card(c, windows, now, facts["harvest"], sowing, today)
    lines.append(_harvest_line(c, sowing, facts["harvest"]))


def _card(where: str, suitable: list) -> dict:
    return {
        "type": "crop_fit",
        "title": f"Crops that fit the climate - {where}",
        "note": "Climate only. Confirm with your agriculture officer.",
        "crops": [
            {"crop": c.common, "fit": s["temp"], "label": s["label"], "sow_on": s["sow_on"], "harvest_from": s["harvest_from"],
             "harvest_to": s["harvest_to"], "rain_mm": s["rain_mm"], "rain_label": s["rain_label"]}
            for c, s in suitable[:MAX_SUITABLE_LISTED]
        ],
    }


def get_crop_fit(profile: dict, text: str = "") -> ToolResult:
    loc = resolve_location(profile)
    today = dt.date.today()
    clim, f, first_year, last_year = fetch_climate(loc, today)
    crops = cf.load_crops()
    where = f"{loc['name']}, {loc['state']}".strip(", ")
    ranked = cf.rank_for_sowing(clim, today, crops)
    suitable = [(c, s) for c, s in ranked if s["temp"] >= cf.GOOD]
    soil_note, soil_blocked = _apply_soil(loc, suitable)
    suitable = [(c, s) for c, s in suitable if c.key not in soil_blocked]
    rain_fed = [(c, s) for c, s in cf.rain_fed_only(ranked) if c.key not in soil_blocked]
    unsuitable = [c.common for c, s in ranked if s["temp"] == 0]

    lines = [_intro_line(where, loc, f, first_year, last_year), _suitable_line(today, suitable)]
    lines += [_line(c, s) for c, s in suitable[:MAX_SUITABLE_LISTED]]
    lines += [_rain_fed_line(rain_fed), soil_note]
    if unsuitable:
        lines.append("NOT SUITABLE to sow in this period (temperature fit 0): " + ", ".join(unsuitable[:8]))

    card = _card(where, suitable)
    facts: dict = {"location": {k: loc[k] for k in LOCATION_KEYS}, "suitable": [c.key for c, _ in suitable]}
    if profile.get("crop"):
        _your_crop(profile, crops, clim, today, lines, facts, card)
    sources = [{"name": "Open-Meteo historical weather (5-year average)", "url": f.url, "fetched_at": f.fetched_at, "stale": f.stale}]
    if loc.get("source"):
        sources.append(loc["source"])
    cards = [card] + ([facts.pop("calendar")] if "calendar" in facts else [])
    return ToolResult("\n".join(lines), True, sources, cards, facts)


crop_fit = guarded("crop_fit")(lambda text, profile: get_crop_fit(profile, text))
