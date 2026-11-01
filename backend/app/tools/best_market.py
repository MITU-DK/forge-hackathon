"""Best market nearby: the top-paying mandis for the farmer's crop with their straight-line distance from the farm.

Prices come from the same Agmarknet call as the mandi tool (cached). Each market town is placed by the live geocoder, only inside the
farmer's state; a market it cannot place is left out of the distances and counted, never guessed. No transport cost is invented."""
from __future__ import annotations

import concurrent.futures as cf
import re

from app.decision.market_rank import NEARBY_KM, lot_value, parse_quantity, rank_markets
from app.decision.price_rules import norm_name
from app.tools import SourceUnavailable, ToolResult, guarded
from app.tools.location import MIN_NAME_MATCH, _name_match, _search
from app.tools.mandi import get_mandi

CANDIDATES = 10  # highest-priced markets we try to place
_PAREN = re.compile(r"\(.*?\)|\bapmc\b|\bmandi\b|\bmarket\b", re.I)


def _town(market: str) -> str:
    return " ".join(_PAREN.sub(" ", market).split())


def _place_one(row: dict, state: str) -> tuple[str, dict | None, dict | None]:
    """-> (market label, {lat, lon, located_by} or None, geocoder Fetched source or None). Town first, then the market's district."""
    for query, how in ((_town(row["market"]), "town"), (row.get("district") or "", "district")):
        if not query:
            continue
        try:
            results, fetched = _search(query)
        except SourceUnavailable:
            return row["market"], None, None
        ok = [r for r in results if norm_name(r.get("admin1")) == norm_name(state) and _name_match(query, r) >= MIN_NAME_MATCH]
        if ok:
            best = max(ok, key=lambda r: r.get("population") or 0)
            src = {"name": "Open-Meteo geocoding", "url": fetched.url, "fetched_at": fetched.fetched_at, "stale": fetched.stale}
            return row["market"], {"lat": best["latitude"], "lon": best["longitude"], "located_by": how}, src
    return row["market"], None, None


def get_best_market(profile: dict, text: str = "") -> ToolResult:
    m = get_mandi(profile, text)
    rows = m.facts.get("markets_all") or []
    loc = m.facts["location"]
    if not rows:
        return ToolResult("BEST MARKET: no market reported prices for this crop on the latest day, so no distance ranking is possible.", True, m.sources, [], {"location": loc})
    top = sorted(rows, key=lambda r: r["modal"], reverse=True)[:CANDIDATES]
    with cf.ThreadPoolExecutor(4) as pool:
        placed = list(pool.map(lambda r: _place_one(r, loc["state"]), top))
    places = {name: p for name, p, _ in placed}
    sources = list(m.sources) + [s for _, _, s in placed if s][:1]
    ranked = rank_markets(top, places, (loc["lat"], loc["lon"]))
    crop = m.facts["commodity"]
    if not ranked["markets"]:
        return ToolResult(f"BEST MARKET: the {len(top)} highest-priced markets could not be placed on the map, so distances are not available. Say so; do not guess distances.", True, sources, [], {"location": loc})
    lines = [f"BEST MARKETS for {crop} from {loc['name']} (computed in code; distance is a straight line, NOT road distance; transport cost, loading and market fees are NOT included):"]
    for mk in ranked["markets"]:
        away = f"about {mk['km']} km away (to the centre of its district; the town itself was not found)" if mk["located_by"] == "district" else f"{mk['km']} km away"
        lines.append(f"- {mk['market']} ({mk['district']}): Rs {mk['modal']:.0f} per quintal, {away}, Rs {mk['vs_nearest']:+d} vs the nearest listed market")
    if ranked["best_nearby"]:
        b = ranked["best_nearby"]
        lines.append(f"BEST WITHIN {NEARBY_KM:.0f} KM: {b['market']} at Rs {b['modal']:.0f} per quintal, {'about ' if b['located_by'] == 'district' else ''}{b['km']} km away.")
    else:
        lines.append(f"NOTE: none of the placed markets is within {NEARBY_KM:.0f} km. Say so plainly.")
    lot = None
    qty = parse_quantity(profile.get("quantity")) if profile.get("quantity") else None
    if qty:
        lot = lot_value(ranked, qty)
    if lot:
        line = f"LOT VALUE (computed in code, before transport, loading, market fees and grade): Rs {lot['price']} per quintal x {qty:g} quintals = Rs {lot['total']:,} at {lot['market']}."
        if "gap_total" in lot:
            line += f" Versus the nearest listed market {lot['nearest']} (Rs {lot['nearest_price']}): Rs {lot['gap_per_quintal']:+d} per quintal x {qty:g} = Rs {lot['gap_total']:+,}."
        lines.insert(1, line)  # first, so the answer leads with the farmer's own lot
    if ranked["unplaced"]:
        lines.append(f"NOTE: {ranked['unplaced']} of the {len(top)} top-priced markets could not be placed on the map and are not listed.")
    card = {"type": "best_market", "title": f"{crop} - best markets from {loc['name']}", "unit": "Rs/quintal", "radius_km": NEARBY_KM,
            "markets": ranked["markets"], "best_nearby": ranked["best_nearby"], "nearest": ranked["nearest"],
            "lot": lot, "note": "Straight-line distance. Transport cost, loading and market fees are not included."}
    return ToolResult("\n".join(lines), True, sources, [card], {"location": loc, "best_market": ranked})


best_market = guarded("best_market")(lambda text, profile: get_best_market(profile, text))
