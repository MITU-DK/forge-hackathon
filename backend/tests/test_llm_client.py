"""LLM client fallback ladder with httpx.MockTransport (real environment: needs httpx)."""
import json

from app.core.config import Settings
from app.llm.client import LLMClient, parse_json, strip_think


def test_parse_helpers():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1} and parse_json("x") is None
    assert strip_think("<think>t</think>Hi") == "Hi"


def test_ladder_primary_fallback_offline():
    import httpx

    def handler(req):
        host = req.url.host
        if host == "primary.test":
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from fallback"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://primary.test/v1", fallback_api_key="k2", fallback_base_url="https://fb.test/v1", fallback_model="m")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from fallback" and r.provider == "fallback" and r.degraded
    r2 = LLMClient(Settings(llm_api_key=""), transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r2.provider == "offline" and r2.text == "OFF"


def test_short_429_is_waited_out_on_primary_before_using_fallback(monkeypatch):
    import httpx

    import app.llm.client as c

    slept, calls = [], {"primary": 0, "fb": 0}
    monkeypatch.setattr(c.time, "sleep", lambda s: slept.append(s))

    def handler(req):
        if req.url.host == "p.test":
            calls["primary"] += 1
            if calls["primary"] == 1:
                return httpx.Response(429, headers={"retry-after": "2"}, json={"error": "rate"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "from primary"}}]})
        calls["fb"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "from fallback"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", fallback_api_key="k2", fallback_base_url="https://f.test/v1", fallback_model="m")
    r = c.LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from primary" and r.provider == "primary" and not r.degraded
    assert slept == [2.0] and calls == {"primary": 2, "fb": 0}


def test_long_or_missing_retry_after_goes_straight_to_fallback(monkeypatch):
    import httpx

    import app.llm.client as c

    slept = []
    monkeypatch.setattr(c.time, "sleep", lambda s: slept.append(s))
    for hdr in ({"retry-after": "30"}, {}):
        handler = lambda req, hdr=hdr: httpx.Response(429, headers=hdr) if req.url.host == "p.test" else httpx.Response(200, json={"choices": [{"message": {"content": "fb"}}]})
        s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", fallback_api_key="k2", fallback_base_url="https://f.test/v1", fallback_model="m")
        r = c.LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
        assert r.provider == "fallback" and r.degraded
    assert slept == []


def test_fallback_gets_enough_tokens_to_finish_json_after_reasoning():
    import httpx

    seen = {}

    def handler(req):
        if req.url.host == "p.test":
            return httpx.Response(401, json={"error": "bad key"})
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_fast_model="small", fallback_api_key="k2", fallback_base_url="https://fb.test/v1", fallback_model="m")
    LLMClient(s, transport=httpx.MockTransport(handler)).chat_json([{"role": "user", "content": "hi"}], fast=True, max_tokens=80)
    assert seen["max_tokens"] >= 400


def test_primary_only_skips_the_fallback():
    import httpx

    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(401, json={"error": "bad key"})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", fallback_api_key="k2", fallback_base_url="https://fb.test/v1", fallback_model="m")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}], primary_only=True, offline="OFF")
    assert set(hosts) == {"p.test"} and r.provider == "offline"  # the reserve model shares the provider; the slow fallback is skipped


