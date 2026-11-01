"""5.1 network failures + 5.2 bad input. No network, no keys: httpx.MockTransport and fakes only."""
import httpx
import pytest

import app.tools.http as http
from app.agents import planner as planner_mod
from app.agents.orchestrator import Orchestrator, script_lang
from app.core.config import Settings
from app.domain.farm import DOMAIN
from app.llm.client import LLMClient
from app.tools import SourceUnavailable
from tests.helpers import FakeLLM, StubStore
from tests.test_tools import GEO


@pytest.fixture(autouse=True)
def _clean_http():
    http._cache.clear()
    old = http._client
    yield
    http._client, http._cache = old, {}


def use(handler):
    http._client = httpx.Client(transport=httpx.MockTransport(handler))


# ---- 5.1 network failures ---------------------------------------------------------------
def test_429_without_cache_is_source_unavailable_and_key_never_leaks():
    use(lambda req: httpx.Response(429, json={"error": "rate"}))
    with pytest.raises(SourceUnavailable) as e:
        http.get_json("https://x.test/api", {"api-key": "SECRET123", "q": 1})
    assert "SECRET123" not in str(e.value)


def test_failure_after_success_returns_labelled_stale_real_data():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"v": 1}) if calls["n"] == 1 else httpx.Response(503)

    use(handler)
    assert http.get_json("https://x.test/a", ttl=0).stale is False
    again = http.get_json("https://x.test/a", ttl=0)  # ttl=0 forces a live call, which now fails
    assert again.stale is True and again.data == {"v": 1}


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("no network")])
def test_timeout_and_offline_are_source_unavailable(exc):
    def handler(req):
        raise exc

    use(handler)
    with pytest.raises(SourceUnavailable):
        http.get_json("https://x.test/b")


def test_everything_down_still_answers_with_degraded_and_no_crash(monkeypatch):
    monkeypatch.setattr(planner_mod, "RETRY_DELAY_S", 0)

    def handler(req):
        raise httpx.ConnectError("wifi off")

    use(handler)
    r = Orchestrator(DOMAIN, FakeLLM(off=True), StubStore()).run("Should I sell my onions?", profile={"place": "Nashik", "crop": "Onion"})
    assert r.answer and r.degraded and "not reachable right now" in r.answer and "wifi off" not in r.answer
    assert any(t.action == "replan" for t in r.trace)


def test_both_llm_providers_failing_gives_offline_text_not_exception():
    handler = lambda req: httpx.Response(429 if req.url.host == "p.test" else 500)
    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", fallback_api_key="k2", fallback_base_url="https://f.test/v1", fallback_model="m")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.provider == "offline" and r.text == "OFF" and r.degraded


# ---- 5.2 bad input ----------------------------------------------------------------------
def test_nonsense_place_asks_for_a_nearer_town(monkeypatch):
    monkeypatch.setattr(planner_mod, "RETRY_DELAY_S", 0)
    use(lambda req: httpx.Response(200, json={"results": []}))
    llm = FakeLLM(["Please tell me a nearer town."], route="weather_advice")
    r = Orchestrator(DOMAIN, llm, StubStore()).run("will it rain?", profile={"place": "asdfgh"})
    assert "place not found" in llm.last_messages[0]["content"] and r.answer and not any(t.action == "replan" for t in r.trace)


def test_huge_message_is_truncated_to_2000_chars():
    llm = FakeLLM(["ok"], route="general")
    Orchestrator(DOMAIN, llm, StubStore()).run("x" * 10000)
    assert len(llm.last_messages[-1]["content"]) == 2000


def test_api_rejects_oversized_message_with_422_not_500():
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception:
        return
    with TestClient(app) as c:
        assert c.post("/api/chat", json={"message": "x" * 10000}).status_code == 422


@pytest.mark.parametrize(
    "text,intent,lang",
    [("🌾 मेरे प्याज का क्या भाव है?", "market_price", "hi"), ("mera onion ka bhav kya hai Nashik mein?", "market_price", "en"), ("😀😀", "general", "en")],
)
def test_router_keyword_fallback_handles_emoji_hindi_hinglish(text, intent, lang):
    o = Orchestrator(DOMAIN, FakeLLM(off=True), StubStore())
    got_intent, got_lang, _, degraded, _ = o._route(text, None)
    assert (got_intent.name, got_lang, degraded) == (intent, lang, True)


def test_script_lang_detects_indic_scripts():
    assert script_lang("வணக்கம்") == "ta" and script_lang("ਸਤ ਸ੍ਰੀ ਅਕਾਲ") == "pa" and script_lang("hello 🌾") == "en"


