"""Best market nearby: the top-paying markets with their real distance from the farm. Pure functions.

Distance is straight-line (haversine) between the farm and the market town as the geocoder places it. It is NOT road distance, and
transport cost, loading charges and market fees are NOT included: the price gap is only the gap in the reported modal price."""
from __future__ import annotations

import math

NEARBY_KM = 100.0  # HEURISTIC: a day's trip by truck; the farmer can see every distance and judge for themselves
MAX_LISTED = 6
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def rank_markets(rows: list[dict], places: dict[str, dict | None], farm: tuple[float, float]) -> dict:
    """rows: [{market, district, modal}], places: market -> {lat, lon, located_by} or None (could not be placed).
    -> {markets: [...], nearest, best_nearby, best_overall}; every entry carries distance and the gap to the nearest placed market."""
    placed = []
    for r in rows:
        p = places.get(r["market"])
        if p:
            km = haversine_km(farm[0], farm[1], p["lat"], p["lon"])
            placed.append({**r, "km": round(km), "located_by": p.get("located_by", "town")})
    if not placed:
        return {"markets": [], "nearest": None, "best_nearby": None, "best_overall": None, "unplaced": len(rows)}
    exact = [m for m in placed if m["located_by"] == "town"]  # a market placed only by its district centre has no real distance
    nearest = min(exact or placed, key=lambda m: m["km"])
    for m in placed:
        m["vs_nearest"] = round(m["modal"] - nearest["modal"])
    by_price = sorted(placed, key=lambda m: m["modal"], reverse=True)
    nearby = [m for m in by_price if m["km"] <= NEARBY_KM and m["located_by"] == "town"]
    shown = by_price[:MAX_LISTED]
    if nearest not in shown:
        shown = shown[: MAX_LISTED - 1] + [nearest]
    return {
        "markets": shown, "nearest": nearest, "best_nearby": nearby[0] if nearby else None,
        "best_overall": by_price[0], "unplaced": len(rows) - len(placed),
    }


MAX_QUINTALS = 100000


def parse_quantity(raw) -> float | None:
    """Quintals the farmer wants to sell: a positive number up to MAX_QUINTALS, else None (never guessed)."""
    try:
        q = float(str(raw).replace(",", ".").strip().split()[0])
    except (ValueError, IndexError):
        return None
    return round(q, 2) if 0 < q <= MAX_QUINTALS else None


def lot_value(ranked: dict, quintals: float) -> dict | None:
    """Value of the farmer's lot at the best market within reach, and the extra over the nearest listed market.
    Straight multiplication of reported modal prices: transport, loading, fees and quality grade are NOT included."""
    pick = ranked.get("best_nearby") or ranked.get("nearest")
    if not pick:
        return None
    nearest = ranked.get("nearest")
    out = {"quintals": quintals, "market": pick["market"], "price": round(pick["modal"]), "total": round(pick["modal"] * quintals), "km": pick["km"],
           "approx": pick["located_by"] == "district"}
    if nearest and nearest["market"] != pick["market"]:
        gap = round(pick["modal"] - nearest["modal"])
        out.update({"nearest": nearest["market"], "nearest_price": round(nearest["modal"]), "gap_per_quintal": gap, "gap_total": round(gap * quintals)})
    return out
