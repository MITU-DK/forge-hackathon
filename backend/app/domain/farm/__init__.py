"""Theme 2, Problem 1: Multilingual Agritech & Resource Advisory Assistant.

A decision agent (not a chatbot): it calls LIVE weather + soil (Open-Meteo) and LIVE mandi prices
(data.gov.in Agmarknet), computes risk flags and a sell/hold signal IN CODE, and the LLM only explains
the result in the farmer's language. Every number must come from a tool (grounded_numbers guardrail).

Layout: persona.py (who the model is), intents.py (what it can be asked), rules.py + checks.py (guardrails),
retrieval.py (document search and question-overrides-profile), labels.py and localized.py (per-language texts).
This file only assembles them.
"""
from __future__ import annotations

from app.domain import Domain
from app.domain.diagnosis import VISION_PROMPT, language_note, parse_diagnosis
from app.domain.farm.intents import INTENTS
from app.domain.farm.labels import LABEL_TEXT
from app.domain.farm.localized import (
    LANGUAGES,
    SAFE_FALLBACK,
    SAFE_FALLBACK_BY_LANG,
    STT_PROMPTS,
    SUGGESTED_PROMPTS,
    SUGGESTED_PROMPTS_BY_LANG,
)
from app.domain.farm.persona import PERSONA
from app.domain.farm.retrieval import question_overrides, retrieval_query
from app.domain.farm.rules import HARD_VALIDATORS, SOFT_VALIDATORS
from app.tools.best_market import best_market
from app.tools.crop_fit import crop_fit
from app.tools.crop_stage import crop_stage
from app.tools.farm_plan import farm_plan
from app.tools.mandi import ALIASES, cross, mandi
from app.tools.rain_history import rain_history
from app.tools.soil import soil
from app.tools.weather import weather

__all__ = ["DOMAIN", "question_overrides", "retrieval_query"]

# One name per commodity for the crop picker: no "Soybean" AND "Soyabean".
CROP_OPTIONS = sorted({k.title() for k in {v: k for k, v in reversed(ALIASES.items())}.values()})

DOMAIN = Domain(
    name="farm",
    title="KisaanMitra",
    tagline="Live weather, soil and mandi prices, clear advice in your language.",
    persona=PERSONA,
    intents=INTENTS,
    tools={"soil": soil, "crop_fit": crop_fit, "weather": weather, "mandi": mandi, "cross_check": cross, "rain_history": rain_history, "crop_stage": crop_stage, "farm_plan": farm_plan, "best_market": best_market},
    intent_tools={
        "sell_or_hold": ["mandi", "weather"],
        "market_price": ["mandi"],
        "weather_advice": ["weather", "rain_history"],
        "sow_or_crop_care": ["weather", "rain_history", "crop_fit", "soil"],
        "crop_suggest": ["crop_fit", "weather", "soil"],
        "farm_plan": ["farm_plan"],
        "best_market": ["best_market", "mandi"],
    },
    post_tools={"sell_or_hold": ["cross_check"], "weather_advice": ["crop_stage"], "sow_or_crop_care": ["crop_stage"]},
    tool_help={
        "weather": "7-day forecast, soil moisture, risk flags, spray-safe days, irrigation advice",
        "mandi": "last 7 days of mandi prices for the crop, nearby markets, sell/hold signal",
        "rain_history": "rain in the last 7/14/30 days vs next 7 days; dry-spell, irrigation-urgent and waterlogging alerts",
        "soil": "soil pH, texture and organic carbon around the place (ISRIC SoilGrids model estimate) and whether the farmer's crop tolerates them",
        "farm_plan": "ranked list of today's actions (weather risk, water, spraying, crop rules, market) from all live sources at once",
        "best_market": "the highest-paying mandis for the crop with straight-line distance from the farm (no transport cost)",
        "crop_fit": "which crops fit this place's climate now, best sowing windows for the farmer's crop, harvest window from a sowing date",
    },
    label_text=LABEL_TEXT,
    retrieval_query=retrieval_query,
    knowledge_intents={"scheme_info", "general", "market_price", "sell_or_hold", "sow_or_crop_care", "crop_suggest"},
    question_overrides=question_overrides,
    vision_prompt=VISION_PROMPT,
    vision_parser=parse_diagnosis,
    vision_language_note=language_note,
    validators=HARD_VALIDATORS,
    soft_validators=SOFT_VALIDATORS,
    safe_fallback=SAFE_FALLBACK,
    safe_fallback_by_lang=SAFE_FALLBACK_BY_LANG,
    stt_prompts=STT_PROMPTS,
    suggested_prompts=SUGGESTED_PROMPTS,
    suggested_prompts_by_lang=SUGGESTED_PROMPTS_BY_LANG,
    languages=LANGUAGES,
    profile_fields=[
        {"key": "place", "label": "Village / town (e.g. Nashik, Maharashtra)", "placeholder": "Nashik, Maharashtra"},
        {"key": "crop", "label": "Crop", "placeholder": "Onion", "options": CROP_OPTIONS},
        {"key": "quantity", "label": "Quantity to sell, in quintals (optional)", "placeholder": "20"},
        {"key": "sowing_date", "label": "Sowing date (optional, day/month/year)", "placeholder": "15/07/2026"},
    ],
)
