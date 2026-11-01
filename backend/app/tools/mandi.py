"""Live mandi (wholesale market) prices from data.gov.in / Agmarknet, plus a sell/hold signal.

Needs a free key: DATA_GOV_API_KEY (https://data.gov.in -> register -> My Account -> API key).
Without it the tool returns an explicit "source unavailable" result; it NEVER fills in prices.

Two resources, both verified live on 30 Oct 2026:
* MANDI_RESOURCE_ID: today's prices only (the date filter is ignored; lower-case filter names).
* MANDI_HISTORY_RESOURCE_ID: dated prices up to yesterday (the date filter works; Title-case filter names). This is what
  makes a trend possible. If it fails the tool degrades to today's prices and says no trend is available.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

from app.core.config import get_settings
from app.decision.price_rules import (
    MIN_SHARED_MARKETS,
    TREND_BAND_PCT,
    build_series,
    clean_rows,
    cross_check,
    market_position,
    norm_name,
    parse_date,
    per_market,
    to_float,
    trend_evidence,
    trend_signal,
    without_partial_day,
)
from app.tools import NeedsInput, SourceUnavailable, ToolResult, guarded
from app.tools.http import get_json
from app.tools.location import resolve_location
from app.tools.weather import get_weather

log = logging.getLogger("tool.mandi")
API = "https://api.data.gov.in/resource/{rid}"
PAGE, MAX_PAGES = 500, 4  # per-call limit and a cap on pages: a state-level query is far below 2000 rows
HISTORY_TTL_S = 3600  # new days arrive at most daily, and every cache miss spends rate-limited quota
HISTORY_PAGES = 2  # 500 rows is about a week for a busy crop in a big state
HISTORY_RETRY_DELAY_S = 0.6
HISTORY_TIMEOUT_S = 4.0  # history is an extra: it must not eat the whole request when the API is slow

# UI/common crop name -> Agmarknet commodity name. EVERY name below was checked against the real data.gov.in file of
# 30 Oct 2026 (all exist; 'Paddy(Dhan)(Common)' did not and is now 'Paddy(Common)'). Unknown crops pass through Title-Cased.
# Sugarcane is absent on purpose: the dataset has no sugarcane commodity.
ALIASES = {
    "tomato": "Tomato", "onion": "Onion", "potato": "Potato", "wheat": "Wheat", "maize": "Maize", "rice": "Rice",
    "paddy": "Paddy(Common)", "soybean": "Soyabean", "soyabean": "Soyabean", "cotton": "Cotton",
    "groundnut": "Groundnut", "gram": "Bengal Gram(Gram)(Whole)", "chana": "Bengal Gram(Gram)(Whole)",
    "banana": "Banana", "mustard": "Mustard", "bajra": "Bajra(Pearl Millet/Cumbu)", "jowar": "Jowar(Sorghum)",
    "cabbage": "Cabbage", "cauliflower": "Cauliflower", "brinjal": "Brinjal", "garlic": "Garlic", "turmeric": "Turmeric",
    "chilli": "Green Chilli", "coriander": "Coriander(Leaves)", "moong": "Green Gram(Moong)(Whole)",
    "urad": "Black Gram(Urd Beans)(Whole)", "tur": "Red gram/Arhar/Tur(whole)", "arhar": "Red gram/Arhar/Tur(whole)",
    "apple": "Apple", "mango": "Mango(Raw-Ripe)", "lemon": "Lemon", "ginger": "Ginger(Green)",
    "pomegranate": "Pomegranate", "grapes": "Grapes",
}


# Hindi/Marathi crop names -> the English key above. Farmers type their crop in their own script; without this the
# Devanagari word would be sent to Agmarknet as a commodity and match nothing. Kept apart from ALIASES: the UI dropdown
# is built from ALIASES and must stay English.
NATIVE_CROPS = {
    "कांदा": "onion", "कांदे": "onion", "प्याज": "onion", "प्याज़": "onion", "टोमॅटो": "tomato", "टमाटर": "tomato",
    "बटाटा": "potato", "आलू": "potato", "गहू": "wheat", "गेहूं": "wheat", "गेहूँ": "wheat", "मका": "maize", "मक्का": "maize",
    "तांदूळ": "rice", "चावल": "rice", "धान": "paddy", "भात": "paddy", "सोयाबीन": "soybean", "कापूस": "cotton", "कपास": "cotton",
    "भुईमूग": "groundnut", "मूंगफली": "groundnut", "हरभरा": "gram", "चना": "chana", "केळी": "banana", "केला": "banana",
    "मोहरी": "mustard", "सरसों": "mustard", "बाजरी": "bajra", "बाजरा": "bajra", "ज्वारी": "jowar", "ज्वार": "jowar",
    "कोबी": "cabbage", "पत्तागोभी": "cabbage", "फुलकोबी": "cauliflower", "वांगी": "brinjal", "बैंगन": "brinjal",
    "लसूण": "garlic", "लहसुन": "garlic", "हळद": "turmeric", "हल्दी": "turmeric", "मिरची": "chilli", "मिर्च": "chilli",
    "कोथिंबीर": "coriander", "धनिया": "coriander", "मूग": "moong", "उडीद": "urad", "उड़द": "urad", "तूर": "tur", "अरहर": "arhar",
    "सफरचंद": "apple", "सेब": "apple", "आंबा": "mango", "आम": "mango", "लिंबू": "lemon", "नींबू": "lemon",
    "आले": "ginger", "अदरक": "ginger", "डाळिंब": "pomegranate", "अनार": "pomegranate", "द्राक्षे": "grapes", "अंगूर": "grapes",
}
_DEVANAGARI = "\u0900-\u097f"


# Latin-script spellings of Hindi/Marathi crop names (Hinglish: "pyaz ka bhav") and Marathi/Hindi word STEMS: a crop is
# inflected in a sentence ("कांद्याचा भाव" = onion's price) and would not equal the dictionary form above. A stem is
# matched only at the start of a Devanagari word and only when it is long enough not to hit unrelated words.
TRANSLIT_CROPS = {
    "pyaz": "onion", "pyaaz": "onion", "kanda": "onion", "tamatar": "tomato", "aloo": "potato", "alu": "potato", "batata": "potato",
    "gehu": "wheat", "gehun": "wheat", "gahu": "wheat", "makka": "maize", "makai": "maize", "chawal": "rice", "dhan": "paddy",
    "kapas": "cotton", "kapus": "cotton", "sarson": "mustard", "lahsun": "garlic", "haldi": "turmeric", "adrak": "ginger",
    "tomatoes": "tomato", "onions": "onion", "potatoes": "potato", "chillies": "chilli", "mangoes": "mango", "bananas": "banana",
}
CROP_STEMS = {
    "कांद": "onion", "कांदे": "onion", "प्याज": "onion", "टोमॅट": "tomato", "टमाटर": "tomato", "बटाट": "potato", "गव्ह": "wheat", "गेहू": "wheat",
    "कापस": "cotton", "सोयाबीन": "soybean", "हरभर": "gram", "तांदळ": "rice", "मक्य": "maize", "ज्वारी": "jowar", "बाजर": "bajra", "भुईमूग": "groundnut",
}


def crops_in_text(text: str) -> list[str]:
    """Distinct crops named in a question, as lower-case ALIASES keys, in order of first appearance. Used to let the question
    override the profile crop; with two crops named it is ambiguous and the caller keeps the profile."""
    found: list[str] = []
    low = (text or "").lower()
    for m in re.finditer(r"[a-z]+", low):
        w = m.group(0)
        key = TRANSLIT_CROPS.get(w) or (w if w in ALIASES else None)
        if key and key not in found:
            found.append(key)
    for m in re.finditer(rf"[{_DEVANAGARI}]+", text or ""):
        w = m.group(0)
        key = NATIVE_CROPS.get(w) or next((c for st, c in CROP_STEMS.items() if w.startswith(st)), None)
        if key and key not in found:
            found.append(key)
    return found


def crop_key(crop: str) -> str:
    """Lower-case English key for a typed crop: 'कांदा' -> 'onion'; anything unknown is returned lower-cased as typed."""
    c = (crop or "").strip().lower()
    return NATIVE_CROPS.get(c, c)


# Geocoder state name (lower-case) -> the spelling used in the data.gov.in dataset. Checked against the real file of
# 30 Oct 2026 and live geocoder output. Names the dataset does not contain today (Jharkhand, Sikkim, Mizoram, Arunachal,
# Ladakh) are left alone and honestly return "no records".
STATE_ALIASES = {
    "kerala": "Keralam", "chhattisgarh": "Chattisgarh", "national capital territory of delhi": "NCT of Delhi",
    "delhi": "NCT of Delhi", "puducherry": "Pondicherry", "andaman and nicobar islands": "Andaman and Nicobar",
    "orissa": "Odisha", "uttaranchal": "Uttarakhand",
}


def agmarknet_state(name: str) -> str:
    return STATE_ALIASES.get((name or "").strip().lower(), (name or "").strip())


def commodity_name(profile: dict, text: str) -> str:
    crop = crop_key(profile.get("crop") or "")
    if not crop:
        low = (text or "").lower()
        crop = next((k for k in ALIASES if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low)), "")  # whole words only
    if not crop:
        crop = next((v for k, v in NATIVE_CROPS.items() if re.search(rf"(?<![{_DEVANAGARI}]){re.escape(k)}(?![{_DEVANAGARI}])", text or "")), "")
    if not crop:
        raise NeedsInput("crop", "which crop the farmer grows or wants to sell")
    return ALIASES.get(crop, crop.title())


def _norm(rec: dict) -> dict:
    """Lower-case keys and undo the XML-style space escape: the data.gov.in preview shows 'Modal X0020 Price', i.e. the
    field may be named `Modal_x0020_Price` instead of `modal_price`. Accept both so prices are never silently dropped."""
    return {str(k).lower().replace("_x0020_", "_"): v for k, v in rec.items()}


def _same(a, b) -> bool:
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def _fetch_records(state: str, commodity: str, key: str, rid: str, style: str, ttl: float | None = None):
    """Returns (records, Fetched|None). Verified against the live API (30 Oct 2026):
    * lower-case filter names work (`filters[state]`); Title-case ones match nothing;
    * the date filter is ignored (yesterday returns the same total as today): this resource publishes only the current day;
    * the commodity filter is a LOOSE text match: 'Onion' also returns 'Onion Green', 'Potato' returns 'Sweet Potato',
      'Green Chilli' returns ginger and moong gram. So records are kept only if the commodity matches EXACTLY.
    Pages through results (the API reports `total`) so an exact match is never cut off by the page size."""
    styles = ["lower", "title"] if style == "auto" else [style]
    last = None
    for st in styles:
        names = ("state", "commodity") if st == "lower" else ("State", "Commodity")
        got: list[dict] = []
        for page in range(MAX_PAGES):
            params = {"api-key": key, "format": "json", "limit": PAGE, "offset": page * PAGE, f"filters[{names[0]}]": state, f"filters[{names[1]}]": commodity}
            f = get_json(API.format(rid=rid), params, ttl=get_settings().cache_ttl_s * 2 if ttl is None else ttl)
            last = f
            data = f.data if isinstance(f.data, dict) else {}
            batch = [_norm(r) for r in (data.get("records") or [])]
            got += batch
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            if not batch or len(got) >= total:
                break
        exact = [r for r in got if _same(r.get("commodity", ""), commodity)]
        if exact:
            return exact, last
    return [], last


def _fetch_history(state: str, commodity: str, key: str, rid: str, days: int, ttl: float = HISTORY_TTL_S):
    """The `days - 1` days before today -> (records, [Fetched], complete). Raw rows on purpose: build_series averages and
    cleans them. One sorted call instead of one per day, because data.gov.in rate limits (429) a burst and that would trip
    the shared circuit breaker and take today's prices down too. A failure only loses the trend, so it never raises."""
    today = dt.date.today()
    oldest = today - dt.timedelta(days=days - 1)
    records, fetched = [], []
    for page in range(HISTORY_PAGES):
        params = {"api-key": key, "format": "json", "limit": PAGE, "offset": page * PAGE, "filters[State]": state, "filters[Commodity]": commodity, "sort[Arrival_Date]": "desc"}
        f = None
        for attempt in range(2):  # the shared free API drops the odd request; losing the history silently loses the trend
            try:
                f = get_json(API.format(rid=rid), params, ttl=ttl, timeout=HISTORY_TIMEOUT_S)
                break
            except Exception as e:
                log.warning("price history unavailable (attempt %d): %s", attempt + 1, type(e).__name__)
                if attempt == 0:
                    time.sleep(HISTORY_RETRY_DELAY_S)
        if f is None:
            return records, fetched, False
        batch = [_norm(r) for r in ((f.data if isinstance(f.data, dict) else {}).get("records") or [])]
        dates = [d for d in (parse_date(r.get("arrival_date", "")) for r in batch) if d]
        records += [r for r in batch if _same(r.get("commodity", ""), commodity) and (d := parse_date(r.get("arrival_date", ""))) and oldest <= d < today]
        fetched.append(f)
        if len(batch) < PAGE or not dates or min(dates) < oldest:
            break
    return records, fetched if records else [], True


