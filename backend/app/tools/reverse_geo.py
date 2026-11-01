"""Coordinates -> a place name the other tools understand, from OpenStreetMap Nominatim (live, no key).

Lets a farmer who cannot type a village name tap "use my location". Nominatim gives the district and state reliably, and the
village or town when OpenStreetMap has one. Each candidate name is checked against the forward geocoder the weather and mandi
tools use, so the place handed back is one they can resolve; when the village is unknown the taluka, then the district, is used.
Coordinates are used for this one lookup only: they are not stored and not logged."""
from __future__ import annotations

from app.tools import PlaceNotFound
from app.tools.http import get_json
from app.tools.location import clean_admin_name, resolve_location

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
INDIA = (6.0, 38.0, 68.0, 98.5)  # lat_min, lat_max, lon_min, lon_max: a rough box, so a stray coordinate is not looked up
LOCAL_NAME_KEYS = ("village", "town", "city", "suburb", "municipality", "county")  # most local first


def _in_india(lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = INDIA
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _local_names(addr: dict, district: str) -> list[str]:
    """Distinct place names from the address, most local first, without repeating the district."""
    names, seen = [], {district.lower()}
    for key in LOCAL_NAME_KEYS:
        name = clean_admin_name(addr.get(key))
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def locate(lat: float, lon: float) -> dict:
    """-> {place, name, state, district, source}. Raises PlaceNotFound (outside India / no usable name) or SourceUnavailable."""
    if not _in_india(lat, lon):
        raise PlaceNotFound("these coordinates are outside India")
    params = {"format": "jsonv2", "lat": round(lat, 3), "lon": round(lon, 3), "zoom": 12, "addressdetails": 1, "accept-language": "en"}
    fetched = get_json(NOMINATIM_URL, params, ttl=86400)
    addr = (fetched.data.get("address") if isinstance(fetched.data, dict) else None) or {}
    if addr.get("country_code") != "in":
        raise PlaceNotFound("these coordinates are not in India")
    state = addr.get("state", "")
    district = clean_admin_name(addr.get("state_district") or addr.get("county"))
    candidates = [f"{n}, {district}, {state}" if district else f"{n}, {state}" for n in _local_names(addr, district)]
    if district:
        candidates.append(f"{district}, {state}")
    for candidate in candidates:
        try:
            loc = resolve_location({"place": candidate})
        except PlaceNotFound:
            continue
        source = {"name": "OpenStreetMap Nominatim", "url": NOMINATIM_URL, "fetched_at": fetched.fetched_at, "stale": fetched.stale}
        return {"place": candidate, "name": loc["name"], "state": loc["state"], "district": loc["district"], "source": source}
    raise PlaceNotFound("no place name for these coordinates could be matched")
