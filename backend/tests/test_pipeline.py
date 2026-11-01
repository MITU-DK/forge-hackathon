"""Orchestrator behaviour with scripted LLM + stub tools. No network."""
from app.agents.orchestrator import Orchestrator
from app.domain import Domain, Intent
from app.guardrails.validators import grounded_numbers
from app.tools import NeedsInput, ToolResult
from tests.helpers import FakeLLM, StubStore, hit

DATA = ToolResult("WEATHER for Nashik: max 33.1 C, rain 70.2 mm", True, [{"name": "Open-Meteo forecast", "url": "u", "fetched_at": "t", "stale": False}], [{"type": "forecast", "title": "x"}], {"location": {"name": "Nashik", "state": "Maharashtra", "district": "Nashik", "lat": 1, "lon": 2}})
calls = []


def good(text, profile):
    calls.append("good")
    return DATA


def other(text, profile):
    calls.append("other")
    return ToolResult("MANDI PRICES: Rs 2450 per quintal", True, [{"name": "data.gov.in Agmarknet", "url": "u2", "fetched_at": "t", "stale": True}])


def needs(text, profile):
    raise NeedsInput("place", "nearest town")


def boom(text, profile):
    raise RuntimeError("bug")


def down(text, profile):
    return ToolResult("MANDI SOURCE UNAVAILABLE (x). Do NOT guess these values", False)


def post(text, profile):
    calls.append("post")
    return ToolResult("DECISION (computed in code): HOLD", True)


def domain(intent_tools, post_tools=None, tools=None):
    t = {"good": good, "other": other, "needs": needs, "boom": boom, "down": down, "post": post}
    t.update(tools or {})
    return Domain("t", "T", "t", "persona", [Intent("weather_advice", "d", "i", ["rain"]), Intent("general", "g", "i")], [grounded_numbers()], "SAFE", ["p"], tools=t, intent_tools={"weather_advice": intent_tools}, post_tools={"weather_advice": post_tools or []})


def run(intent_tools, llm, post_tools=None, hits=None, text="will it rain?", profile=None):
    return Orchestrator(domain(intent_tools, post_tools), llm, StubStore(hits)).run(text, profile=profile or {"place": "Nashik"})


def test_happy_path_trace_sources_cards_profile():
    llm = FakeLLM(["Max 33 C on that day and rain 70.2 mm."])
    r = run(["good", "other"], llm)
    agents = [t.agent for t in r.trace]
    assert agents[0] == "Router" and agents.count("Tool") == 2 and "Guardrail" in agents
    assert [s["name"] for s in r.live_sources] == ["Open-Meteo forecast", "data.gov.in Agmarknet"]
    assert r.cards and r.profile["state"] == "Maharashtra"
    assert r.degraded  # one source was stale -> flagged
    assert "CONTEXT" in llm.last_messages[0]["content"] and "MANDI PRICES" in llm.last_messages[0]["content"]


def test_ungrounded_number_is_rejected_then_corrected():
    r = run(["good"], FakeLLM(["Rain will be 85 mm.", "Rain will be 70.2 mm."]))
    assert [t.action for t in r.trace if t.agent == "Guardrail"] == ["REJECTED", "passed"]
    assert r.answer == "Rain will be 70.2 mm."


def test_persistent_hallucination_returns_safe_fallback():
    r = run(["good"], FakeLLM(["Rain will be 85 mm."]))
    assert r.answer == "SAFE" and r.warnings


def test_missing_input_makes_agent_ask_and_skips_post_tools():
    calls.clear()
    llm = FakeLLM(["Which town is your farm in?"])
    r = run(["needs"], llm, post_tools=["post"], profile={})
    ctx = llm.last_messages[0]["content"]
    assert "MISSING INFO" in ctx and "place" in ctx and "post" not in calls
    assert any("needs place" in t.action for t in r.trace)


