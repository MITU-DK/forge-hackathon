"""Hard rules (safety, correctness) fall back to the safe answer; soft rules (style) get one rewrite, then the draft is delivered."""
from app.agents.orchestrator import Orchestrator
from app.guardrails.validators import forbid_patterns, grounded_numbers
from tests.helpers import FakeLLM, StubStore
from tests.test_pipeline import domain, good


def orch(answers):
    d = domain(["good"])
    d.validators = [grounded_numbers()]
    d.soft_validators = [forbid_patterns({r"\bclumsy\b": "Do not say clumsy."})]
    return Orchestrator(d, FakeLLM(answers), StubStore())


def actions(r):
    return [t.action for t in r.trace if t.agent == "Guardrail"]


def test_a_persistent_soft_violation_is_delivered_after_one_rewrite_not_replaced_by_the_fallback():
    r = orch(["Max 33 C but clumsy.", "Still clumsy, max 33 C."]).run("will it rain?", profile={"place": "Nashik"})
    assert "clumsy" in r.answer and r.answer != "SAFE"
    assert actions(r) == ["REJECTED", "passed with style notes"] and r.warnings == []


def test_a_soft_violation_that_the_rewrite_fixes_passes_normally():
    r = orch(["Max 33 C but clumsy.", "Max 33 C."]).run("will it rain?", profile={"place": "Nashik"})
    assert actions(r) == ["REJECTED", "passed"] and r.answer == "Max 33 C."


def test_a_persistent_hard_violation_still_falls_back_to_the_safe_answer():
    r = orch(["Rain will be 85 mm.", "Rain will be 85 mm again.", "Still 85 mm."]).run("will it rain?", profile={"place": "Nashik"})
    assert r.answer == "SAFE" and actions(r)[-1] == "fallback"


def test_hard_and_soft_violations_together_are_both_reported_to_the_model_for_the_rewrite():
    llm = FakeLLM(["Rain 85 mm, clumsy.", "Max 33 C."])
    d = domain(["good"])
    d.validators, d.soft_validators = [grounded_numbers()], [forbid_patterns({r"\bclumsy\b": "Do not say clumsy."})]
    Orchestrator(d, llm, StubStore()).run("will it rain?", profile={"place": "Nashik"})
    critique = llm.last_messages[-1]["content"]
    assert "not in the provided data" in critique and "Do not say clumsy" in critique
