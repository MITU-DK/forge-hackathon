"""Planner: asks a small LLM which live tools a question needs, then validates the answer in code.

LLM proposes, code disposes: unknown tool names are dropped, and the tools the intent already
requires are always kept (the sell/hold signal needs mandi + weather). The planner can only ADD
tools (e.g. rain_history for a "will rain hurt my harvest" question). Any failure -> intent_tools.
"""
from __future__ import annotations

from pathlib import Path

from app.domain import Domain, Intent
from app.tools import NeedsInput

PLANNER_PROMPT = (Path(__file__).parent.parent / "llm" / "prompts" / "planner.md").read_text(encoding="utf-8")


class Planner:
    def __init__(self, llm, domain: Domain):
        self.llm, self.domain = llm, domain

    def _allowed(self) -> list[str]:
        post = {n for names in self.domain.post_tools.values() for n in names}
        return [n for n in self.domain.tools if n not in post]

    def _prompt(self, intent: Intent, profile: dict, allowed: list[str]) -> str:
        listing = "\n".join(f"- {n}: {self.domain.tool_help.get(n, '')}" for n in allowed)
        shown = {k: v for k, v in profile.items() if k in ("place", "crop")} or "not provided"
        return PLANNER_PROMPT.replace("{tools}", listing).replace("{intent}", intent.name).replace("{profile}", str(shown))

    def plan(self, text: str, profile: dict, intent: Intent) -> tuple[list[str], str, bool]:
        """Returns (tool_names, reason, used_fallback)."""
        base = list(self.domain.intent_tools.get(intent.name, []))
        allowed = self._allowed()
        try:
            data, _ = self.llm.chat_json(
                [{"role": "system", "content": self._prompt(intent, profile, allowed)}, {"role": "user", "content": text}],
                fast=True, temperature=0, max_tokens=120, offline="", primary_only=True,  # it can only add tools, so it is not worth ~10 s
            )
        except Exception:
            data = None
        picked = data.get("tools") if isinstance(data, dict) else None
        if not isinstance(picked, list):
            return base, "planner unavailable; using default tools for the intent", True
        extra = [n for n in picked if isinstance(n, str) and n in allowed and n not in base]
        return base + list(dict.fromkeys(extra)), str(data.get("reason", ""))[:120], False


RETRY_DELAY_S = 0.5


def retryable(outs: list[tuple]) -> list[str]:
    """Re-plan rule (code, not LLM): retry once a tool that crashed or whose live source was unreachable.
    Never retry NeedsInput or place-not-found: asking again cannot fix those. outs = (name, ToolResult|None, error|None, ms)."""
    names = []
    for name, tr, err, _ in outs:
        if isinstance(err, NeedsInput):
            continue
        if err is not None or (tr is not None and not tr.ok and "SOURCE UNAVAILABLE" in tr.text):
            names.append(name)
    return names