def own_markets(rows: list[dict], names: list[str]) -> list[dict]:
    """Markets (one row each, see per_market) whose name matches the place the farmer gave: "Lasalgaon" -> "Lasalgaon(Vinchur)".
    Names shorter than 4 letters are not matched (substring hits on unrelated markets). At most 3, best-priced first."""
    keys = {k for k in (norm_name(n) for n in names) if len(k) >= 4}
    hits = [r for r in rows if r.get("market") and any(k in norm_name(r["market"]) for k in keys)]
    return sorted(hits, key=lambda r: r["modal"], reverse=True)[:3]


LOCATION_KEYS = ("name", "state", "district", "lat", "lon")
SIGNAL_WORDS = {"HOLD": "prices are RISING", "SELL_NOW": "prices are FALLING", "NEUTRAL": "prices are roughly STEADY"}
DECISION_WORDS = {"HOLD": "prices rising", "SELL_NOW": "prices falling", "NEUTRAL": "prices roughly steady"}


def _num(v):
    return to_float(v)


def _same_day(raw, iso: str) -> bool:
    d = parse_date(raw or "")
    return bool(d) and d.isoformat() == iso


def _source(name: str, fetched: list) -> dict:
    return {"name": name, "url": fetched[0].url, "fetched_at": max(f.fetched_at for f in fetched), "stale": any(f.stale for f in fetched)}


