"""Crop-specific advice from the live forecast + cited rules (decision/crop_rules.py). Runs after the weather tool."""
from __future__ import annotations

from app.decision.crop_rules import crop_advice, has_rules
from app.tools import PlaceNotFound, SourceUnavailable, ToolResult, guarded
from app.tools.mandi import crop_key
from app.tools.weather import get_weather


def get_crop_stage(profile: dict) -> ToolResult:
    crop = crop_key(profile.get("crop") or "")
    if not crop:
        return ToolResult("CROP RULES: no crop selected, so no crop-specific rules were checked.")
    try:
        w = get_weather(profile)  # same URL + params as the weather tool: served from the http cache, no second live call
    except (SourceUnavailable, PlaceNotFound):
        return ToolResult("CROP RULES: not checked because the forecast is unavailable.")
    state = w.facts["location"]["state"]
    if not has_rules(crop, state):
        return ToolResult(f"CROP RULES: no verified crop-specific rule is available for {crop.title()} in {state}. Do not invent crop-stage advice.", sources=[], facts={"crop_advice": []})
    adv = crop_advice(crop, state, w.facts["days"])
    if not adv:
        return ToolResult(f"CROP RULES for {crop.title()} in {state}: no crop-specific risk triggered by the forecast.", facts={"crop_advice": []})
    lines = [f"CROP RULES for {crop.title()} in {state} (computed in code from the live forecast):"]
    lines += [f"- {a['code']}: {a['text']} Source: {a['source']}" for a in adv]
    return ToolResult("\n".join(lines), facts={"crop_advice": adv})


crop_stage = guarded("crop_stage")(lambda text, profile: get_crop_stage(profile))
