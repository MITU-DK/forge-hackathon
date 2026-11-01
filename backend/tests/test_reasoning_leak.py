"""A model that answers the correction note instead of the farmer prints its own reasoning. That must never reach the farmer."""
import pytest

from app.agents.orchestrator import Orchestrator
from app.domain.farm import DOMAIN
from app.guardrails.validators import run_all
from tests.helpers import FakeLLM, StubStore
from tests.test_chaos import _fast_and_keyed, ask, world  # noqa: F401

LEAKS = [
    "1. Analyze the Request: The user is pointing out violations in my previous response based on a strict set of rules.",
    "Violation 1: I used Rs for a soil moisture figure. I need to use the correct unit.",
    "Let me re-read carefully: the rule says name only the data source. Wait, I should just say it plainly.",
    "7.9 % -> OK and 5000 -> OK, let's check the other numbers.",
    "Constraints: plain text, short sentences.",
]
REAL = [
    "Onion price is Rs 4300 per quintal at Nashik (data.gov.in Agmarknet, 2026-11-01). No strong signal to sell or wait.",
    "कांद्याचा भाव 4300 रुपये प्रति क्विंटल आहे (data.gov.in Agmarknet, 2026-11-01).",
    "The user interface shows rain on Tuesday, so wait to spray.",  # 'the user interface' is not the model talking about the user
]


@pytest.mark.parametrize("text", LEAKS)
def test_reasoning_text_is_a_hard_violation(text):
    assert any("reasoning" in v.lower() for v in run_all(DOMAIN.validators, text, ""))


@pytest.mark.parametrize("text", REAL)
def test_real_answers_are_not_flagged_as_reasoning(text):
    assert not any("reasoning" in v.lower() for v in run_all(DOMAIN.validators, text, ""))


def test_a_leaked_reasoning_answer_is_replaced_by_the_safe_answer_not_shown():
    http_world = world(set())
    import httpx
    from app.tools import http
    http._client = httpx.Client(transport=httpx.MockTransport(http_world))
    llm = FakeLLM([LEAKS[0]], route="sell_or_hold")
    r = Orchestrator(DOMAIN, llm, StubStore()).run("should I sell onion?", profile={"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert "Analyze the Request" not in r.answer and "violation" not in r.answer.lower()