def test_a_dropped_connection_is_retried_once_before_the_fallback(monkeypatch):
    import httpx

    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    calls = {"p": 0, "fb": 0}

    def handler(req):
        if req.url.host == "p.test":
            calls["p"] += 1
            if calls["p"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=req)
            return httpx.Response(200, json={"choices": [{"message": {"content": "from primary"}}]})
        calls["fb"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "from fallback"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", fallback_api_key="k2", fallback_base_url="https://fb.test/v1", fallback_model="m")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from primary" and not r.degraded and calls == {"p": 2, "fb": 0}


def test_speech_text_drops_silence_but_keeps_real_speech():
    from app.llm.client import speech_text

    silent = {"text": " Thank you.", "segments": [{"text": " Thank you.", "no_speech_prob": 0.92, "avg_logprob": -1.7}]}
    assert speech_text(silent) == ""  # Whisper's habitual phrase on silence is not a question
    mixed = {"segments": [{"text": " Thank you.", "no_speech_prob": 0.9, "avg_logprob": -1.5}, {"text": " Should I sell my onions", "no_speech_prob": 0.02, "avg_logprob": -0.3}]}
    assert speech_text(mixed) == "Should I sell my onions"
    unsure_but_speech = {"segments": [{"text": " mera pyaaz", "no_speech_prob": 0.1, "avg_logprob": -1.4}]}
    assert speech_text(unsure_but_speech) == "mera pyaaz"  # low confidence alone is not silence
    assert speech_text({"text": " plain text only "}) == "plain text only"  # a provider without segment scores
    assert speech_text({}) == "" and speech_text(None) == ""


def test_transcribe_sends_language_vocabulary_hint_and_zero_temperature():
    import httpx

    seen = {}

    def handler(req):
        seen["body"] = req.read().decode("latin-1")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"text": "x", "segments": [{"text": " कांदा विकू का", "no_speech_prob": 0.01, "avg_logprob": -0.2}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1")
    out = LLMClient(s, transport=httpx.MockTransport(handler)).transcribe(b"x" * 2000, "audio.m4a", "mr", "कांदा, पाऊस")
    assert out == "कांदा विकू का" and seen["url"].endswith("/audio/transcriptions")
    body = seen["body"]
    assert 'name="response_format"' in body and "verbose_json" in body and 'name="temperature"' in body and 'name="language"' in body and 'filename="audio.m4a"' in body


def test_transcribe_retries_a_dropped_connection_once(monkeypatch):
    import httpx

    import app.llm.client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("dropped")
        return httpx.Response(200, json={"text": "should I sell"})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1")
    assert LLMClient(s, transport=httpx.MockTransport(handler)).transcribe(b"x" * 2000) == "should I sell" and len(calls) == 2


def test_stock_silence_phrases_and_punctuation_only_output_are_dropped_even_when_the_provider_is_confident():
    from app.llm.client import speech_text

    confident = {"segments": [{"text": " Thank you.", "no_speech_prob": 0, "avg_logprob": -0.29}]}  # what Groq really returned for 4 s of silence
    assert speech_text(confident) == "" and speech_text({"text": " ."}) == "" and speech_text({"text": "..."}) == ""
    assert speech_text({"text": " धन्यवाद।"}) == "" and speech_text({"text": "Thanks for watching!"}) == ""
    assert speech_text({"text": "Thank you, should I sell my onions?"}) == "Thank you, should I sell my onions?"  # only the bare phrase is dropped
    assert speech_text({"text": " मी कांदा विकू का?"}) == "मी कांदा विकू का?"


def test_reserve_model_answers_when_the_big_models_quota_is_spent_before_the_slow_fallback():
    import httpx

    models = []

    def handler(req):
        body = json.loads(req.content)
        models.append((req.url.host, body["model"]))
        if body["model"] == "big":
            return httpx.Response(429, json={"error": "tokens per day"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from small"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="small", fallback_api_key="k2", fallback_base_url="https://f.test/v1", fallback_model="m")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from small" and r.provider == "reserve" and r.model == "small" and not r.degraded
    assert all(h == "p.test" for h, _ in models)  # the slow fallback was never needed


def test_a_segment_that_loops_on_one_phrase_is_dropped_but_ordinary_speech_with_a_normal_ratio_is_kept():
    from app.llm.client import speech_text

    looping = {"segments": [{"text": "बारिश बारिश बारिश बारिश बारिश बारिश", "avg_logprob": -0.1, "no_speech_prob": 0, "compression_ratio": 3.1}]}
    ordinary = {"segments": [{"text": "Should I sell my onions now?", "avg_logprob": -0.14, "no_speech_prob": 0, "compression_ratio": 0.79}]}
    assert speech_text(looping) == "" and speech_text(ordinary) == "Should I sell my onions now?"
    assert speech_text({"segments": [{"text": "a", "compression_ratio": 3.0}, {"text": "kept", "compression_ratio": 1.2}]}) == "kept"


def _counting_client(statuses, **kw):
    """A client whose primary host answers with the given status codes in turn (the last repeats); counts the calls it received."""
    import httpx

    calls = {"n": 0}

    def handler(req):
        i = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        status = statuses[i]
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(status, json={"error": "x"})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big", **kw)
    return LLMClient(s, transport=httpx.MockTransport(handler)), calls


def test_forbidden_model_is_skipped_on_the_next_questions():
    client, calls = _counting_client([403])
    first = client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert first.provider == "offline" and calls["n"] == 1
    again = client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert again.provider == "offline" and calls["n"] == 1  # no new request: the breaker is open
    assert "skipped" in again.error


def test_breaker_closes_after_its_window(monkeypatch):
    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    client, calls = _counting_client([503, 503, 503, 200])
    client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert calls["n"] == c.MAX_ATTEMPTS  # three tries, then the provider is skipped
    real = c.time.time()
    monkeypatch.setattr(c.time, "time", lambda: real + c.BREAKER_OUTAGE_S + 1)
    r = client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.provider == "primary" and r.text == "ok" and calls["n"] == c.MAX_ATTEMPTS + 1


def test_rate_limit_and_bad_json_do_not_open_the_breaker(monkeypatch):
    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    client, calls = _counting_client([429, 429, 200])
    client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    n = calls["n"]
    client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert calls["n"] > n  # tried again: a 429 is not an outage


def test_a_5xx_or_a_dropped_connection_is_retried_and_the_answer_still_arrives(monkeypatch):
    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    client, calls = _counting_client([502, 503, 200])
    r = client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.text == "ok" and r.provider == "primary" and calls["n"] == 3


def test_a_connection_reset_twice_is_survived(monkeypatch):
    import httpx

    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] < 3:
            raise httpx.RemoteProtocolError("Connection reset by peer")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big"), transport=httpx.MockTransport(handler))
    assert client.chat([{"role": "user", "content": "hi"}], offline="OFF").text == "ok" and n["i"] == 3


def test_a_used_up_primary_quota_is_not_retried_but_a_busy_free_fallback_is(monkeypatch):
    import httpx

    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    primary, fallback = {"n": 0}, {"n": 0}

    def handler(req):
        if req.url.host == "p.test":
            primary["n"] += 1
            return httpx.Response(429, headers={"retry-after": "3000"}, json={"error": "daily quota"})
        fallback["n"] += 1
        return httpx.Response(429, json={"error": "busy"}) if fallback["n"] < 3 else httpx.Response(200, json={"choices": [{"message": {"content": "free"}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big",
                 fallback_api_key="f", fallback_base_url="https://f.test/v1", fallback_model="openrouter/free")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.provider == "fallback" and r.text == "free" and primary["n"] == 1 and fallback["n"] == 3


def test_a_blank_answer_is_asked_again_and_never_returned_as_success(monkeypatch):
    import httpx

    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    answers = iter(["", "real answer"])

    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": next(answers)}}]})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big")
    assert LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}], offline="OFF").text == "real answer"

    always_blank = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}))
    r = LLMClient(s, transport=always_blank).chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.provider == "offline" and r.text == "OFF" and "empty answer" in r.error