def test_failed_source_continues_and_says_dont_guess():
    llm = FakeLLM(["The price source is not reachable right now."])
    r = run(["good", "down"], llm)
    ctx = llm.last_messages[0]["content"]
    assert "UNAVAILABLE" in ctx and "WEATHER for Nashik" in ctx and r.degraded
    assert any(t.agent == "Tool" and not t.ok for t in r.trace)


def test_crashing_tool_never_crashes_pipeline():
    r = run(["good", "boom"], FakeLLM(["Max 33 C."]))
    assert r.answer == "Max 33 C." and any("crashed" in t.action for t in r.trace)


def test_post_tools_run_after_main_tools():
    calls.clear()
    run(["good", "other"], FakeLLM(["Max 33 C."]), post_tools=["post"])
    assert calls[-1] == "post" and set(calls[:2]) == {"good", "other"}


def test_llm_outage_still_shows_real_live_data_not_fake_text():
    r = run(["good"], FakeLLM(off=True))
    assert r.degraded and r.answer.startswith("(AI summary unavailable - showing live data only)") and "rain 70.2 mm" in r.answer


def test_llm_outage_and_missing_input_asks_user():
    r = run(["needs"], FakeLLM(off=True), profile={})
    assert r.answer.startswith("To help you I need:")


def test_empty_input_and_documents_used():
    assert "type or speak" in run(["good"], FakeLLM(), text="   ").answer
    r = run([], FakeLLM(["Fine, 33 C."]), hits=[hit("Spray in the evening.", "icar.md")])
    assert r.sources == ["icar.md"]


def test_complete_inputs_tell_the_model_not_to_ask_again():
    llm = FakeLLM(["Rain 70.2 mm expected."])
    run(["good"], llm)
    assert "INPUTS COMPLETE" in llm.last_messages[0]["content"]
    llm = FakeLLM(["Which place?"])
    run(["needs"], llm)
    assert "INPUTS COMPLETE" not in llm.last_messages[0]["content"]


def test_safe_fallback_uses_the_farmers_language():
    d = domain(["good"])
    d.safe_fallback_by_lang, d.languages = {"mr": "SAFE-MR"}, {"en": "English", "mr": "Marathi"}
    r = Orchestrator(d, FakeLLM(["Rain will be 85 mm."]), StubStore()).run("will it rain?", language="mr", profile={"place": "Nashik"})
    assert r.answer == "SAFE-MR"
    r = Orchestrator(d, FakeLLM(["Rain will be 85 mm."]), StubStore()).run("will it rain?", language="en", profile={"place": "Nashik"})
    assert r.answer == "SAFE"


def test_offline_answer_hides_exception_text_and_urls_from_the_farmer():
    llm = FakeLLM(off=True)
    r = run(["good", "down"], llm)
    assert "WEATHER for Nashik" in r.answer  # the real data stays
    assert "Do NOT guess" not in r.answer and "SOURCE UNAVAILABLE" not in r.answer
    assert "not reachable right now" in r.answer


def test_every_supported_language_has_a_native_safe_fallback():
    from app.domain.farm import DOMAIN
    assert set(DOMAIN.languages) - {"en"} == set(DOMAIN.safe_fallback_by_lang)


def test_a_slow_request_stops_rewriting_and_returns_the_safe_answer(monkeypatch):
    import app.agents.orchestrator as orch
    monkeypatch.setattr(orch, "CORRECTION_BUDGET_S", -1)
    llm = FakeLLM(["Rain will be 85 mm.", "Rain will be 70.2 mm."])
    r = run(["good"], llm)
    assert r.answer == "SAFE" and llm.n == 1  # one draft only, the correct second one is never requested
    assert [t.action for t in r.trace if t.agent == "Guardrail"] == ["REJECTED", "fallback"]


def test_offline_answer_drops_instructions_meant_for_the_model():
    ctx = "SIGNAL (computed in code): NEUTRAL - prices are roughly STEADY. This is a recent trend, not a forecast. Say that in one or two short sentences.\nNOTE: no markets in Nashik on 2026-10-31. Tell the farmer this, and that the prices below are from other markets."
    from app.agents.offline import _plain_failure
    out = _plain_failure(ctx)
    assert "Say that" not in out and "Tell the farmer" not in out
    assert "NEUTRAL" in out and "not a forecast" in out and "no markets in Nashik on 2026-10-31" in out