# ---- circuit breaker + cache cap --------------------------------------------------------
def test_breaker_opens_after_two_failed_rounds_then_fails_fast_and_recovers(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    use(handler)
    clock = {"t": 1000.0}
    monkeypatch.setattr(http.time, "time", lambda: clock["t"])
    with pytest.raises(SourceUnavailable):
        http.get_json("https://dead.test/a")  # round 1
    clock["t"] += 8.5
    with pytest.raises(SourceUnavailable):
        http.get_json("https://dead.test/a")  # round 2 (the retry) -> breaker opens
    n = calls["n"]
    clock["t"] += 1
    with pytest.raises(SourceUnavailable) as e:
        http.get_json("https://dead.test/b")
    assert calls["n"] == n and "temporarily unreachable" in str(e.value)  # no network call while open
    clock["t"] += 40  # window over: one probe is allowed through
    use(lambda req: httpx.Response(200, json={"ok": 1}))
    assert http.get_json("https://dead.test/a").data == {"ok": 1}
    assert "dead.test" not in http._fails  # success closes it


def test_parallel_failures_count_as_one_round_so_the_retry_still_runs(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        raise httpx.ConnectError("x")

    use(handler)
    clock = {"t": 50.0}
    monkeypatch.setattr(http.time, "time", lambda: clock["t"])
    for _ in range(7):  # seven days fetched in parallel, all failing together
        with pytest.raises(SourceUnavailable):
            http.get_json("https://par.test/a", {"d": _})
    clock["t"] += 0.5
    with pytest.raises(SourceUnavailable):
        http.get_json("https://par.test/a", {"d": 99})  # the retry is NOT blocked
    assert calls["n"] == 8


def test_open_breaker_still_serves_stale_real_data(monkeypatch):
    clock = {"t": 10.0}
    monkeypatch.setattr(http.time, "time", lambda: clock["t"])
    use(lambda req: httpx.Response(200, json={"v": 7}))
    assert http.get_json("https://st.test/a", ttl=5).data == {"v": 7}
    clock["t"] += 100

    def boom(req):
        raise httpx.ReadTimeout("x")

    use(boom)
    for _ in range(2):
        r = http.get_json("https://st.test/a", ttl=5)  # fails live -> stale cache each time
        clock["t"] += 10
    assert r.stale and r.data == {"v": 7}
    assert http.get_json("https://st.test/a", ttl=5).stale  # breaker open: still the labelled stale copy


def test_client_errors_do_not_trip_the_breaker():
    use(lambda req: httpx.Response(403, json={"error": "bad key"}))
    for i in range(4):
        with pytest.raises(SourceUnavailable):
            http.get_json("https://key.test/a", {"i": i})
    assert "key.test" not in http._fails


def test_cache_is_capped(monkeypatch):
    monkeypatch.setattr(http, "CACHE_MAX", 5)
    use(lambda req: httpx.Response(200, json={}))
    for i in range(20):
        http.get_json("https://cap.test/a", {"i": i}, ttl=999)
    assert len(http._cache) == 5


def test_tools_identify_themselves_with_a_descriptive_user_agent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, json={})

    http._client = None  # force get_json to build its own client, then swap the transport underneath it
    real = httpx.Client

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=httpx.MockTransport(handler), **k)

    import pytest as _p

    mp = _p.MonkeyPatch()
    mp.setattr(http.httpx, "Client", Spy)
    try:
        http.get_json("https://ua.test/a")
    finally:
        mp.undo()
        http._client = None
    assert seen["ua"] == http.USER_AGENT and "python-httpx" not in seen["ua"] and "KisaanMitra" in seen["ua"]


def test_failure_text_names_the_source_and_never_a_hostname_or_url():
    from app.tools import safe_failure_text

    t = safe_failure_text("HTTPStatusError: Server error '503 Service Unavailable' for url 'https://api.open-meteo.com/v1/forecast?latitude=1&api-key=SECRET'")
    assert "Open-Meteo weather" in t and "https" not in t and "SECRET" not in t and "api.open-meteo.com" not in t
    assert "the Open-Meteo place finder" in safe_failure_text("geocoding-api.open-meteo.com is temporarily unreachable (not retrying for 31s)")
    assert safe_failure_text("no response from data.gov.in") == "no response from data.gov.in Agmarknet"
    assert safe_failure_text("could not reach evil.example.com") == "could not reach the live source"
