"""Proof behind the numbers: the lines of live data (and document passages) the answer was written from.

The UI lets the farmer tap a number in the answer and see the line it came from, the source and the time it was fetched. These are the
same CONTEXT lines the grounded-numbers guardrail checks against, so a tapped number is never matched to something the model made up."""
from __future__ import annotations

import re

MAX_ENTRIES = 80
MAX_LINE = 240
_HAS_NUMBER = re.compile(r"\d")


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[: MAX_LINE - 1] + "…"


def build_evidence(results: dict, kb_hits: list) -> list[dict]:
    out: list[dict] = []
    for name, r in results.items():
        if not r.ok:
            continue
        srcs = [{"name": s["name"], "url": s.get("url"), "fetched_at": s.get("fetched_at"), "stale": bool(s.get("stale"))} for s in r.sources]
        for line in r.text.splitlines():
            if _HAS_NUMBER.search(line):
                out.append({"kind": "live", "tool": name, "text": _clip(line.strip()), "sources": srcs})
    for h in kb_hits:
        for line in re.split(r"(?<=[.;:])\s+|\n", h.text):
            if _HAS_NUMBER.search(line) and len(line.strip()) > 6:
                out.append({"kind": "document", "tool": "documents", "text": _clip(line.strip()), "sources": [{"name": h.source, "url": None, "fetched_at": None, "stale": False}]})
    return out if len(out) <= MAX_ENTRIES else _spread(out)


def _spread(out: list[dict]) -> list[dict]:
    """Too many lines: keep live lines first (they are what the farmer asked about), documents after."""
    live = [e for e in out if e["kind"] == "live"]
    docs = [e for e in out if e["kind"] == "document"]
    return (live + docs)[:MAX_ENTRIES]
