"""Soil rules: USDA texture class from sand/silt/clay, the FAO EcoCrop texture group, and a crop's tolerance to both texture and pH.

Inputs are ISRIC SoilGrids estimates (250 m model predictions, not a measurement of the farmer's field). Deterministic code;
the LLM only explains the result. Group boundaries are approximate and labelled so.
"""
from __future__ import annotations

from app.decision.crop_fit import Crop, ph_fit

MIN_VALID_PIXELS = 3  # fewer valid soil pixels than this (a built-up area is masked in SoilGrids) -> say the estimate is thin


def usda_texture(sand: float, silt: float, clay: float) -> str:
    """USDA texture triangle (percent, summing to about 100)."""
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (clay < 7 and silt < 50 and silt + 2 * clay >= 30):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    if clay >= 40:
        return "clay"
    return "loam"  # the triangle leaves thin gaps from rounding; the middle class is the safe reading


def texture_group(cls: str, clay: float) -> str:
    """FAO EcoCrop words: light (sandy), medium (loamy), heavy (clayey). APPROXIMATE: EcoCrop gives no numeric limits."""
    if cls in ("sand", "loamy sand", "sandy loam"):
        return "light"
    return "heavy" if clay >= 35 else "medium"


def _words(s: str) -> set[str]:
    return {w.strip().lower() for w in (s or "").split(",") if w.strip()}


def texture_fit(c: Crop, group: str) -> str | None:
    """OPTIMAL / TOLERATED / NOT_TOLERATED against the crop's EcoCrop texture words; None if the database has none for it."""
    opt, tol = _words(c.texture), _words(c.texture_abs)
    if not opt and not tol:
        return None
    if "wide" in opt or group in opt:
        return "OPTIMAL"
    if "wide" in tol or group in tol:
        return "TOLERATED"
    return "NOT_TOLERATED"


def crop_soil_fit(c: Crop, ph: float, group: str) -> dict:
    return {"crop": c.key, "ph": ph_fit(c, ph), "texture": texture_fit(c, group)}


def soil_problems(crops: dict[str, Crop], ph: float, group: str) -> list[tuple[str, list[str]]]:
    """Crops that do not tolerate this soil's pH or texture -> [(crop, [reasons])]."""
    out = []
    for c in crops.values():
        why = []
        p = ph_fit(c, ph)
        if p and p["label"] == "OUTSIDE_TOLERANCE":
            why.append(f"pH {ph:g} is outside the tolerated {p['absolute'][0]:g}-{p['absolute'][1]:g}")
        if texture_fit(c, group) == "NOT_TOLERATED":
            why.append(f"{group} soil texture is not tolerated")
        if why:
            out.append((c.common, why))
    return out
