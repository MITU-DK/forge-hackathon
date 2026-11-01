"""Leaks seen in real answers on 31 Oct 2026: foreign-script words, internal labels, system parts named as sources."""
import pytest

from app.domain.farm import DOMAIN
from app.guardrails.validators import run_all

ALL = DOMAIN.validators + DOMAIN.soft_validators  # hard (safety) and soft (style) rules together

CTX = "Open-Meteo 2026-10-31: rain 12.8 mm. Agmarknet 2026-10-30: Nashik median Rs 4000. SIGNAL NEUTRAL 3.7 %. SOIL MOISTURE 0.171 m3/m3"


def flagged(answer: str) -> bool:
    return bool(run_all(ALL, answer, CTX))


@pytest.mark.parametrize("bad", [
    "बारिश का шанс 12.8 mm है।",  # a Russian word inside a Hindi answer
    "Irrigate if needed (irrigate_if_soil_dry) as rain is 12.8 mm.",
    "पाणी कमी राहू शकते (Rain_short_of_demand), पाऊस 12.8 mm.",
    "निर्णय: कीमतें स्थिर हैं 【DECISION, 2026‑10‑31】।",
    "Nashik median Rs 4000 (Market Position code).",
    "Prices steady, 3.7 % (Signal from the price code).",
    "Rain 12.8 mm (RISK FLAGS, 2026-10-31).",
    "雨 12.8 mm expected.",
    "आजचे (202६‑10‑31) भाव Rs 4000 आहेत.",  # Devanagari digit inside an ordinary number
    "भाव data.gov.in Agmarkनॅट नुसार Rs 4000 आहे.",  # Latin and Devanagari letters in one word
    "Prices are steady (3.7 %), so there is no clear rise expected next week.",  # an implied forecast
    "Onion prices will likely go up if the rain continues.",
    "Your market today: Dindori Rs 4000 per quintal.",  # CTX has no YOUR MARKET line
])
def test_leaks_are_rejected(bad):
    assert flagged(bad), bad


@pytest.mark.parametrize("good", [
    "Rain is 12.8 mm in the next week (Open-Meteo, 2026-10-31).",
    "Nashik median is Rs 4000 (data.gov.in Agmarknet, 2026-10-30). The trend is steady, 3.7 %.",
    "आज नाशिक का भाव Rs 4000 है (data.gov.in Agmarknet, 2026-10-30)। रुझान स्थिर है, 3.7 %।",
    "पुढील आठवड्यात पाऊस 12.8 mm (Open‑Meteo, 2026‑10‑31).",
    "Soil moisture is 0.171 m3/m3 (Open-Meteo).",
    "सल्ला: कृषी विज्ञान केंद्र (KVK) शी बोला. भाव Rs 4000 रु/क्विंटल आहे, ३.७ % बदल.",  # Devanagari digits on their own are fine
    "भाव ₹4000 प्रति क्विंटल (Open‑Meteo, 2026-10-31); पाऊस 12.8 mm.",
    "I cannot forecast prices; the trend is 3.7 %, which only describes the last week.",
    "Temperatures are expected to rise next week; rain is 12.8 mm (Open-Meteo, 2026-10-31).",  # weather is a forecast source: allowed
    "Status ABOVE_STATE_MEDIAN reads as above the state median.",  # ALL_CAPS labels are humanised by plain_text, not rejected
])

def test_normal_answers_are_left_alone(good):
    assert not flagged(good), good



def test_own_market_claim_is_allowed_when_the_tool_found_one():
    ans = "Your market today: Lasalgaon Rs 4000 per quintal (Agmarknet, 2026-10-30)."
    assert not run_all(ALL, ans, CTX + "\nYOUR MARKET on 2026-10-30: Lasalgaon Rs 4000")


SRC_CTX = CTX + "\nMANDI PRICES for Onion in Maharashtra\nWEATHER for Nashik"