def _price_row(r: dict) -> dict:
    return {"market": r.get("market"), "district": r.get("district"), "modal": _num(r.get("modal_price")), "min": _num(r.get("min_price")), "max": _num(r.get("max_price"))}


def _no_records_result(loc: dict, commodity: str, where: str, days: int, history_complete: bool, sources: list) -> ToolResult:
    span = f"in the last {days} days" if history_complete else "today (earlier days could not be checked: the history source did not respond)"
    text = (
        f"MANDI PRICES: no published records for {commodity} in {where} {span} (source: data.gov.in Agmarknet). "
        "Say honestly that no price data is available; do not estimate prices, do not call prices neutral or steady, and give no sell or hold view."
    )
    if history_complete:
        text += " The source answered normally and simply holds no records for this crop, so never say it is unreachable; suggest checking the crop name."
    facts = {"location": {k: loc[k] for k in LOCATION_KEYS}, "price": {"signal": "INSUFFICIENT_DATA", "points": 0}}
    return ToolResult(text, True, sources, [], facts)


def _signal_line(commodity: str, sig: dict) -> str:
    if sig["signal"] == "INSUFFICIENT_DATA":
        return (
            f"SIGNAL: INSUFFICIENT_DATA (no comparable day about a week earlier with at least {MIN_SHARED_MARKETS} markets in common). "
            "Say plainly that a price trend is not available and report today's prices and the market position only."
        )
    return (
        f"SIGNAL (computed in code): {sig['signal']} - {SIGNAL_WORDS[sig['signal']]}: the same {sig['markets_compared']} markets moved "
        f"{sig['change_pct']} % between {sig['compared_with']} and {sig['latest_date']} ({sig['window_days']} days). "
        f"Rule: a rise of {TREND_BAND_PCT:g} % or more = HOLD, a fall of {TREND_BAND_PCT:g} % or more = SELL_NOW, otherwise NEUTRAL. "
        f"This is a recent trend, not a forecast. {trend_evidence(commodity, sig['signal'])} Say that in one or two short sentences."
    )


