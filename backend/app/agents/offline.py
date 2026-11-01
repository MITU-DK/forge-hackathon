"""Answers built only from real tool output, for when the LLM cannot be reached."""
from __future__ import annotations

import re

NOT_DATA_PREFIXES = ("MISSING", "TOOL FAILED")

_FAILURE = re.compile(r"^(\w+?) (?:SOURCE UNAVAILABLE|FAILED: place not found)")


# Sentences that are instructions to the model ("Say that in one or two short sentences."), not facts.
_INSTRUCTION = re.compile(r"\s*(?:Say|Tell|Ask|Do NOT|Never|Report|Lead with|Give|Mention|Use only)\b[^.]*\.")


def _plain_failure(text: str) -> str:
    """Offline answers show tool text to the farmer. Failure text carries the exception and request URL for the LLM and
    the logs, so it becomes one plain sentence; instructions meant for the model are dropped from the rest."""
    m = _FAILURE.match(text)
    if not m:
        return _INSTRUCTION.sub("", text)
    what = m.group(1).replace("_", " ").lower()
    if "place not found" in text[: m.end()]:
        return f"The place name could not be matched, so no {what} data is shown. Please give the nearest bigger town or the district name."
    return f"{what.capitalize()}: this live source is not reachable right now, so nothing is shown instead of a guess."


def offline_answer(tool_ctx: list[str], kb: list, missing: dict) -> str:
    """LLM unreachable: show the REAL live data we already fetched (no AI summary), or ask for missing input."""
    data_blocks = [t for t in tool_ctx if not t.startswith(NOT_DATA_PREFIXES)]
    if missing and not any(data_blocks):
        return "To help you I need: " + "; ".join(missing.values()) + "."
    if data_blocks:
        return "(AI summary unavailable - showing live data only)\n\n" + "\n\n".join(_plain_failure(t) for t in data_blocks)[:1800]
    if kb:
        return "(AI offline) Most relevant document text:\n" + kb[0].text[:400]
    return "I'm offline right now and have no live data for that. Please try again shortly."
