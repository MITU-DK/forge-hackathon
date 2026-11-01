"""Soil pH, texture and organic carbon around the farm: ISRIC SoilGrids through its official WCS map service.

SoilGrids' REST API returned 503 on every request on 31 Oct 2026 (it is a beta service), but the same dataset is served by
maps.isric.org as a WCS: a point query returns a tiny GeoTIFF. Values are 250 m MODEL ESTIMATES, not a measurement of the field;
built-up and water pixels are masked, so the tool takes the median of the valid pixels within a few kilometres of the place.
When ISRIC does not answer, the tool says so; it never fills in a soil.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.decision import crop_fit as cf
from app.decision import soil_rules as sr
from app.tools import SourceUnavailable, ToolResult, guarded
from app.tools.crop_fit import table_key
from app.tools.geotiff import TiffError, read_tiff
from app.tools.http import get_bytes
from app.tools.location import resolve_location

log = logging.getLogger("tool.soil")
WCS = "https://maps.isric.org/mapserv"
DEPTH = "5-15cm"  # the layer a seedbed and young roots meet
HALF_BOX_DEG = 0.05  # about 5.5 km each way: 44 x 42 pixels, a few KB
TTL_S = 30 * 86400  # soil does not change within a month
TIMEOUT_S = 12.0
SCALE = {"phh2o": 10.0, "clay": 10.0, "sand": 10.0, "silt": 10.0, "soc": 10.0}  # SoilGrids d_factor: pH x10, g/kg x10 (clay/sand/silt -> %), dg/kg x10 (soc -> g/kg)
SOURCE_URL = "https://www.isric.org/explore/soilgrids"


def _wcs_params(prop: str, lon: float, lat: float) -> dict:
    d = HALF_BOX_DEG
    crs = "http://www.opengis.net/def/crs/EPSG/0/4326"
    return {
        "map": f"/map/{prop}.map", "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
        "COVERAGEID": f"{prop}_{DEPTH}_mean", "FORMAT": "image/tiff",
        "SUBSET": [f"long({lon - d},{lon + d})", f"lat({lat - d},{lat + d})"],
        "SUBSETTINGCRS": crs, "OUTPUTCRS": crs,
    }


def _grid(prop: str, lon: float, lat: float):
    f = get_bytes(WCS, _wcs_params(prop, lon, lat), ttl=TTL_S, timeout=TIMEOUT_S)
    try:
        return read_tiff(f.data), f
    except TiffError as e:
        raise SourceUnavailable(f"the soil map response could not be read ({e})") from e


def summarise(grids: dict[str, list[list[float]]]) -> dict | None:
    """Median of the properties over the pixels where the pH layer has data (0 = masked). None when no pixel is valid."""
    ph = grids["phh2o"]
    valid = [(r, c) for r, row in enumerate(ph) for c, v in enumerate(row) if v > 0]
    if not valid:
        return None

    def median(prop: str) -> float:
        return statistics.median(grids[prop][r][c] for r, c in valid) / SCALE[prop]

    sand, silt, clay = median("sand"), median("silt"), median("clay")
    texture = sr.usda_texture(sand, silt, clay)
    return {
        "ph": round(median("phh2o"), 1), "sand_pct": round(sand), "silt_pct": round(silt), "clay_pct": round(clay),
        "organic_carbon_g_kg": round(median("soc"), 1), "texture_class": texture,
        "texture_group": sr.texture_group(texture, clay), "valid_pixels": len(valid),
    }


_summaries: dict[tuple, tuple] = {}  # (lat, lon) -> (time, summary, Fetched)
_key_locks: dict[tuple, threading.Lock] = {}
_guard = threading.Lock()


def fetch_summary(loc: dict):
    """-> (summary dict or None when every pixel is masked, Fetched). Raises SourceUnavailable.

    Single flight: the soil tool and crop_fit both need the soil and run in parallel; without the per-place lock they fetched the
    same five layers at the same time (ten requests to a free service) and one of them timed out."""
    key = (round(loc["lat"], 3), round(loc["lon"], 3))
    with _guard:
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:
        hit = _summaries.get(key)
        if hit and time.time() - hit[0] < TTL_S:
            return hit[1], hit[2]
        with ThreadPoolExecutor(max_workers=len(SCALE)) as pool:
            fetched = dict(zip(SCALE, pool.map(lambda prop: _grid(prop, loc["lon"], loc["lat"]), SCALE)))
        result = (summarise({prop: grid for prop, (grid, _) in fetched.items()}), fetched["phh2o"][1])
        _summaries[key] = (time.time(), *result)
        return result


NO_DATA_NOTE = (
    "ISRIC SoilGrids has no soil data within about 5 km (the area is built-up or water). "
    "Say that the soil type and pH could not be checked here; give no soil advice."
)
UNAVAILABLE_NOTE = (
    "SOIL: the ISRIC SoilGrids map service did not answer, so soil type and pH could not be checked. Say that plainly and "
    "give no soil advice; the rest of the answer does not depend on it."
)


def _summary_line(where: str, s: dict, fetched) -> str:
    thin = f" Only {s['valid_pixels']} soil pixels are valid here (built-up areas are masked), so treat it as rough." if s["valid_pixels"] < sr.MIN_VALID_PIXELS else ""
    return (
        f"SOIL for {where} - ISRIC SoilGrids, fetched {fetched.fetched_at}. A 250 m MODEL ESTIMATE for {DEPTH} depth "
        f"(median of the valid pixels within about 5 km), not a measurement of the field: "
        f"pH {s['ph']:g}; sand {s['sand_pct']} %, silt {s['silt_pct']} %, clay {s['clay_pct']} %; organic carbon {s['organic_carbon_g_kg']:g} g/kg. "
        f"USDA texture class: {s['texture_class']} (group: {s['texture_group']}, approximate).{thin} "
        "A soil test or the local agriculture officer gives the real values. There is no verified good/poor threshold for organic carbon here: "
        "state the number only. Quote the SOIL FIT labels exactly (OPTIMAL, ACCEPTABLE, TOLERATED, NOT_TOLERATED): never upgrade one to a better word."
    )


def _crop_fit_line(crop, fit: dict, s: dict) -> str | None:
    bits = []
    if fit["ph"]:
        p = fit["ph"]
        bits.append(f"pH {s['ph']:g} is {p['label']} (optimum {p['optimal'][0]:g}-{p['optimal'][1]:g}, tolerated {p['absolute'][0]:g}-{p['absolute'][1]:g})")
    if fit["texture"]:
        bits.append(f"{s['texture_group']} texture is {fit['texture']}")
    return f"SOIL FIT for {crop.common} (FAO EcoCrop limits): " + "; ".join(bits) + "." if bits else None


def _card(where: str, s: dict) -> dict:
    return {
        "type": "soil", "title": f"Soil - {where}", "ph": s["ph"], "sand_pct": s["sand_pct"], "silt_pct": s["silt_pct"],
        "clay_pct": s["clay_pct"], "texture_class": s["texture_class"], "organic_carbon_g_kg": s["organic_carbon_g_kg"],
        "note": "Model estimate (250 m). A soil test gives the real values.",
    }


def get_soil(profile: dict, text: str = "") -> ToolResult:
    loc = resolve_location(profile)
    where = f"{loc['name']}, {loc['state']}".strip(", ")
    s, fetched = fetch_summary(loc)
    src = [{"name": "ISRIC SoilGrids (250 m model estimate)", "url": SOURCE_URL, "fetched_at": fetched.fetched_at, "stale": fetched.stale}]
    if s is None:
        return ToolResult(f"SOIL for {where}: {NO_DATA_NOTE}", True, src, [], {"soil": None})
    lines = [_summary_line(where, s, fetched)]
    crops = cf.load_crops()
    key = table_key(profile.get("crop", ""))
    facts: dict = {"location": {k: loc[k] for k in ("name", "state", "district", "lat", "lon")}, "soil": s}
    if key in crops:
        fit = sr.crop_soil_fit(crops[key], s["ph"], s["texture_group"])
        facts["crop_fit"] = fit
        line = _crop_fit_line(crops[key], fit, s)
        if line:
            lines.append(line)
    problems = sr.soil_problems(crops, s["ph"], s["texture_group"])
    if problems:
        lines.append("CROPS THIS SOIL DOES NOT SUIT (EcoCrop tolerance): " + "; ".join(f"{n} ({', '.join(w)})" for n, w in problems[:8]))
    return ToolResult("\n".join(lines), True, src, [_card(where, s)], facts)


def _soil_tool(text: str, profile: dict) -> ToolResult:
    try:
        return get_soil(profile, text)
    except SourceUnavailable as e:
        # Soil is an add-on: its outage must not mark the whole answer degraded or cost a retry. It is still said out loud.
        log.warning("soil unavailable: %s", type(e).__name__)
        return ToolResult(UNAVAILABLE_NOTE, True, [], [], {"soil": None})


soil = guarded("soil")(_soil_tool)
