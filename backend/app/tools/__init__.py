"""Tool contract used by the orchestrator.

    tool(text: str, profile: dict) -> ToolResult

Rules: tools return REAL data from a live source or an explicit
failure. They never invent values. Recoverable problems are expressed as
exceptions that `guarded()` turns into a clear ToolResult(ok=False):

    NeedsInput(field)   -> the agent asks the user for that field
    PlaceNotFound       -> the agent asks for a better place name
    SourceUnavailable   -> the agent says the live source is down (no guessing)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import wraps
from urllib.parse import urlparse

# Failure text goes to the LLM, which then repeats it to the farmer: it must name the SOURCE, never a hostname or URL.
_SOURCE_NAMES = {
    "geocoding-api.open-meteo.com": "the Open-Meteo place finder",
    "api.open-meteo.com": "Open-Meteo weather",
    "api.data.gov.in": "data.gov.in Agmarknet",
    "data.gov.in": "data.gov.in Agmarknet",
    "nominatim.openstreetmap.org": "the OpenStreetMap place lookup",
}
_URL = re.compile(r"https?://\S+")
_HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|org|in|net|gov)\b", re.I)


def _source_name(host: str) -> str:
    return _SOURCE_NAMES.get(host.lower(), "the live source")


def safe_failure_text(message: str) -> str:
    """Drop URLs (they can carry API-key query strings) and replace each hostname by the name of the source."""
    # By URL first: that keeps WHICH source failed while dropping the query string.
    by_url = _URL.sub(lambda m: _source_name(urlparse(m.group(0).strip("'\"")).hostname or ""), message)
    return _HOST.sub(lambda m: _source_name(m.group(0)), by_url).strip()


class NeedsInput(Exception):
    def __init__(self, field_name: str, hint: str = ""):
        super().__init__(field_name)
        self.field = field_name
        self.hint = hint or field_name


class PlaceNotFound(Exception):
    pass


class SourceUnavailable(Exception):
    pass


@dataclass
class ToolResult:
    text: str  # goes into the LLM CONTEXT block: facts only
    ok: bool = True
    sources: list[dict] = field(default_factory=list)  # {name, url, fetched_at, stale}
    cards: list[dict] = field(default_factory=list)  # UI cards {type, title, ...}
    facts: dict = field(default_factory=dict)  # structured output for other tools / UI


def _place_not_found(name: str, error: Exception) -> ToolResult:
    return ToolResult(
        f"{name.upper()} FAILED: place not found ({error}). This is NOT a network problem: the place name could not be matched. "
        "Ask the user for the nearest bigger town or the district name.",
        ok=False,
    )


def _source_unavailable(name: str, error: Exception) -> ToolResult:
    return ToolResult(
        f"{name.upper()} SOURCE UNAVAILABLE ({safe_failure_text(str(error))}). Do NOT guess these values; "
        "tell the user this live source is not reachable right now.",
        ok=False,
    )


def guarded(name: str):
    """Turn a tool's recoverable exceptions into a ToolResult(ok=False). NeedsInput passes through to the orchestrator."""

    def decorate(fn):
        @wraps(fn)
        def wrapper(text: str, profile: dict) -> ToolResult:
            try:
                return fn(text, profile)
            except PlaceNotFound as e:
                return _place_not_found(name, e)
            except SourceUnavailable as e:
                return _source_unavailable(name, e)

        return wrapper

    return decorate
