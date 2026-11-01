"""The safety rules (no guarantee, no brand, no dose) hold in Hindi and Marathi, not just English.
The numbers are in the context on purpose, so that only the safety rules can flag these answers."""
import pytest

from app.domain.farm import DOMAIN
from app.guardrails.validators import run_all

CTX = "dose numbers present: 20 500 2"


def flagged(text: str) -> bool:
    return bool(run_all(DOMAIN.validators, text, CTX))


@pytest.mark.parametrize("bad", [
    "इस भाव पर बेचने से पक्का मुनाफा होगा।",
    "आप गारंटी के साथ कमाएंगे।",
    "आता विकल्यास नक्की नफा होईल.",
    "याची खात्री आहे, भाव वाढणार.",
    "प्रति लीटर पानी में 20 मिली दवा मिलाकर छिड़कें।",
    "एकरी 500 ग्रॅम खत टाका.",
    "2 मिली प्रति लिटर फवारा.",
    "राउंडअप का छिड़काव करें।", "रिडोमिल गोल्ड फवारा.", "कॉन्फिडोर वापरा.",
    "Spray Confidor on the leaves.",
])
def test_unsafe_hindi_and_marathi_answers_are_rejected(bad):
    assert flagged(bad), bad


@pytest.mark.parametrize("good", [
    "मैं मुनाफे की गारंटी नहीं दे सकता; यह केवल निर्णय सहायता है।",
    "नफ्याची हमी देता येत नाही; ही फक्त मदत आहे.",
    "कोई गारंटी नहीं है, इसलिए कृषि अधिकारी से पूछें।",
    "दवा के लेबल का पालन करें और कृषि अधिकारी की सलाह लें।",
    "औषधाच्या लेबलप्रमाणे फवारणी करा आणि कृषी अधिकाऱ्याला विचारा.",
    "आज भाव स्थिर है।",
])
def test_refusals_and_plain_advice_are_left_alone(good):
    assert not flagged(good), good


@pytest.mark.parametrize("bad", [
    "இப்போது விற்றால் உத்தரவாதம் உண்டு, லாபம் நிச்சயம்.",  # ta
    "ఇప్పుడు అమ్మితే లాభం హామీ.",  # te
    "ಈಗ ಮಾರಿದರೆ ಗ್ಯಾರಂಟಿ ಲಾಭ.",  # kn
    "এখন বিক্রি করলে লাভের গ্যারান্টি আছে।",  # bn
    "હવે વેચવાથી નફાની ગેરંટી છે.",  # gu
    "ਹੁਣ ਵੇਚਣ ਨਾਲ ਮੁਨਾਫ਼ੇ ਦੀ ਗਾਰੰਟੀ ਹੈ।",  # pa
    "ഇപ്പോൾ വിറ്റാൽ ലാഭത്തിന് ഗ്യാരണ്ടി ഉണ്ട്.",  # ml
])
def test_guarantees_in_the_other_languages_are_rejected(bad):
    assert flagged(bad), bad


@pytest.mark.parametrize("good", [
    "லாபத்திற்கு உத்தரவாதம் இல்லை; இது முடிவு உதவி மட்டுமே.",  # ta: no guarantee
    "లాభానికి హామీ ఇవ్వలేను; ఇది కేవలం సహాయం.",  # te
    "ಲಾಭಕ್ಕೆ ಗ್ಯಾರಂಟಿ ಇಲ್ಲ.",  # kn
    "লাভের গ্যারান্টি নেই; এটি কেবল সহায়তা।",  # bn
    "નફાની ગેરંટી નથી.",  # gu
    "ਮੁਨਾਫ਼ੇ ਦੀ ਗਾਰੰਟੀ ਨਹੀਂ ਹੈ।",  # pa
    "ലാഭത്തിന് ഗ്യാരണ്ടി ഇല്ല.",  # ml
    "உங்கள் வயல் நிலையை உறுதி செய்யுங்கள்.",  # ta: "make sure of your field condition" is everyday speech, not a guarantee
    "તમારા ખેતરની ખાતરી કરો.",  # gu: "make sure", everyday speech
])
def test_refusals_and_everyday_make_sure_phrases_are_left_alone(good):
    assert not flagged(good), good