def _own_market_line(own: list[dict], latest_date: str) -> str:
    def one(m: dict) -> str:
        spread = f" (lowest Rs {m['min']:.0f}, highest Rs {m['max']:.0f})" if m.get("min") and m.get("max") else ""
        return f"{m['market']} Rs {m['modal']:.0f} per quintal{spread}"

    return (
        f"YOUR MARKET on {latest_date}: " + "; ".join(one(m) for m in own)
        + ". The market name matches the farmer's place: lead with this price, it is the farmer's own mandi, "
        "and say it is one price per market (median over varieties)."
    )


def _position_line(position: dict, where: str, district_label: str, latest_date: str) -> str:
    line = f"MARKET POSITION (computed in code, {latest_date}): {where} median Rs {position['state_median']} across {position['markets']} markets"
    if "label" in position:
        line += f"; {district_label} median Rs {position['local_median']} ({position['pct_vs_state_median']} % vs state median) -> {position['label']}"
    return line + f"; best price Rs {position['best_price']} at {position['best_market']} ({position['best_district']}). This is today's snapshot, not a forecast."


def _text_lines(commodity: str, where: str, loc: dict, src: dict, series: list, partial: dict | None, sig: dict,
                own: list, near: list, top: list, position: dict | None, latest_date: str) -> list[str]:
    stale = " [STALE]" if src["stale"] else ""
    lines = [
        f"MANDI PRICES for {commodity} in {where} - data.gov.in Agmarknet, fetched {src['fetched_at']}{stale}",
        "Unit: Rs per quintal. Daily average of the modal prices reported that day (one report per variety and market):",
    ]
    lines += [f"{p['date']}: Rs {p['modal']:.0f} ({p['n']} price reports)" for p in series]
    if partial:
        lines.append(
            f"NOTE: only {partial['markets']} price report(s) have come in for {partial['date']} so far, so the latest COMPLETE day, "
            f"{partial['used']}, is used for everything below. Mention that today's prices are still arriving."
        )
    lines.append(_signal_line(commodity, sig))
    district_label = loc.get("district") or loc["name"]
    if own:
        lines.append(_own_market_line(own, latest_date))
    if near:
        lines.append(f"MARKETS in {district_label} on {latest_date}: " + "; ".join(f"{m['market']} Rs {m['modal']:.0f}" for m in near))
    elif top:  # small town / district with no reporting mandi: say so, then show the state's markets instead
        lines.append(f"NOTE: no markets reporting {commodity} in {district_label} on {latest_date}. Tell the farmer this, and that the prices below are from other markets in {where}.")
    if position:
        lines.append(_position_line(position, where, district_label, latest_date))
    if top:
        lines.append(f"HIGHEST MARKETS in {where} on {latest_date}: " + "; ".join(f"{m['market']} ({m['district']}) Rs {m['modal']:.0f}" for m in top))
    return lines


