"""THE ONLY PLACE A NEW PROBLEM NEEDS EDITING (plus app/tools/ and app/decision/ for live data + rules).

A Domain bundles: persona, intents (router labels), tools per intent, hard-limit validators,
safe fallback, suggested prompts and UI languages. The orchestrator, LLM client, memory, API and UI are generic.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Callable

from app.core.config import get_settings
from app.guardrails.validators import Validator
from app.tools import ToolResult

Tool = Callable[[str, dict], ToolResult]  # (user text, profile) -> ToolResult


@dataclass
class Intent:
    name: str
    description: str  # shown to the router LLM; also used for keyword fallback
    instructions: str  # extra system instructions when this intent is chosen
    keywords: list[str] = field(default_factory=list)


@dataclass
class Domain:
    name: str
    title: str
    tagline: str
    persona: str
    intents: list[Intent]
    validators: list[Validator]
    safe_fallback: str
    suggested_prompts: list[str]
    languages: dict[str, str] = field(default_factory=lambda: {"en": "English"})
    tools: dict[str, Tool] = field(default_factory=dict)
    intent_tools: dict[str, list[str]] = field(default_factory=dict)  # run in parallel
    post_tools: dict[str, list[str]] = field(default_factory=dict)  # run after, in order (cross-checks)
    vision_prompt: str = "Describe what is visible in this photo. Be factual and brief."
    vision_parser: Callable[[str], dict | None] | None = None  # vision text -> {summary, card} or None
    vision_language_note: Callable[[str], str] | None = None  # language name -> extra vision-prompt text so the phrases come back in the farmer's language
    tool_help: dict[str, str] = field(default_factory=dict)  # one line per tool, shown to the planner
    profile_fields: list[dict] = field(default_factory=list)  # UI profile form: {key,label,placeholder,options?}
    safe_fallback_by_lang: dict[str, str] = field(default_factory=dict)  # same text as safe_fallback, in the farmer's language
    stt_prompts: dict[str, str] = field(default_factory=dict)  # speech-to-text vocabulary hint per language (words a farmer says)
    suggested_prompts_by_lang: dict[str, list[str]] = field(default_factory=dict)  # same questions in the farmer's language; others fall back to English
    # (profile, {place, crop} the router found in the question, question) -> profile values the QUESTION overrides for this turn
    soft_validators: list[Validator] = field(default_factory=list)  # style rules: one rewrite is asked for, then the draft is delivered anyway
    # (question, profile) -> the text to search documents with: native-script terms in the documents' language, the resolved crop
    retrieval_query: Callable[[str, dict], str] | None = None
    knowledge_intents: set[str] = field(default_factory=set)  # intents that retrieve documents; empty = all (an MSP paragraph has no place in a rain answer)
    label_text: dict[str, dict[str, str]] = field(default_factory=dict)  # language -> {RULE_LABEL: phrase in that language}
    question_overrides: Callable[[dict, dict, str], dict] | None = None


def load_domain() -> Domain:
    mod = importlib.import_module(f"app.domain.{get_settings().domain}")
    return mod.DOMAIN
