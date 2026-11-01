"""How a farmer's question becomes a document search, and how a question overrides the profile form."""
from __future__ import annotations

import re

from app.tools.crop_fit import table_key
from app.tools.location import FILLER
from app.tools.mandi import crop_key, crops_in_text

# The official documents are in English; a farmer asks in Hindi or Marathi. Native-script scheme words are searched with their English names.
RETRIEVAL_TERMS = {
    "एमएसपी": "MSP minimum support price", "समर्थन मूल्य": "minimum support price MSP", "हमीभाव": "minimum support price MSP", "आधारभूत किंमत": "minimum support price MSP",
    "पीएम किसान": "PM-KISAN instalment", "किसान सम्मान": "PM-KISAN", "किसान निधी": "PM-KISAN", "किस्त": "instalment", "हप्ता": "instalment",
    "फसल बीमा": "crop insurance PMFBY premium", "पीक विमा": "crop insurance PMFBY premium", "प्रीमियम": "premium", "हप्त्याचा विमा": "premium",
    "साठवण": "storage losses", "भंडारण": "storage losses", "नुकसान": "loss", "नुक्सान": "loss",
    "खरीफ": "Kharif", "खरीप": "Kharif", "रबी": "Rabi", "रब्बी": "Rabi",
}


AMOUNT_QUESTION = re.compile(r"how\s+much|\bpay|\bpaid|amount|कितना|कितनी|कितने|किती|रुपये|रुपए|मिलते|मिळते", re.I)


def retrieval_query(text: str, profile: dict) -> str:
    """Question + English names for its native-script scheme words + the resolved crop in English (documents list "Paddy", not "धान")."""
    extra = [en for native, en in RETRIEVAL_TERMS.items() if native in (text or "")]
    scheme_free = not re.search(r"\bMSP\b|\bsupport price|storage", " ".join(extra) + " " + (text or ""), re.I)  # an MSP or storage question is not about scheme amounts
    if AMOUNT_QUESTION.search(text or "") and scheme_free:
        extra.append("amount annual financial assistance Rs instalments premium")  # "how much does it pay" shares no word with "annual financial assistance of Rs. 6,000"
    crop = profile.get("crop") or ""
    names = {crop_key(crop), table_key(crop)} - {""} if crop else set()  # paddy AND rice, soyabean AND soybean: documents use the plain name
    return " ".join([text, *extra, *sorted(names)])


def question_overrides(profile: dict, named: dict, text: str) -> dict:
    """A farmer who asks "price of tomato in Pune" wants Pune and tomato, whatever the profile form still says.

    Crop: found by code (known crops, Hindi/Marathi/Hinglish names) when exactly one is named; a crop the code does not know
    ("dragon fruit") comes from the router, already checked to occur in the question. Agmarknet then says honestly if it has
    no such commodity, instead of the profile's crop being used for a question about another crop.
    Place: the router's reading, same check; the live geocoder still has the last word.
    """
    over: dict = {}
    crops = crops_in_text(text)
    crop = crops[0].title() if len(crops) == 1 else "" if crops else " ".join((named.get("crop") or "").split()).title()
    if crop and crop_key(crop) != crop_key(profile.get("crop", "")):
        over["crop"] = crop
    place = " ".join((named.get("place") or "").split())
    if place and place.lower() not in FILLER and place.lower() not in (profile.get("place") or "").lower():
        over["place"] = place
    return over
