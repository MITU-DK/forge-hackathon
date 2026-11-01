"""Crop suitability from climate: which crops fit this place now, when to sow one, when it is ready. Deterministic code.

Method: the annual-crop scoring of the UKCEH EcoCrop model (https://github.com/OpenCLIM/ecocrop, Open Government Licence)
applied to the FAO EcoCrop limits in data/ecocrop_subset.csv (see scripts/build_ecocrop_subset.py), on the average climate of
the last five complete years at the farm (Open-Meteo archive, ERA5 reanalysis). It is a CLIMATE guide only: soil, variety,
water supply, pests, local practice and prices are not in it, and it is not a forecast.

Per sowing day and per cycle length G (GMIN, GMIN+10, ... up to GMAX):
* a day's temperature suitability D is 1 between the optimal temperatures, falls linearly to 0 at the absolute limits;
* the first G whose summed D reaches GMIN gives the temperature score 100 * (1 - (G - GMIN) / (GMAX - GMIN));
* a daily minimum below the killing temperature (when the database has one) sets the score to 0;
* days hotter than the crop's absolute maximum are COUNTED and reported, not subtracted. The repository subtracts them, but its
  daily maxima are compared with a limit meant for mean temperatures: in India's dry winter that wipes out crops that are grown
  there every year (wheat around Nashik scored 1 of 100 for November sowing), so here the score is the mean-temperature one;
* the rain score compares the average rain over that cycle with the optimal and absolute rainfall limits.
Labels (GOOD/POSSIBLE/MARGINAL) and the 30-day sowing look-ahead are HEURISTIC: no Indian source sets them.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "data" / "ecocrop_subset.csv"
YEAR = 365  # climatology is by day of year; 29 February is folded into 28 February
GOOD, POSSIBLE = 70, 40  # HEURISTIC cut-offs on the 0-100 temperature score
SOW_LOOKAHEAD_DAYS, SOW_STEP_DAYS = 30, 5  # HEURISTIC: "sow now" means within the next month
CYCLE_STEP = 10  # the EcoCrop model tries cycle lengths at 10-day steps
MIN_WINDOW_GAP_DAYS = 45  # sowing windows closer than this are the same season


@dataclass(frozen=True)
class Crop:
    key: str
    scientific: str
    common: str
    topmn: float
    topmx: float
    tmin: float
    tmax: float
    ropmn: float
    ropmx: float
    rmin: float
    rmax: float
    ph_opt: tuple[float, float] | None
    ph_abs: tuple[float, float] | None
    gmin: int
    gmax: int
    ktmp: float | None
    texture: str
    texture_abs: str
    drainage: str


def _f(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


NUMERIC_COLUMNS = ("TOPMN", "TOPMX", "TMIN", "TMAX", "ROPMN", "ROPMX", "RMIN", "RMAX", "GMIN", "GMAX")


def _range(row: dict, low: str, high: str) -> tuple[float, float] | None:
    lo, hi = _f(row[low]), _f(row[high])
    return (lo, hi) if lo is not None and hi is not None else None


def _crop_from_row(row: dict) -> Crop | None:
    """None when the row has no usable limits: a crop that cannot be scored is left out rather than guessed."""
    n = {k: _f(row[k]) for k in NUMERIC_COLUMNS}
    if any(v is None for v in n.values()) or not n["GMIN"]:
        return None
    return Crop(
        key=row["crop"], scientific=row["ScientificName"], common=row["crop"].title(),
        topmn=n["TOPMN"], topmx=n["TOPMX"], tmin=n["TMIN"], tmax=n["TMAX"],
        ropmn=n["ROPMN"], ropmx=n["ROPMX"], rmin=n["RMIN"], rmax=n["RMAX"],
        ph_opt=_range(row, "PHOPMN", "PHOPMX"), ph_abs=_range(row, "PHMIN", "PHMAX"),
        gmin=int(n["GMIN"]), gmax=int(max(n["GMAX"], n["GMIN"])), ktmp=_f(row["KTMP"]),
        texture=row["TEXT"], texture_abs=row["TEXTR"], drainage=row["DRA"],
    )


@lru_cache(maxsize=1)
def load_crops() -> dict[str, Crop]:
    with DATA.open(encoding="utf-8", newline="") as fh:
        crops = (_crop_from_row(row) for row in csv.DictReader(fh))
        return {c.key: c for c in crops if c}


def day_index(d: dt.date) -> int:
    """0..364; a leap day counts as 28 February."""
    y = d.timetuple().tm_yday
    if d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0) and d.month > 2:
        y -= 1
    return min(y, YEAR) - 1


def climatology(days: list[dict]) -> list[dict] | None:
    """days: [{date: iso, tmean, tmin, tmax, rain}] over several years -> 365 day-of-year averages. None if any day of the year is empty."""
    acc = [[0.0, 0.0, 0.0, 0.0, 0] for _ in range(YEAR)]
    for d in days:
        vals = (d.get("tmean"), d.get("tmin"), d.get("tmax"), d.get("rain"))
        if any(v is None for v in vals):
            continue
        a = acc[day_index(dt.date.fromisoformat(d["date"]))]
        for i, v in enumerate(vals):
            a[i] += v
        a[4] += 1
    if any(a[4] == 0 for a in acc):
        return None
    return [{"tmean": a[0] / a[4], "tmin": a[1] / a[4], "tmax": a[2] / a[4], "rain": a[3] / a[4]} for a in acc]


def _suit(t: float, c: Crop) -> float:
    if c.topmn <= t <= c.topmx:
        return 1.0
    if c.tmin < t < c.topmn:
        return (t - c.tmin) / (c.topmn - c.tmin)
    if c.topmx < t < c.tmax:
        return (c.tmax - t) / (c.tmax - c.topmx)
    return 0.0


def _rain_score(mm: float, c: Crop) -> float:
    if c.ropmn <= mm <= c.ropmx:
        return 100.0
    if c.rmin < mm < c.ropmn:
        return 100.0 * (mm - c.rmin) / (c.ropmn - c.rmin)
    if c.ropmx < mm < c.rmax:
        return 100.0 * (c.rmax - mm) / (c.rmax - c.ropmx)
    return 0.0


def cycle_lengths(c: Crop) -> list[int]:
    steps = list(range(c.gmin, c.gmax + 1, CYCLE_STEP))
    return steps + ([c.gmax] if steps[-1] != c.gmax else [])


def score_start(clim: list[dict], start: int, c: Crop) -> dict:
    """Temperature score for sowing on day-of-year `start`, with the rain over that cycle and the heat days in it."""
    need = c.gmin
    cum, frost, hot, optimal = 0.0, False, 0, 0
    lengths = set(cycle_lengths(c))
    g, temp = None, 0.0
    for i in range(cycle_lengths(c)[-1]):
        day = clim[(start + i) % YEAR]
        cum += _suit(day["tmean"], c)
        frost = frost or (c.ktmp is not None and day["tmin"] < c.ktmp)
        hot += day["tmax"] > c.tmax
        optimal += c.topmn <= day["tmean"] <= c.topmx
        n = i + 1
        if n in lengths and cum >= need:  # the first cycle length that collects enough suitable days is the one scored
            span = c.gmax - c.gmin
            temp = 0.0 if frost else (100.0 * (1 - (n - c.gmin) / span) if span else 100.0)
            g = n
            break
    used = g or c.gmin
    rain_mm = sum(clim[(start + i) % YEAR]["rain"] for i in range(used))
    return {"temp": round(temp), "rain": round(_rain_score(rain_mm, c)), "rain_mm": round(rain_mm), "cycle_days": used, "frost": bool(frost),
            "hot_days": int(hot) if g else 0, "optimal_share": round(100 * optimal / max(g or used, 1)) if g else 0}


def temp_label(score: float) -> str:
    if score >= GOOD:
        return "GOOD"
    if score >= POSSIBLE:
        return "POSSIBLE"
    return "MARGINAL" if score > 0 else "NOT_SUITABLE"


def rain_label(rain_mm: float, c: Crop) -> str:
    if rain_mm < c.ropmn:
        return "NEEDS_IRRIGATION" if rain_mm >= c.rmin else "NEEDS_IRRIGATION_RAIN_FAR_BELOW_NEED"
    if rain_mm > c.ropmx:
        return "WETTER_THAN_OPTIMUM" if rain_mm <= c.rmax else "FAR_WETTER_THAN_CROP_TOLERATES"
    return "RAIN_COVERS_NEED"


def best_sowing_now(clim: list[dict], c: Crop, today: dt.date) -> dict:
    best = None
    for k in range(0, SOW_LOOKAHEAD_DAYS + 1, SOW_STEP_DAYS):
        day = today + dt.timedelta(days=k)
        s = score_start(clim, day_index(day), c)
        key = (s["temp"], s["optimal_share"], s["rain"])
        if best is None or key > best[0]:
            best = (key, day, s)
    day, s = best[1], best[2]
    return {**s, "sow_on": day.isoformat(), "label": temp_label(s["temp"]), "rain_label": rain_label(s["rain_mm"], c),
            "harvest_from": (day + dt.timedelta(days=c.gmin)).isoformat(), "harvest_to": (day + dt.timedelta(days=c.gmax)).isoformat()}


def rank_for_sowing(clim: list[dict], today: dt.date, crops: dict[str, Crop] | None = None) -> list[tuple[Crop, dict]]:
    crops = crops or load_crops()
    scored = [(c, best_sowing_now(clim, c, today)) for c in crops.values()]
    return sorted(scored, key=lambda cs: (-cs[1]["temp"], -cs[1]["optimal_share"], -cs[1]["rain"], cs[0].key))


def rain_fed_only(ranked: list[tuple[Crop, dict]]) -> list[tuple[Crop, dict]]:
    """Crops whose temperature fit is GOOD and whose cycle rain reaches the crop's own optimum, i.e. no irrigation assumed."""
    return [(c, s) for c, s in ranked if s["temp"] >= GOOD and s["rain_label"] in ("RAIN_COVERS_NEED", "WETTER_THAN_OPTIMUM")]


