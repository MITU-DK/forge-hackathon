"""Deterministic price-trend signal from REAL mandi price history, plus weather cross-checks.

The LLM explains these results; it does not compute or override them.

The signal is a TREND-FOLLOWING indicator, not a forecast. It compares the SAME markets today and about a week ago:
rising prices -> HOLD, falling prices -> SELL_NOW, small moves -> NEUTRAL. Why this direction: a year of real Agmarknet
prices (6 series) showed momentum (a move tends to continue), and the earlier rule "5 % above the recent average -> sell"
pointed the wrong way (see scripts/backtest_signal.py; README "Status" has the measured numbers and their limits).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

TREND_BAND_PCT = 5.0  # |7-day move| below this is NEUTRAL (chosen on the older 60 % of a year of data, judged on the newest 40 %)
TREND_DAYS = 7  # compare today with the day closest to a week earlier ...
TREND_DAY_SLACK = 2  # ... within +/- this many days (markets close on some days)
MIN_SHARED_MARKETS = 5  # markets that must report on both days; fewer = no trend, never a guess
OUTLIER_FACTOR = 10.0
POSITION_BAND_PCT = 5.0  # local median within +/-5 % of the state median counts as NEAR


def norm_name(s: str) -> str:
    """Compare place names across sources: 'Srīnagar District' / 'Raipur district' / 'Raipur' must all match."""
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"\bdistrict\b", "", t)).strip()


def parse_date(s: str) -> dt.date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def build_series(records: list[dict]) -> list[dict]:
    """records: normalised dicts with arrival_date + modal_price. -> [{date, modal, n}] ascending."""
    by: dict[dt.date, list[float]] = {}
    for r in records:
        d, m = parse_date(r.get("arrival_date", "")), to_float(r.get("modal_price"))
        if d and m and m > 0:
            by.setdefault(d, []).append(m)
    by = {d: _drop_outliers(v) for d, v in by.items()}
    return [{"date": d.isoformat(), "modal": round(sum(v) / len(v), 0), "n": len(v)} for d, v in sorted(by.items())]


PARTIAL_DAY_RATIO = 0.5  # a newest day with under half the previous day's markets is still being filled in


def without_partial_day(records: list[dict]) -> tuple[list[dict], dict | None]:
    """data.gov.in publishes through the morning, so "today" can hold one or two markets while yesterday has fifty. Prices,
    market position and the trend are then taken from the last complete day instead of from a handful of markets. Crops that
    always report from a few markets are untouched (their day-to-day counts are alike). -> (records, note or None)."""
    series = build_series(records)
    if len(series) < 2 or series[-1]["n"] >= PARTIAL_DAY_RATIO * series[-2]["n"]:
        return records, None
    cutoff = parse_date(series[-2]["date"])
    kept = [r for r in records if (d := parse_date(r.get("arrival_date", ""))) and d <= cutoff]
    return kept, {"date": series[-1]["date"], "markets": series[-1]["n"], "used": series[-2]["date"]}


def _drop_outliers(vals: list[float]) -> list[float]:
    """Agmarknet has data-entry slips (an extra zero, a Rs/kg row). Drop prices >10x or <1/10 of the day's median."""
    if len(vals) < 3:
        return vals
    med = _median(vals)
    return [v for v in vals if med / OUTLIER_FACTOR < v < med * OUTLIER_FACTOR]


def _market_label(row: dict) -> str:
    return " ".join(str(row.get("market") or "").split())


def per_market(rows: list[dict]) -> list[dict]:
    """One row per market: Agmarknet reports one row per variety/grade, so a market with four varieties would count four times
    in a median or a "49 markets" figure. Modal price = median over the market's rows, min = lowest min, max = highest max."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r.get("modal"):
            groups.setdefault((_market_label(r), norm_name(r.get("district"))), []).append(r)
    out = []
    for group in groups.values():
        mins, maxs = [x["min"] for x in group if x.get("min")], [x["max"] for x in group if x.get("max")]
        out.append({
            "market": _market_label(group[0]), "district": group[0].get("district"), "modal": _median([x["modal"] for x in group]),
            "min": min(mins) if mins else None, "max": max(maxs) if maxs else None, "varieties": len(group),
        })
    return out


def clean_rows(rows: list[dict]) -> list[dict]:
    """Same outlier rule as build_series, for per-market rows (all one commodity): drops entries 10x away from the median
    (typos or unit slips). NOTE: the fetch layer already removes other commodities that the API's loose filter returns."""
    kept = set(_drop_outliers([r["modal"] for r in rows if r.get("modal")]))
    return [r for r in rows if r.get("modal") and r["modal"] in kept]