def test_the_ladder_rotates_through_several_fallback_models_and_a_second_provider(monkeypatch):
    import httpx

    import app.llm.client as c

    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    seen = []

    def handler(req):
        model = __import__("json").loads(req.content)["model"]
        seen.append((req.url.host, model))
        ok = req.url.host == "g.test"  # only the second provider is healthy
        return httpx.Response(200, json={"choices": [{"message": {"content": "from g"}}]}) if ok else httpx.Response(503, json={})

    s = Settings(llm_api_key="k", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big",
                 fallback_api_key="f", fallback_base_url="https://f.test/v1", fallback_model="free-a, free-b",
                 fallback2_api_key="g", fallback2_base_url="https://g.test/v1", fallback2_model="gem")
    client = LLMClient(s, transport=httpx.MockTransport(handler))
    r = client.chat([{"role": "user", "content": "hi"}], offline="OFF")
    assert r.text == "from g" and r.provider == "fallback"
    tried = {m for _, m in seen}
    assert {"big", "free-a", "free-b", "gem"} <= tried
    assert [p[3] for p in client._providers(False, False)] == ["big", "free-a", "free-b", "gem"]
    assert [p[3] for p in client._providers(False, True)] == ["big"]  # optional steps never wait on fallbacks


def _stt_settings():
    return Settings(llm_api_key="k", llm_base_url="https://groq.test/v1", fallback2_api_key="g", fallback2_model="gem-1,gem-2")


def test_speech_falls_back_to_gemini_when_whisper_is_blocked():
    import httpx
    seen = {}

    def handler(req):
        if req.url.host == "groq.test":
            return httpx.Response(403, json={"error": {"code": "model_permission_blocked_org"}})
        seen["url"], seen["mime"] = str(req.url), json.loads(req.content)["contents"][0]["parts"][1]["inline_data"]["mime_type"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "आज कांदा विकू का?"}]}}]})

    text = LLMClient(_stt_settings(), transport=httpx.MockTransport(handler)).transcribe(b"audio", "voice.ogg", "mr")
    assert text == "आज कांदा विकू का?" and "models/gem-1:generateContent" in seen["url"] and seen["mime"] == "audio/ogg"


