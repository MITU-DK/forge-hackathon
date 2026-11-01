"""Place name -> lat/lon/state/district via Open-Meteo's free geocoding API (live, no key)."""
from __future__ import annotations

import difflib
import re
import unicodedata

from app.tools import NeedsInput, PlaceNotFound
from app.tools.http import get_json

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEO_GET_URL = "https://geocoding-api.open-meteo.com/v1/get"

# The geocoder only matches a native-script name when `language` is that script's language (verified live on 31 Oct 2026:
# "नाशिक" finds Nashik with language=mr and nothing with en/hi; "लुधियाना" works with hi). Devanagari is tried as
# Marathi, then Hindi. Results then carry native-script state/district names, so the chosen place is re-read in English
# by id: the mandi and weather tools are keyed on English names.
SCRIPT_LANGS = [
    ("\u0900", "\u097f", ["mr", "hi"]), ("\u0a80", "\u0aff", ["gu"]), ("\u0a00", "\u0a7f", ["pa"]), ("\u0b80", "\u0bff", ["ta"]),
    ("\u0c00", "\u0c7f", ["te"]), ("\u0c80", "\u0cff", ["kn"]), ("\u0980", "\u09ff", ["bn"]), ("\u0d00", "\u0d7f", ["ml"]),
]


def _search_langs(query: str) -> list[str]:
    for lo, hi, langs in SCRIPT_LANGS:
        if any(lo <= ch <= hi for ch in query):
            return langs
    return ["en"]


# Indian states / UTs, lower-case, including common alternative spellings. If the farmer TYPES one of these, results from
# other states must never be used silently (e.g. "Ahmednagar, Maharashtra" must not become Himatnagar, Gujarat).
INDIA_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "chattisgarh", "goa", "gujarat", "haryana",
    "himachal pradesh", "jharkhand", "karnataka", "kerala", "keralam", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana",
    "tripura", "uttar pradesh", "uttarakhand", "uttaranchal", "west bengal", "delhi", "new delhi", "puducherry",
    "pondicherry", "chandigarh", "ladakh", "jammu and kashmir", "andaman and nicobar islands", "lakshadweep",
}


# Words that say nothing about WHICH place: "Pune district", "Khed taluka", "Nashik, India".
FILLER = {"india", "district", "dist", "tehsil", "taluka", "taluk", "tal", "village", "city"}


# Names farmers still type -> the name the geocoder actually knows. Each target was checked live on 31 Oct 2026 and
# returns the right state ("Gurgaon" alone otherwise resolves to a village in Hingoli, Maharashtra; "Panaji" has no Goa entry).
GEO_NAMES = {
    "ahmednagar": "Ahilyanagar", "panaji": "Panjim", "bangalore": "Bengaluru", "allahabad": "Prayagraj", "gurgaon": "Gurugram",
    # District names the geocoder does not list (checked live 31 Oct 2026): the district's own town, or the old name.
    "buldhana": "Buldana", "raigad": "Alibag", "chhatrapati sambhajinagar": "Aurangabad", "sambhajinagar": "Aurangabad",
    # Old and alternate spellings. Without these the geocoder returned a village ("Nasik" -> Vani, "Banaras" -> a Chhattisgarh
    # hamlet), an airport ("Belgaum", "Cochin", "Trivandrum") or nothing ("Gulbarga", "Shimoga"). Each target checked live.
    "nasik": "Nashik", "banaras": "Varanasi", "benares": "Varanasi", "gulbarga": "Kalaburagi", "shimoga": "Shivamogga", "bombay": "Mumbai",
    "calcutta": "Kolkata", "madras": "Chennai", "belgaum": "Belagavi", "trivandrum": "Thiruvananthapuram", "cochin": "Kochi",
    "mysore": "Mysuru", "mangalore": "Mangaluru",
}


