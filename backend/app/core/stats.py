"""In-process counters for GET /api/stats (reset on restart). Aggregates only: never user text, never keys."""
from __future__ import annotations

import threading
from collections import Counter

# Trace events counted per answer: counter name -> (agent, action or None, required ok value or None).
# `None` for action matches any action; `None` for ok means "do not look at ok".
_TRACE_COUNTERS: dict[str, tuple[str, str | None, bool | None]] = {
    "guardrail_rejections": ("Guardrail", "REJECTED", None),
    "guardrail_fallbacks": ("Guardrail", "fallback", None),
    "injection_attempts_flagged": ("Guardrail", "injection attempt flagged", None),
    "tool_failures": ("Tool", None, False),
    "replans": ("Planner", "replan", None),
}

_lock = threading.Lock()
_counts: Counter = Counter()
_languages: Counter = Counter()
_intents: Counter = Counter()


def _matches(step: dict, agent: str, action: str | None, ok: bool | None) -> bool:
    if step.get("agent") != agent:
        return False
    if action is not None and step.get("action") != action:
        return False
    return ok is None or step.get("ok", True) == ok


def record(result: dict, ms: int) -> None:
    """`result` is ChatResult.dict(); counts pipeline events from its trace."""
    trace = result.get("trace") or []
    with _lock:
        _counts["total_answers"] += 1
        _counts["degraded_answers"] += bool(result.get("degraded"))
        _counts["latency_total_ms"] += ms
        for name, (agent, action, ok) in _TRACE_COUNTERS.items():
            _counts[name] += sum(_matches(step, agent, action, ok) for step in trace)
        _languages[result.get("language") or "unknown"] += 1
        _intents[result.get("intent") or "unknown"] += 1


def snapshot() -> dict:
    with _lock:
        total = _counts["total_answers"]
        return {
            "total_answers": total,
            "degraded_answers": _counts["degraded_answers"],
            **{name: _counts[name] for name in _TRACE_COUNTERS},
            "avg_latency_ms": round(_counts["latency_total_ms"] / total) if total else 0,
            "languages_used": dict(_languages),
            "intents_used": dict(_intents),
        }


def reset() -> None:  # tests only
    with _lock:
        _counts.clear()
        _languages.clear()
        _intents.clear()