def test_keyword_router_understands_marathi_and_hindi_when_the_llm_is_down():
    from app.domain.farm import DOMAIN
    o = Orchestrator(DOMAIN, FakeLLM(off=True), StubStore())
    for text, want in [("मी माझे पीक आता विकू की थांबू?", "sell_or_hold"), ("क्या मैं अपना प्याज़ अभी बेचूँ?", "sell_or_hold"),
                       ("आज माझ्या पिकाचा बाजारभाव काय आहे?", "market_price"), ("आज मंडी भाव क्या है?", "market_price"),
                       ("पुढील काही दिवसांत फवारणी करणे सुरक्षित आहे का?", "weather_advice"), ("क्या आज दवा छिड़कना सुरक्षित है?", "weather_advice"),
                       ("या आठवड्यात पाऊस पडेल का?", "weather_advice"),
                       ("pyaaz bechna chahiye ya ruk jaun", "sell_or_hold"), ("माझ्या कांद्याच्या पानांवर डाग आहेत, काय करू?", "crop_health")]:
        assert o._route(text, None)[0].name == want, text


def test_anonymous_default_session_shares_no_history_or_memory():
    o = Orchestrator(domain(["good"]), FakeLLM(["Rain 70.2 mm."]), StubStore())
    o.run("will it rain?", profile={"place": "Nashik"})
    llm = FakeLLM(["Rain 70.2 mm."])
    o.llm = llm
    o.run("and tomorrow?", profile={"place": "Nashik"})
    roles = [m["role"] for m in llm.last_messages]
    assert roles == ["system", "user"]  # no earlier caller's turns
    o.run("hello", session_id="farmer-1", profile={"place": "Nashik"})
    o.run("and tomorrow?", session_id="farmer-1", profile={"place": "Nashik"})
    assert [m["role"] for m in llm.last_messages] == ["system", "user", "assistant", "user"]  # a real session keeps its turns


def test_devanagari_language_marathi_vs_hindi_is_decided_by_words_only_one_has():
    from app.agents.orchestrator import devanagari_lang, script_lang

    assert devanagari_lang("आज कांद्याचा भाव काय आहे?") == "mr" and devanagari_lang("मी माझा कांदा आता विकू की थांबू?") == "mr"
    assert devanagari_lang("क्या मैं अपना गेहूं अभी बेचूं?") == "hi" and devanagari_lang("आज प्याज का भाव क्या है और क्या मुझे बेचना चाहिए?") == "hi"
    assert devanagari_lang("कांदा") is None and devanagari_lang("price") is None
    assert script_lang("आज कांद्याचा भाव काय आहे?") == "mr" and script_lang("कांदा") == "hi" and script_lang("hello") == "en"


def test_hinglish_typed_in_roman_letters_is_answered_in_roman_letters_unless_a_language_was_chosen():
    from app.agents.orchestrator import Orchestrator, is_romanised
    from tests.helpers import FakeLLM, StubStore

    assert is_romanised("pyaz ka bhav kya hai", None, "hi") and is_romanised("kanda vikau ka", None, "mr")
    assert not is_romanised("pyaz ka bhav kya hai", "hi", "hi")  # the UI language is chosen: respect it
    assert not is_romanised("what is the price", None, "en") and not is_romanised("प्याज का भाव", None, "hi") and not is_romanised("123", None, "hi")
    llm = FakeLLM(["ok"])
    llm.route = "weather_advice"
    o = Orchestrator(domain(["good"]), llm, StubStore())
    msgs = o._build_messages(o.domain.intents[0], "hi", "ctx", "", "default", "baarish hogi kya", roman=True)
    assert "written in Roman letters" in msgs[0]["content"] and "do not use Devanagari" in msgs[0]["content"]
    assert "Roman letters" not in o._build_messages(o.domain.intents[0], "hi", "ctx", "", "default", "x")[0]["content"]