# Maharashtra districts and big cities as farmers write them in Marathi/Hindi -> the English name the geocoder knows.
# Needed because the geocoder lacks many native spellings (checked live 31 Oct 2026: Satara, Sangli, Indore returned nothing).
NATIVE_PLACES = {
    "अहिल्यानगर": "Ahilyanagar", "अहमदनगर": "Ahilyanagar", "अकोला": "Akola", "अमरावती": "Amravati", "छत्रपती संभाजीनगर": "Aurangabad", "बीड": "Beed",
    "भंडारा": "Bhandara", "बुलढाणा": "Buldana", "चंद्रपूर": "Chandrapur", "धुळे": "Dhule", "गडचिरोली": "Gadchiroli", "गोंदिया": "Gondia",
    "हिंगोली": "Hingoli", "जळगाव": "Jalgaon", "जालना": "Jalna", "मुंबई": "Mumbai", "नांदेड": "Nanded", "नंदुरबार": "Nandurbar",
    "धाराशिव": "Dharashiv", "उस्मानाबाद": "Dharashiv", "पालघर": "Palghar", "परभणी": "Parbhani", "रायगड": "Alibag", "रत्नागिरी": "Ratnagiri",
    "सांगली": "Sangli", "सातारा": "Satara", "सिंधुदुर्ग": "Sindhudurg", "ठाणे": "Thane", "वर्धा": "Wardha", "वाशीम": "Washim", "यवतमाळ": "Yavatmal",
    "लासलगाव": "Lasalgaon", "मालेगाव": "Malegaon", "बारामती": "Baramati", "पंढरपूर": "Pandharpur", "कराड": "Karad", "शिर्डी": "Shirdi", "सिन्नर": "Sinnar",
    "इंदौर": "Indore", "भोपाल": "Bhopal", "लखनऊ": "Lucknow", "पटना": "Patna", "अमृतसर": "Amritsar", "चंडीगढ़": "Chandigarh", "दिल्ली": "Delhi",
    "अहमदाबाद": "Ahmedabad", "हैदराबाद": "Hyderabad", "बेंगलुरु": "Bengaluru", "जयपुर": "Jaipur", "आगरा": "Agra", "लुधियाना": "Ludhiana",
}


NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
# Villages and hamlets are too ambiguous across states for a name alone ("कळवण" -> hamlets in Rajasthan, not Kalwan in Nashik), so the
# fallback accepts only towns, cities and administrative areas, and a typed state must match.
FALLBACK_TYPES = {"town", "city", "administrative", "suburb", "municipality"}
_ADMIN_WORD = re.compile(r"\s+(taluka|tehsil|tahsil|subdistrict|sub-district|district|mandal|block|urban)$", re.I)


def clean_admin_name(name: str | None) -> str:
    return _ADMIN_WORD.sub("", (name or "").strip()).strip()


def _nominatim_fallback(query: str, rest: list[str]) -> dict | None:
    """Second place finder for Devanagari names Open-Meteo does not know (OpenStreetMap carries the Marathi/Hindi name)."""
    params = {"q": query, "countrycodes": "in", "format": "jsonv2", "addressdetails": 1, "accept-language": "en", "limit": 5}
    f = get_json(NOMINATIM_SEARCH, params, ttl=86400)
    found = f.data if isinstance(f.data, list) else []
    candidates = [x for x in found if x.get("type") in FALLBACK_TYPES and (x.get("address") or {}).get("country_code") == "in"]
    wanted = [t for t in rest if t in INDIA_STATES]
    if wanted:
        candidates = [x for x in candidates if any(w in (x["address"].get("state") or "").lower() for w in wanted)]
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x.get("importance") or 0)
    address = best["address"]
    return {
        "name": clean_admin_name(best.get("name")) or query,
        "state": address.get("state", ""),
        "district": clean_admin_name(address.get("state_district") or address.get("county")),
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "source": {"name": "OpenStreetMap Nominatim", "url": f.url, "fetched_at": f.fetched_at, "stale": f.stale},
    }


def _district_hints(rest: list[str]) -> list[str]:
    """Typed parts that are neither a state nor filler: the district/town the farmer named."""
    hints = []
    for t in rest:
        if t in INDIA_STATES:
            continue
        words = [w for w in t.split() if w not in FILLER]
        if words:
            hints.append(" ".join(words))
    return hints


