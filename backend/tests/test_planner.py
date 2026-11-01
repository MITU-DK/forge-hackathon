from app.agents.planner import Planner
from app.domain import Intent
from app.domain.farm import DOMAIN
from app.llm.client import LLMResult

SELL = Intent("weather_advice", "", "")


class PlanLLM:
    def __init__(self, data=None, boom=False):
        self.data, self.boom = data, boom

    def chat_json(self, msgs, **kw):
        if self.boom:
            raise TimeoutError("slow")
        return self.data, LLMResult("{}", "primary")


def plan(llm, intent="weather_advice"):
    return Planner(llm, DOMAIN).plan("will rain hurt my harvest and what are prices?", {"place": "Nashik"}, Intent(intent, "", ""))


def test_valid_plan_adds_tools_in_parallel_set():
    names, why, fb = plan(PlanLLM({"tools": ["weather", "mandi", "rain_history"], "reason": "rain and prices"}))
    assert names == ["weather", "rain_history", "mandi"] and why == "rain and prices" and not fb


def test_invalid_json_falls_back_to_intent_tools():
    for bad in (None, {"tools": "weather"}, {"nope": 1}):
        names, _, fb = plan(PlanLLM(bad))
        assert names == DOMAIN.intent_tools["weather_advice"] and fb


def test_llm_exception_falls_back():
    names, _, fb = plan(PlanLLM(boom=True), "sell_or_hold")
    assert names == ["mandi", "weather"] and fb


def test_unknown_and_post_tools_dropped_and_core_tools_kept():
    names, _, _ = plan(PlanLLM({"tools": ["rm_rf", "cross_check"], "reason": "x"}), "sell_or_hold")
    assert names == ["mandi", "weather"]


# ---- re-plan on failure -------------------------------------------------------
from app.agents import planner as planner_mod
from app.agents.orchestrator import Orchestrator
from app.tools import NeedsInput, ToolResult
from tests.helpers import FakeLLM, StubStore
from tests.test_pipeline import domain


def _run(tools):
    planner_mod.RETRY_DELAY_S = 0
    return Orchestrator(domain(list(tools), tools=tools), FakeLLM(["Max 33 C."]), StubStore()).run("will it rain?", profile={"place": "Nashik"})


DOWN = lambda: ToolResult("W SOURCE UNAVAILABLE (timeout). Do NOT guess", False)
UP = ToolResult("WEATHER max 33 C", True)


def test_retryable_picks_only_unreachable_or_crashed():
    outs = [("a", DOWN(), None, 1), ("b", None, RuntimeError("x"), 1), ("c", None, NeedsInput("place"), 1), ("d", ToolResult("FAILED: place not found", False), None, 1), ("e", UP, None, 1)]
    assert planner_mod.retryable(outs) == ["a", "b"]


def test_transient_failure_recovers_and_is_not_degraded():
    n = {"i": 0}

    def flaky(text, profile):
        n["i"] += 1
        return DOWN() if n["i"] == 1 else UP

    r = _run({"flaky": flaky})
    replan = [t for t in r.trace if t.action == "replan"]
    assert n["i"] == 2 and replan and replan[0].ok and "none" in replan[0].detail
    assert not r.degraded and any(t.agent == "Tool" and t.ok for t in r.trace)


def test_persistent_failure_retries_once_then_reports_and_continues():
    n = {"i": 0}

    def dead(text, profile):
        n["i"] += 1
        return DOWN()

    r = _run({"dead": dead, "up": lambda t, p: UP})
    assert n["i"] == 2  # exactly one retry, no loop
    assert r.degraded and any(t.action == "replan" and not t.ok for t in r.trace)
    assert any(t.agent == "Tool" and t.action == "up" and t.ok for t in r.trace)


def test_crash_is_retried_once_and_survives():
    n = {"i": 0}

    def boom(text, profile):
        n["i"] += 1
        raise RuntimeError("bug")

    r = _run({"boom": boom})
    assert n["i"] == 2 and r.degraded and r.answer


def test_needs_input_is_never_retried():
    n = {"i": 0}

    def need(text, profile):
        n["i"] += 1
        raise NeedsInput("place", "town")

    r = _run({"need": need})
    assert n["i"] == 1 and not any(t.action == "replan" for t in r.trace)


def test_post_tools_skipped_when_a_source_failed_so_dead_source_is_not_requeried():
    calls = []

    def dead(text, profile):
        return DOWN()

    def post(text, profile):
        calls.append("post")
        return UP

    planner_mod.RETRY_DELAY_S = 0
    o = Orchestrator(domain(["dead"], ["post"], tools={"dead": dead, "post": post}), FakeLLM(["ok"]), StubStore())
    r = o.run("will it rain?", profile={"place": "Nashik"})
    assert calls == [] and any(t.action == "skip post-tools" for t in r.trace)