def test_gemini_no_speech_marker_is_dropped_not_sent_on_as_a_question():
    import httpx

    def handler(req):
        if req.url.host == "groq.test":
            return httpx.Response(503)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[NO_SPEECH]"}]}}]})

    assert LLMClient(_stt_settings(), transport=httpx.MockTransport(handler)).transcribe(b"x", "a.webm", "hi") == ""


def test_speech_error_still_raises_when_no_second_provider():
    import httpx
    import pytest
    s = Settings(llm_api_key="k", llm_base_url="https://groq.test/v1")
    with pytest.raises(httpx.HTTPStatusError):
        LLMClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(403))).transcribe(b"x", "a.webm", None)


def test_second_key_takes_over_when_the_first_quota_is_spent_and_is_not_marked_degraded():
    import httpx

    def handler(req):
        if req.headers["authorization"] == "Bearer k1":
            return httpx.Response(429, json={"error": "daily limit"}, headers={"retry-after": "1200"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from key two"}}]})

    s = Settings(llm_api_key="k1", llm_api_key2="k2", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from key two" and r.provider == "primary2" and not r.degraded


def test_speech_tries_the_second_key_before_gemini():
    import httpx
    used = []

    def handler(req):
        if req.url.host == "groq.test":
            used.append(req.headers["authorization"])
            return httpx.Response(403) if req.headers["authorization"] == "Bearer k1" else httpx.Response(200, json={"text": "कांदा विकू का"})
        raise AssertionError("Gemini must not be called when key two works")

    s = Settings(llm_api_key="k1", llm_api_key2="k2", llm_base_url="https://groq.test/v1", fallback2_api_key="g")
    assert LLMClient(s, transport=httpx.MockTransport(handler)).transcribe(b"x", "a.ogg", "mr") == "कांदा विकू का" and used == ["Bearer k1", "Bearer k2"]


def test_photo_analysis_tries_the_next_account_then_gemini_when_a_model_is_blocked():
    import httpx
    seen = []

    def handler(req):
        key = req.headers["authorization"]
        seen.append((req.url.host, key))
        if key == "Bearer k1":
            return httpx.Response(403, json={"error": {"code": "model_permission_blocked_org"}})
        if key == "Bearer k2":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Brown spots on cassava leaves."}}]})
        raise AssertionError("Gemini must not be called when the second key works")

    s = Settings(llm_api_key="k1", llm_api_key2="k2", llm_base_url="https://groq.test/v1", llm_vision_model="v", fallback2_api_key="g", fallback2_base_url="https://gem.test/v1")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).vision("aGk=", "what is wrong?")
    assert r.text.startswith("Brown spots") and r.provider == "primary" and [k for _, k in seen] == ["Bearer k1", "Bearer k2"]


def test_photo_analysis_falls_to_gemini_and_marks_it_degraded_and_fails_cleanly_when_all_fail():
    import httpx

    def handler(req):
        if req.url.host == "gem.test":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Leaf looks yellow."}}]})
        return httpx.Response(403)

    s = Settings(llm_api_key="k1", llm_base_url="https://groq.test/v1", fallback2_api_key="g", fallback2_base_url="https://gem.test/v1", fallback2_model="gm1,gm2")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).vision("aGk=", "q")
    assert r.text == "Leaf looks yellow." and r.provider == "fallback" and r.degraded
    dead = LLMClient(s, transport=httpx.MockTransport(lambda req: httpx.Response(403))).vision("aGk=", "q")
    assert dead.provider == "offline" and dead.text == "" and dead.error


def test_third_key_takes_over_when_the_first_two_are_spent():
    import httpx

    def handler(req):
        if req.headers["authorization"] in ("Bearer k1", "Bearer k2"):
            return httpx.Response(429, json={"error": "daily limit"}, headers={"retry-after": "1200"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from key three"}}]})

    s = Settings(llm_api_key="k1", llm_api_key2="k2", llm_api_key3="k3", llm_base_url="https://p.test/v1", llm_model="big", llm_fast_model="big")
    r = LLMClient(s, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "hi"}])
    assert r.text == "from key three" and r.provider == "primary3" and not r.degraded
