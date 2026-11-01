"""Photo diagnosis: structured card, sanitised model output, no brands/doses, honest failure."""
from app.domain.diagnosis import DISCLAIMER, parse_diagnosis
from app.domain.farm import DOMAIN
from app.guardrails.validators import run_all
from app.agents.orchestrator import Orchestrator
from app.llm.client import LLMResult
from tests.helpers import FakeLLM, StubStore

GOOD = '{"crop": "Tomato", "symptoms": ["yellowing leaves", "brown spots on lower leaves"], "severity": "Moderate", "possible_causes": ["Early blight", "Nutrient deficiency"]}'


def test_parse_builds_card_and_summary():
    d = parse_diagnosis("```json\n" + GOOD + "\n```")
    c = d["card"]
    assert c["type"] == "diagnosis" and c["severity"] == "moderate" and c["symptoms"][0] == "yellowing leaves"
    assert c["disclaimer"] == DISCLAIMER and any("KVK" in r for r in c["recommendations"])
    assert "IMAGE ASSESSMENT" in d["summary"] and "Tomato" in d["summary"]


def test_unsafe_items_are_dropped_and_bad_severity_is_unknown():
    bad = '{"crop": "Tomato", "symptoms": ["spots", "spray Bayer Confidor"], "severity": "catastrophic", "possible_causes": ["use 5 ml per litre of X"]}'
    c = parse_diagnosis(bad)["card"]
    assert c["symptoms"] == ["spots"] and c["possible_causes"] == [] and c["severity"] == "unknown"


def test_unparseable_or_non_dict_returns_none():
    for t in ("", "I see a leaf", "[1,2]", "{broken"):
        assert parse_diagnosis(t) is None


def test_lists_are_bounded():
    c = parse_diagnosis('{"crop":"x","symptoms":' + str([f"s{i}" for i in range(20)]).replace("'", '"') + ',"severity":"mild","possible_causes":"oops"}')["card"]
    assert len(c["symptoms"]) == 5 and c["possible_causes"] == []


def test_farm_guardrails_reject_brands_and_rates():
    v = DOMAIN.validators
    assert run_all(v, "Spray Roundup on the field.", "")
    assert run_all(v, "Use 500 ml per acre of the product.", "")
    assert run_all(v, "Use 2 g/litre.", "")
    assert not run_all(v, "The leaves look yellow. Show them to your KVK officer.", "")


class VisionLLM(FakeLLM):
    def __init__(self, vtext, **kw):
        super().__init__(**kw)
        self.vtext = vtext

    def vision(self, *a, **k):
        return LLMResult(self.vtext, "primary", "m") if self.vtext else LLMResult("", "offline", degraded=True, error="down")


def _run(vtext, answers):
    llm = VisionLLM(vtext, answers=answers, route="crop_health")
    o = Orchestrator(DOMAIN, llm, StubStore())
    return o.run("", image_b64="AAAA", profile={}), llm


def test_pipeline_photo_gives_card_context_and_no_tools_needed():
    r, llm = _run(GOOD, ["The leaves show yellow spots. Please show them to your KVK officer."])
    assert r.intent == "crop_health" and r.cards and r.cards[0]["type"] == "diagnosis"
    assert "IMAGE ASSESSMENT" in llm.last_messages[0]["content"]
    assert any(t.agent == "Vision" and t.ok for t in r.trace) and not r.warnings


def test_pipeline_unusable_photo_says_so_and_never_guesses():
    r, llm = _run("", ["I could not assess the photo. Please send a clearer one."])
    assert not r.cards and r.degraded
    assert "IMAGE ANALYSIS FAILED" in llm.last_messages[0]["content"]
    assert any(t.agent == "Vision" and not t.ok for t in r.trace)


def test_internal_image_label_never_reaches_the_farmer():
    assert run_all(DOMAIN.validators, "Spots on leaves (IMAGE ASSESSMENT).", "")
    assert run_all(DOMAIN.validators, "image analysis says blight", "")
    assert not run_all(DOMAIN.validators, "From your photo, the leaves have brown spots.", "")


def test_vision_prompt_asks_for_the_farmers_language_only_when_it_is_not_english():
    seen = []

    class Spy(VisionLLM):
        def vision(self, image_b64, question, *a, **k):
            seen.append(question)
            return super().vision(image_b64, question)

    for lang in ("en", "hi", "mr"):
        Orchestrator(DOMAIN, Spy(GOOD, answers=["ok"], route="crop_health"), StubStore()).run("", language=lang, image_b64="AAAA", profile={})
    assert "Write every symptom" not in seen[0]  # English: prompt unchanged
    assert "हिन्दी" in seen[1] and "मराठी" in seen[2]
    assert "Keep the JSON keys" in seen[1] and "Do NOT recommend pesticides" in seen[1]  # the safety rules stay in the prompt


def test_a_line_with_a_foreign_letter_inside_a_hindi_word_is_dropped_from_the_card():
    from app.domain.diagnosis import _clean
    assert _clean(["पोषक तत्वों की कमी", "जड़ों का गнить होना", "叶子发黄", "yellow leaves"]) == ["पोषक तत्वों की कमी", "yellow leaves"]