def _ascii(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().strip()


def _name_match(query: str, r: dict) -> float:
    """How much the geocoder's result looks like what the farmer typed (1.0 = same word). The typed name may be the whole name,
    a word inside it ("Sindhudurg" in "Sindhudurg Airport") or the result's district ("Belgaum" -> district "Belgaum")."""
    q, name = _ascii(query), _ascii(r.get("name"))
    district = _ascii(r.get("admin2")).replace("district", "").strip()
    if q and (q in name.split() or q == district):
        return 1.0
    return difflib.SequenceMatcher(None, q, name).ratio()


MIN_NAME_MATCH = 0.75  # "Nasik" vs "Nashik" is 0.91; "Nasik" vs "Vani" is 0.0


def _score(r: dict, rest: list[str]) -> int:
    hay = f"{r.get('admin1', '')} {r.get('admin2', '')}".lower()
    return sum(1 for t in rest if t and t in hay)


def _rank_key(rest: list[str]):
    """The geocoder orders by name match, not importance: "Panaji" returns a Gujarat village before Goa's capital and
    "Ahmednagar" a Gujarat town before the Maharashtra city. Rank by state/district hint first, then by population."""
    return lambda r: (_score(r, rest), r.get("population") or 0)


def _from_profile_coordinates(profile: dict) -> dict | None:
    if profile.get("lat") is None or profile.get("lon") is None or not profile.get("state"):
        return None
    return {
        "name": profile.get("place") or "your location",
        "state": profile["state"],
        "district": profile.get("district", ""),
        "lat": float(profile["lat"]),
        "lon": float(profile["lon"]),
        "source": None,
    }


def _search_query(first_part: str) -> str:
    query = " ".join(w for w in first_part.split() if w.lower() not in FILLER) or first_part  # "Raipur district" -> "Raipur"
    return NATIVE_PLACES.get(query, GEO_NAMES.get(query.lower(), query))


def _search(query: str):
    """-> (results, Fetched of the last search). Tries each language that fits the script of the query until one has results."""
    results, fetched = [], None
    for lang in _search_langs(query):
        fetched = get_json(GEO_URL, {"name": query, "count": 10, "language": lang, "format": "json", "countryCode": "IN"}, ttl=86400)
        results = (fetched.data or {}).get("results") or [] if isinstance(fetched.data, dict) else []
        if results:
            break
    return results, fetched


def _pick_by_name(query: str, results: list[dict], best: dict, rest: list[str], typed: str) -> dict:
    """The top hit is not what was typed ("Nasik" -> a village called Vani): prefer a result that is named like the query."""
    if _name_match(query, best) >= MIN_NAME_MATCH:
        return best
    named = [r for r in results if _name_match(query, r) >= MIN_NAME_MATCH]
    if not named:
        raise PlaceNotFound(f"{typed}: the place finder only matched it to '{best.get('name')}', which is a different name; ask for the nearest bigger town or the district name")
    return max(named, key=_rank_key(rest))


def _check_typed_state_and_district(best: dict, rest: list[str], typed: str) -> None:
    """A typed state or district must match the result: otherwise the farmer would get another place's weather and prices."""
    wanted = [t for t in rest if t in INDIA_STATES]
    if wanted and not any(w in (best.get("admin1") or "").lower() for w in wanted):
        raise PlaceNotFound(f"{typed} in {', '.join(wanted)}: the place finder has no match there; ask for the nearest bigger town or the district name")
    # "Sawantwadi, Sindhudurg" must not become the only Sawantwadi the geocoder knows (a village in Latur).
    hints = _district_hints(rest)
    where = f"{best.get('admin1', '')} {best.get('admin2', '')} {best.get('name', '')}".lower()
    if hints and not any(h in where for h in hints):
        raise PlaceNotFound(f"{typed} in {', '.join(hints)}: the place finder only knows {typed} in {best.get('admin2') or best.get('admin1')}; ask for the nearest bigger town")


def _english_record(best: dict, typed: str):
    """For a native-script hit: the English names of the same place (the mandi and weather tools are keyed on English names)."""
    g = get_json(GEO_GET_URL, {"id": best["id"], "language": "en", "format": "json"}, ttl=86400)
    if not isinstance(g.data, dict) or not g.data.get("admin1"):
        raise PlaceNotFound(f"{typed}: the place finder has no English record for it; ask for the nearest bigger town")
    return {**best, **{k: g.data.get(k) for k in ("name", "admin1", "admin2")}}, g


def resolve_location(profile: dict) -> dict:
    """Returns {name,state,district,lat,lon,source}. Raises NeedsInput / PlaceNotFound / SourceUnavailable."""
    given = _from_profile_coordinates(profile)
    if given:
        return given
    place = (profile.get("place") or "").strip()
    if not place:
        raise NeedsInput("place", "the nearest town, village or district of the farm")
    parts = [p.strip() for p in place.split(",") if p.strip()]
    typed = parts[0]
    query = _search_query(typed)
    native_script = _search_langs(query) != ["en"]
    results, fetched = _search(query)
    if not results:
        if native_script:  # a Devanagari (or other native-script) name the first finder lacks
            alt = _nominatim_fallback(query, [p.lower() for p in parts[1:]])
            if alt:
                return alt
        raise PlaceNotFound(place)
    rest = [p.lower() for p in parts[1:]]
    if typed in NATIVE_PLACES:  # searched under the English name: a Devanagari state/district hint cannot match English records
        rest = [p for p in rest if p.isascii()]
    best = max(results, key=_rank_key(rest))
    if not native_script:
        best = _pick_by_name(query, results, best, rest, typed)
    _check_typed_state_and_district(best, rest, typed)
    if native_script:
        best, fetched = _english_record(best, typed)
    return {
        "name": best.get("name", typed),
        "state": best.get("admin1", ""),
        "district": best.get("admin2", ""),
        "lat": best["latitude"],
        "lon": best["longitude"],
        "source": {"name": "Open-Meteo geocoding", "url": fetched.url, "fetched_at": fetched.fetched_at, "stale": fetched.stale},
    }