# What the one-year backtest (scripts/backtest_signal.py, newest 40 % of days, unseen when the rule was chosen) showed per crop.
# Words, not numbers, so the answer stays grounded; the measured figures live in the README. Crops not listed were not tested.
TREND_EVIDENCE = {
    "onion": "In a one-year check on real onion prices, a rising week was followed by a further rise about two times out of three.",
    "tomato": "In a one-year check on real tomato prices this trend rule was NOT reliable, so treat it as a weak hint only.",
    "potato": "Potato prices barely moved in the one-year check, so this rule has little to say about potato.",
}
TREND_UNTESTED = "This trend rule has not been checked for this crop, so treat it as a weak hint."


def trend_evidence(commodity: str, signal: str | None = None) -> str:
    """The backtest sentence for this crop. Given the live signal it is only returned where it applies: the onion result
    is about a RISING week, so it goes with HOLD alone; with NEUTRAL there is no trend claim to qualify."""
    text = TREND_EVIDENCE.get(str(commodity or "").strip().lower(), TREND_UNTESTED)
    if signal == "NEUTRAL" or (signal == "SELL_NOW" and text is TREND_EVIDENCE["onion"]):
        return ""
    return text


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2


def market_day_prices(records: list[dict]) -> dict[dt.date, dict[str, float]]:
    """records -> {date: {market: price}}. One price per market and day (median over its variety/grade rows); values 10x away
    from that day's median are dropped; days with fewer than 3 markets are ignored."""
    by: dict[tuple[dt.date, str], list[float]] = {}
    for r in records:
        d, p = parse_date(r.get("arrival_date", "")), to_float(r.get("modal_price"))
        m = " ".join(str(r.get("market") or "").split()).lower()
        if d and p and p > 0 and m:
            by.setdefault((d, m), []).append(p)
    days: dict[dt.date, dict[str, float]] = {}
    for (d, m), v in by.items():
        days.setdefault(d, {})[m] = _median(v)
    out = {}
    for d, mk in days.items():
        keep = set(_drop_outliers(list(mk.values())))
        mk = {m: p for m, p in mk.items() if p in keep}
        if len(mk) >= 3:
            out[d] = mk
    return out


def _comparison_day(order: list[dt.date], latest: dt.date) -> dt.date | None:
    """The reported day closest to a week before `latest` (within the slack), or None."""
    candidates = [d for d in order if d < latest and abs((latest - d).days - TREND_DAYS) <= TREND_DAY_SLACK]
    return min(candidates, key=lambda d: abs((latest - d).days - TREND_DAYS)) if candidates else None


def _signal_word(pct: float) -> str:
    if pct >= TREND_BAND_PCT:
        return "HOLD"
    return "SELL_NOW" if pct <= -TREND_BAND_PCT else "NEUTRAL"


def trend_signal(records: list[dict]) -> dict:
    """7-day trend of the SAME markets: median over markets reporting on both days of (price today / price then - 1).
    A shifting set of reporting markets therefore cannot fake a price move (the old daily-average series could)."""
    days = market_day_prices(records)
    order = sorted(days)
    result = {"signal": "INSUFFICIENT_DATA", "points": len(order), "latest": None, "latest_date": None}
    if not order:
        return result
    t = order[-1]
    latest = round(sum(days[t].values()) / len(days[t]))
    result.update(latest=latest, latest_date=t.isoformat())
    earlier = _comparison_day(order, t)
    if earlier is None:
        return result
    shared = [m for m in days[t] if m in days[earlier]]
    if len(shared) < MIN_SHARED_MARKETS:
        return result
    pct = _median([(days[t][m] / days[earlier][m] - 1) * 100 for m in shared])
    gap = (t - earlier).days
    base = sum(days[earlier][m] for m in shared) / len(shared)
    now = sum(days[t][m] for m in shared) / len(shared)
    return {
        "signal": _signal_word(pct),
        "points": len(order),
        "latest": latest,
        "latest_date": t.isoformat(),
        "baseline_mean": round(base),  # kept for the UI card: average price of the compared markets on the earlier day
        "pct_vs_baseline": round(pct, 1),  # same number as change_pct (kept for the UI card)
        "change_pct": round(pct, 1),
        "compared_with": earlier.isoformat(),
        "window_days": gap,
        "markets_compared": len(shared),
        "trend_per_day": round((now - base) / gap, 1),
    }