def _fetch_all(state_name: str, commodity: str, settings) -> tuple[list, list, list, list, bool]:
    """-> (today's records, Fetched list for them, history records, history Fetched list, history complete)."""
    key = settings.data_gov_api_key

    def today():
        return _fetch_records(state_name, commodity, key, settings.mandi_resource_id, settings.mandi_filter_style)

    def history(ttl: float = HISTORY_TTL_S):
        return _fetch_history(state_name, commodity, key, settings.mandi_history_resource_id, settings.mandi_days, ttl=ttl)

    records, f = today()
    fetched = [f] if f is not None else []
    if not fetched:
        raise SourceUnavailable("no response from data.gov.in")
    hist, hist_fetched, hist_complete = history()
    if not records and not hist:  # an empty answer can be a blip of the shared free API: ask once more, uncached, before saying "no data"
        time.sleep(HISTORY_RETRY_DELAY_S)
        again, f2 = _fetch_records(state_name, commodity, key, settings.mandi_resource_id, settings.mandi_filter_style, ttl=0)
        hist, hist_fetched, hist_complete = history(ttl=0)
        records, fetched = again, ([f2] if f2 is not None else fetched)
    return records, fetched, hist, hist_fetched, hist_complete


def get_mandi(profile: dict, text: str = "") -> ToolResult:
    s = get_settings()
    if not s.data_gov_api_key:
        raise SourceUnavailable("DATA_GOV_API_KEY is not set (free key from data.gov.in)")
    loc = resolve_location(profile)
    if not loc["state"]:
        raise NeedsInput("state", "the state of the farm (the place could not be matched to a state)")
    commodity = commodity_name(profile, text)
    state_name = agmarknet_state(loc["state"])
    where = loc["state"]

    records, fetched, history, hist_fetched, hist_complete = _fetch_all(state_name, commodity, s)
    all_recs, partial = without_partial_day(records + history)
    series = build_series(all_recs)
    src = _source("data.gov.in Agmarknet", fetched)
    sources = [src] + ([loc["source"]] if loc.get("source") else [])
    if hist_fetched:
        sources.insert(1, _source("data.gov.in Agmarknet history", hist_fetched))
    if not series:
        return _no_records_result(loc, commodity, where, s.mandi_days, hist_complete, sources)

    sig = trend_signal(all_recs)
    latest_date = series[-1]["date"]
    latest_recs = [r for r in all_recs if _same_day(r.get("arrival_date"), latest_date)]
    dist = norm_name(loc.get("district") or loc.get("name") or "")
    rows = clean_rows(per_market([_price_row(r) for r in latest_recs if _num(r.get("modal_price"))]))
    near = [r for r in rows if dist and norm_name(r["district"]) == dist][:3]
    top = sorted(rows, key=lambda r: r["modal"], reverse=True)[:3]
    position = market_position(rows, loc.get("district") or loc.get("name") or "")
    own = own_markets(rows, [loc.get("name") or "", (profile.get("place") or "").split(",")[0]])

    lines = _text_lines(commodity, where, loc, src, series, partial, sig, own, near, top, position, latest_date)
    shown = own + [m for m in near if m not in own] + [m for m in top if m not in own and m not in near]
    card = {"type": "prices", "title": f"{commodity} - mandi prices, {where}", "unit": "Rs/quintal", "series": series,
            "markets": shown[:5], "signal": sig, "position": position, "own_market": own}
    facts = {"location": {k: loc[k] for k in LOCATION_KEYS}, "price": sig, "commodity": commodity, "position": position, "own_market": own,
             "markets_all": [{"market": r["market"], "district": r["district"], "modal": r["modal"]} for r in rows]}
    return ToolResult("\n".join(lines), True, sources, [card], facts)