def sowing_windows(clim: list[dict], c: Crop, today: dt.date, horizon_days: int = 365, step: int = 10, top: int = 2) -> list[dict]:
    """Best non-overlapping stretches (start dates) over the next year, judged by the temperature score."""
    candidates = []
    for k in range(0, horizon_days, step):
        day = today + dt.timedelta(days=k)
        candidates.append((day, score_start(clim, day_index(day), c)))
    out, taken = [], set()
    for day, s in sorted(candidates, key=lambda p: (-p[1]["temp"], -p[1]["optimal_share"], -p[1]["rain"], p[0])):
        if s["temp"] < POSSIBLE:
            break
        if any(abs((day - t).days) < MIN_WINDOW_GAP_DAYS for t in taken):  # one window per season
            continue
        taken.add(day)
        out.append({"sow_on": day.isoformat(), "temp": s["temp"], "rain_mm": s["rain_mm"], "optimal_share": s["optimal_share"], "label": temp_label(s["temp"]), "rain_label": rain_label(s["rain_mm"], c)})
        if len(out) == top:
            break
    return sorted(out, key=lambda w: w["sow_on"])


def harvest_status(c: Crop, sowed: dt.date, today: dt.date) -> dict:
    """Where a crop sown on `sowed` stands against the EcoCrop cycle length (varieties differ, so it is a range, not a date)."""
    age = (today - sowed).days
    earliest, latest = sowed + dt.timedelta(days=c.gmin), sowed + dt.timedelta(days=c.gmax)
    if age < 0:
        status = "NOT_SOWN_YET"
    elif age < c.gmin:
        status = "BEFORE_EARLIEST_HARVEST"
    elif age <= c.gmax:
        status = "IN_HARVEST_WINDOW"
    else:
        status = "PAST_LATEST_CYCLE"
    return {"days_since_sowing": age, "earliest": earliest.isoformat(), "latest": latest.isoformat(), "status": status, "days_to_earliest": max((earliest - today).days, 0)}


def ph_fit(c: Crop, ph: float) -> dict | None:
    """Soil pH against the crop's pH limits (EcoCrop). None when the database has no pH for the crop."""
    if not c.ph_opt or not c.ph_abs:
        return None
    lo, hi = c.ph_opt
    alo, ahi = c.ph_abs
    if lo <= ph <= hi:
        label = "OPTIMAL"
    else:
        label = "ACCEPTABLE" if alo <= ph <= ahi else "OUTSIDE_TOLERANCE"
    return {"ph": ph, "optimal": [lo, hi], "absolute": [alo, ahi], "label": label}