RECENT_TREND_NOTE = "This is a recent trend, not a price forecast."


def _rain_note(flag: dict, sig: str | None, price: dict, position: dict | None) -> str:
    """The note for heavy rain in the next few days, depending on the price trend (or, without one, the market position)."""
    rain = f"heavy rain ({flag['value']} mm) forecast on {flag['day']}"
    window = price.get("window_days", 7)
    label = (position or {}).get("label")
    no_trend = sig in (None, "INSUFFICIENT_DATA", "NEUTRAL")
    if sig == "SELL_NOW":
        return (f"SELL_BEFORE_RAIN: {rain}; prices fell {abs(price.get('change_pct', 0))} % over the last {window} days and rain can damage "
                f"stored produce, so selling before the rain is worth considering. {RECENT_TREND_NOTE}")
    if sig == "HOLD":
        return (f"STORAGE_RISK: {rain}; prices have been rising ({price.get('change_pct', 0)} % over the last {window} days), but rain can damage "
                f"stored produce: protect it, or sell part of it before the rain. {RECENT_TREND_NOTE}")
    if no_trend and label == "ABOVE_STATE_MEDIAN":
        return (f"SELL_BEFORE_RAIN: {rain}; no clear price trend, but your district's price today is {position['pct_vs_state_median']} % above "
                "the state median and rain can damage stored produce, so selling before the rain is worth considering.")
    if no_trend and label == "BELOW_STATE_MEDIAN":
        return (f"STORAGE_RISK: {rain}; your district's price today is {abs(position['pct_vs_state_median'])} % below the state median. "
                f"The best price today is Rs {position['best_price']} at {position['best_market']} ({position['best_district']}); "
                "protect stored produce or consider selling early.")
    return f"STORAGE_RISK: {rain}; protect stored produce or consider selling early."


def cross_check(price: dict, weather: dict, position: dict | None = None) -> list[str]:
    """Combine the price trend with weather risk. Pure function; returns plain-English notes. SELL_NOW means prices have been
    FALLING, HOLD means RISING (trend-following). Without a trend the farmer's market position today (vs the state median)
    is used instead, and the note says so. Neither is a price forecast."""
    notes: list[str] = []
    heavy_soon = [f for f in weather.get("flags", []) if f["flag"] == "HEAVY_RAIN" and f["day_index"] <= 3]
    sig = price.get("signal")
    if heavy_soon:
        notes.append(_rain_note(heavy_soon[0], sig, price, position))
    if sig == "INSUFFICIENT_DATA":
        notes.append("Price history is too short for a trend; report only the latest published prices and say a trend is not available.")
    return notes


def market_position(rows: list[dict], district: str) -> dict | None:
    """Where the farmer's district sits among the state's markets on the latest day. Descriptive, not a forecast: it works
    from a single day of prices, without any history. rows = [{market, district, modal}]."""
    ok = [r for r in rows if r.get("modal")]
    if len(ok) < 3:
        return None
    state_median = _median([r["modal"] for r in ok])
    best = max(ok, key=lambda r: r["modal"])
    out = {
        "state_median": round(state_median), "markets": len(ok), "best_market": best.get("market"),
        "best_district": best.get("district"), "best_price": round(best["modal"]),
    }
    local = [r["modal"] for r in ok if district and norm_name(r.get("district")) == norm_name(district)]
    if local and state_median:
        local_median = _median(local)
        pct = (local_median - state_median) / state_median * 100
        if pct >= POSITION_BAND_PCT:
            label = "ABOVE_STATE_MEDIAN"
        else:
            label = "BELOW_STATE_MEDIAN" if pct <= -POSITION_BAND_PCT else "NEAR_STATE_MEDIAN"
        out.update({"local_median": round(local_median), "local_markets": len(local), "pct_vs_state_median": round(pct, 1), "label": label})
    return out
