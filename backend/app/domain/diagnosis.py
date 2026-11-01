"""Crop photo -> structured, safe assessment. The vision model describes; this code decides what is shown.

Only symptoms / severity / possible causes come from the model, and each is sanitised: no brands, no application
rates, bounded length. Recommendations and disclaimer are fixed safe text, never model output.
"""
from __future__ import annotations

import re

from app.llm.client import parse_json

VISION_PROMPT = (
    "You are looking at a farm / crop photo. Reply with JSON only, no other text:\n"
    '{"crop": "<crop or plant visible, or unknown>", "symptoms": ["<specific visible symptom>", ...], '
    '"severity": "mild | moderate | severe | none", "possible_causes": ["<factual possible cause>", ...]}\n'
    "Rules: describe only what is visible (colour changes, spots, wilting, holes, pests). At most 5 items per list. "
    "Use words, not numbers or percentages. Do NOT recommend pesticides, brands, doses or treatments. "
    "If the photo is not a plant or crop, set crop to unknown and symptoms to []."
)

BRAND_PATTERN = (
    r"\b(?:Roundup|Bayer|Syngenta|BASF|Corteva|Tata\s+Rallis|UPL|Dhanuka|FMC|Confidor|Antracol|Ridomil|Coragen|Amistar|Nativo|Tafgor|Lancer|Actara|Regent)\b"
    r"|\u0930\u093e\u0909\u0902\u0921\u0905\u092a|\u0930\u093e\u0909\u0902\u0921-?\u0905\u092a|\u0930\u093f\u0921\u094b\u092e\u093f\u0932|\u0915\u0949\u0928\u094d\u092b\u093f\u0921\u0949\u0930|\u0915\u0949\u0928\u094d\u092b\u093f\u0921\u094b\u0930|\u092c\u093e\u092f\u0930|\u0938\u093f\u0902\u091c\u0947\u0902\u091f\u093e|\u0938\u093f\u0902\u0917\u0947\u0902\u091f\u093e|\u092f\u0942\u092a\u0940\u090f\u0932|\u0927\u093e\u0928\u0941\u0915\u093e|\u0915\u094b\u0930\u094d\u091f\u0947\u0935\u093e|\u0915\u094b\u0930\u093e\u091c\u0947\u0928"
)
RATE_PATTERN = (
    r"[\d.]+\s*(?:ml|g|gm|kg|l|litre|liter)s?\s*(?:per|/)\s*(?:acre|hectare|ha|bigha|litre|liter|l)\b"
    # Hindi/Marathi: "20 \u092e\u093f\u0932\u0940 \u092a\u094d\u0930\u0924\u093f \u0932\u0940\u091f\u0930", "\u090f\u0915\u0930\u0940 500 \u0917\u094d\u0930\u0945\u092e", "\u092a\u094d\u0930\u0924\u093f \u090f\u0915\u0921\u093c 2 \u0915\u093f\u0932\u094b"
    r"|[\d.]+\s*(?:\u092e\u093f\u0932\u0940|\u092e\u093f\.\u0932\u0940\.?|\u0917\u094d\u0930\u093e\u092e|\u0917\u094d\u0930\u0945\u092e|\u0915\u093f\u0932\u094b|\u0932\u0940\u091f\u0930|\u0932\u093f\u091f\u0930)\s*(?:\u092a\u094d\u0930\u0924\u093f|/)\s*(?:\u090f\u0915\u0921\u093c|\u090f\u0915\u0930|\u0939\u0947\u0915\u094d\u091f\u0930|\u0939\u0947\u0915\u094d\u091f\u0947\u092f\u0930|\u0932\u0940\u091f\u0930|\u0932\u093f\u091f\u0930|\u092c\u0940\u0918\u093e)"
    r"|(?:\u090f\u0915\u0930\u0940|\u092a\u094d\u0930\u0924\u093f \u090f\u0915\u0921\u093c|\u092a\u094d\u0930\u0924\u093f \u090f\u0915\u0930)\s*[\d.]+\s*(?:\u092e\u093f\u0932\u0940|\u0917\u094d\u0930\u093e\u092e|\u0917\u094d\u0930\u0945\u092e|\u0915\u093f\u0932\u094b|\u0932\u0940\u091f\u0930|\u0932\u093f\u091f\u0930)"
)
_UNSAFE = re.compile(f"{BRAND_PATTERN}|{RATE_PATTERN}", re.I)
# a model sometimes slips a Cyrillic, Chinese, Arabic... letter into a Hindi word ("गнить"): such a line is dropped, never shown
_FOREIGN = re.compile(r"[\u0400-\u04ff\u0370-\u03ff\u0590-\u06ff\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
SEVERITIES = {"none", "mild", "moderate", "severe"}
RECOMMENDATIONS = [
    "Show the affected plants to your local Krishi Vigyan Kendra (KVK) or agriculture officer.",
    "If any treatment is advised, follow the product label.",
]
DISCLAIMER = "This is a visual assessment from a photo only. Confirm with your agriculture officer before acting."


def _clean(items, limit: int = 5) -> list[str]:
    """Short, brand-free, dose-free strings from a model-supplied list."""
    if not isinstance(items, list):
        return []
    safe = [x.strip()[:120] for x in items if isinstance(x, str) and x.strip() and not _UNSAFE.search(x) and not _FOREIGN.search(x)]
    return safe[:limit]


def _summary(crop: str, severity: str, symptoms: list[str], causes: list[str]) -> str:
    return (
        f"IMAGE ASSESSMENT (visual only, from the farmer's photo): crop {crop.strip()[:60]}; severity {severity}; "
        f"symptoms: {'; '.join(symptoms) or 'none visible'}; possible causes: {'; '.join(causes) or 'cannot tell from the photo'}. "
        "The photo has ALREADY been received and analysed: never ask the farmer to send or describe a photo. If the crop seen differs from the farmer's crop, say that plainly and still report what is visible. "
        "Say 'from your photo' and never repeat the label IMAGE ASSESSMENT. Advise the farmer to confirm with the local KVK / agriculture officer. Do not recommend pesticides, brands or doses."
    )


def parse_diagnosis(text: str) -> dict | None:
    """Returns {summary, card} or None when the model output is not usable (caller then says the photo could not be assessed)."""
    d = parse_json(text)
    if not d:
        return None
    crop = d.get("crop") if isinstance(d.get("crop"), str) and not _UNSAFE.search(d["crop"]) else "unknown"
    severity = str(d.get("severity", "")).strip().lower()
    severity = severity if severity in SEVERITIES else "unknown"
    symptoms, causes = _clean(d.get("symptoms")), _clean(d.get("possible_causes"))
    card = {
        "type": "diagnosis",
        "title": "Crop health assessment",
        "symptoms": symptoms,
        "severity": severity,
        "possible_causes": causes,
        "recommendations": RECOMMENDATIONS,
        "disclaimer": DISCLAIMER,
    }
    return {"summary": _summary(crop, severity, symptoms, causes), "card": card}


def language_note(language_name: str) -> str:
    """Appended to the vision prompt when the farmer's language is not English: only the free-text phrases change language,
    the JSON keys and the severity word stay in English so the parser and the safety filters still work."""
    return (f"\nWrite every symptom and possible cause phrase in {language_name}, in plain words a farmer uses. "
            "Keep the JSON keys and the severity word (mild, moderate, severe, none) in English.")