def get_cross_check(profile: dict, text: str = "") -> ToolResult:
    """Combines the (cached) price signal with the (cached) weather risk. Pure rules; no new data."""
    m, w = get_mandi(profile, text), get_weather(profile)
    price, weather_analysis = m.facts.get("price", {}), w.facts.get("analysis", {})
    notes = cross_check(price, weather_analysis, m.facts.get("position"))
    decision = price.get("signal", "INSUFFICIENT_DATA")
    if decision == "INSUFFICIENT_DATA":  # too few days of prices for a trend: say what we CAN decide on
        pos = m.facts.get("position") or {}
        where = f"MARKET POSITION {pos['label']} ({pos['pct_vs_state_median']} % vs state median)" if pos.get("label") else "no local market position either"
        lines = [
            f"DECISION (computed in code): NO PRICE TREND (no comparable day about a week earlier); {where}. "
            "Lead with today's position and the best-paying market; do not call this a sell or hold signal."
        ]
    else:
        what = DECISION_WORDS.get(decision, decision)
        lines = [f"DECISION (computed in code): {decision} ({what}, {price.get('change_pct')} % in {price.get('window_days')} days; a recent trend, not a forecast)"]
    lines += [f"NOTE: {n}" for n in notes]
    return ToolResult("\n".join(lines), True, [], [], {"decision": decision, "notes": notes})


mandi = guarded("mandi")(lambda text, profile: get_mandi(profile, text))
cross = guarded("cross_check")(lambda text, profile: get_cross_check(profile, text))