def test_a_price_or_weather_figure_must_name_its_source():
    assert run_all(ALL, "Nashik median is Rs 4000 per quintal.", SRC_CTX)  # no source named
    assert run_all(ALL, "Rain is 12.8 mm in the next week.", SRC_CTX)
    assert not run_all(ALL, "Nashik median is Rs 4000 (data.gov.in Agmarknet, 2026-10-30). Rain 12.8 mm (Open‑Meteo, 2026-10-31).", SRC_CTX)
    assert not run_all(ALL, "आज नाशिक का भाव Rs 4000 है (Agmarknet, 2026-10-30)।", SRC_CTX)
    assert not run_all(ALL, "Nashik median is Rs 4000.", CTX)  # no price source in play: nothing to cite


@pytest.mark.parametrize("bad", [
    "कीमतें अगले हफ्ते भी स्थिर रहेंगी।",
    "भाव अगले कुछ दिनों में बढ़ेंगे।",
    "पुढच्या आठवड्यात कांद्याचे भाव वाढतील.",
    "The soil is not dry enough to meet the crop's needs.",
    "Soil moisture is low in the surface layer.",
])
def test_price_forecasts_in_hindi_and_marathi_and_soil_verdicts_are_rejected(bad):
    assert flagged(bad), bad


@pytest.mark.parametrize("good", [
    "If the soil feels dry, irrigate; the soil moisture is 0.171 m3/m3 (Open-Meteo).",
    "पिछले 7 दिनों में कीमतें स्थिर रहीं, 3.7 %।",
    "मी भाव आधीच्या आठवड्यातील बदल सांगतो: 3.7 %.",
])
def test_past_trend_statements_and_conditional_soil_advice_are_fine(good):
    assert not flagged(good), good


def test_labels_become_phrases_in_the_answers_own_language():
    from app.guardrails.validators import plain_text

    t = plain_text("नाशिक: ABOVE_STATE_MEDIAN, RAIN_SHORT_OF_DEMAND और NEW_THING.", DOMAIN.label_text["hi"])
    assert "राज्य के औसत भाव से ऊपर" in t and "बारिश पानी की मांग से कम है" in t and "new thing" in t and "_" not in t
    assert plain_text("price ABOVE_STATE_MEDIAN", None) == "price above state median"  # languages without a table keep plain English words


def test_soil_figures_must_be_called_a_model_estimate():
    ctx = SRC_CTX + "\nSOIL for Nashik: a 250 m MODEL ESTIMATE: pH 7 ..."
    assert run_all(ALL, "Your soil pH is 7 and it is heavy clay (ISRIC).", ctx)
    assert not run_all(ALL, "Your soil pH is 7 (a 250 m model estimate, ISRIC SoilGrids), heavy clay. Get a soil test.", ctx)
    assert not run_all(ALL, "मिट्टी का pH 7 है, यह अनुमान है; मिट्टी की जांच कराएं।", ctx)
    assert not run_all(ALL, "Wheat sowing is fine.", ctx)  # no soil figure, nothing to qualify


@pytest.mark.parametrize("leak", [
    "The user is asking whether to sell wheat now. I need to follow the rules strictly.",
    "The CONTEXT provides MANDI PRICES, WEATHER, DECISION, etc.",
    "Also: the guidance says to lead with the YOUR MARKET line.",
    "I need to lead with the price, then the weather.",
])
def test_the_models_own_reasoning_is_never_delivered_as_an_answer(leak):
    assert run_all(DOMAIN.validators, leak, "x"), leak  # a HARD rule: the hard list alone must catch it


@pytest.mark.parametrize("fine", [
    "The decision is neutral: prices are steady (data.gov.in Agmarknet, 2026-10-30).",
    "In this context, onion prices moved 3.7 % (data.gov.in Agmarknet, 2026-10-30).",
    "The rule says neither a sell nor a hold, because the change is under 5 %.",
    "According to the context of the last week, rain is 12.8 mm (Open-Meteo).",
])
def test_ordinary_sentences_about_the_decision_or_the_rule_are_not_mistaken_for_leaks(fine):
    ctx = "MANDI PRICES WEATHER 3.7 % 5 % 12.8 mm 2026-10-30 Rs"
    assert not [v for v in run_all(DOMAIN.validators, fine, ctx) if "reasoning" in v], fine
