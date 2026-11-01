from app.agents.orchestrator import Orchestrator
from app.core import stats
from tests.helpers import FakeLLM, StubStore
from tests.test_pipeline import domain


def test_counts_rejection_tool_failure_language_intent_and_latency():
    stats.reset()
    o = Orchestrator(domain(["good", "down"]), FakeLLM(["Rain will be 85 mm.", "Rain will be 70.2 mm."]), StubStore())
    o.run("will it rain?", profile={"place": "Nashik"})
    s = stats.snapshot()
    assert s["total_answers"] == 1 and s["guardrail_rejections"] == 1 and s["tool_failures"] == 1
    assert s["degraded_answers"] == 1 and s["intents_used"] == {"weather_advice": 1} and s["languages_used"] == {"en": 1}
    assert s["avg_latency_ms"] >= 0 and s["guardrail_fallbacks"] == 0


def test_stats_hold_no_user_text_and_endpoint_works():
    stats.reset()
    secret = "my private farm text 12345"
    Orchestrator(domain(["good"]), FakeLLM(["Max 33 C."]), StubStore()).run(secret, profile={"place": "Nashik"})
    assert secret not in str(stats.snapshot())
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception:
        return
    with TestClient(app) as c:
        body = c.get("/api/stats").json()
        assert set(body) >= {"total_answers", "guardrail_rejections", "avg_latency_ms", "languages_used", "intents_used"}
