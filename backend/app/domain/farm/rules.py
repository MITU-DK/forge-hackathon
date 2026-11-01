"""Guardrails for farm answers. The LLM proposes; these rules decide what is delivered.

HARD rules protect safety and correctness: a draft that still breaks one after the rewrites is replaced by the safe answer.
SOFT rules cover style and attribution: one rewrite is asked for, then the draft is delivered (the trace says so).
"""
from __future__ import annotations

from app.domain.diagnosis import BRAND_PATTERN, RATE_PATTERN
from app.domain.farm.checks import cites_sources, own_market_claim
from app.guardrails.validators import Validator, forbid_patterns, forbid_unless_negated, grounded_dates, grounded_numbers, grounded_units, max_words, numeric_limit

HARD_VALIDATORS: list[Validator] = [
    grounded_numbers(),  # every number in the answer must exist in tool output
    grounded_dates(),  # ...and so must every date
    grounded_units(),  # ...and a Rs / % / mm figure must be that kind of figure in the data
    own_market_claim(),
    forbid_patterns({  # a model that answers the correction note instead of the farmer prints its own reasoning: never let that reach a farmer
        r"\banaly[sz]e the (?:request|question|user)\b|\bthe user (?:is|has|wants|asks|asked|said|pointed)\b|\bviolation\s*\d|\bmy (?:previous|earlier|last) (?:response|answer|draft)\b|\blet me (?:re-?read|re-?check|think|check|re-?write)\b|\blet'?s (?:check|re-?read|see|think)\b|\bi need to (?:remove|use|rewrite|fix|re-?check)\b|\bwait,|\bconstraints?:|-> ?(?:OK|fine|good)\b": "Contains your own reasoning or talks about the rules or the user in the third person. Write only the final answer for the farmer, in the farmer's language, nothing else.",
    }),
    forbid_patterns({  # Hindi / Marathi: a future-tense statement about price ("कीमतें अगले हफ्ते स्थिर रहेंगी", "भाव वाढतील")
        r"(?:कीमत|भाव|दाम)\w*[^\u0964.]{0,70}(?:रहेंगी|रहेंगे|बढ़ेंगी|बढ़ेंगे|बढ़ेगा|गिरेंगी|गिरेंगे|गिरेगा|घटेंगी|घटेंगे|बढ़ने की संभावना|गिरने की संभावना)": "Predicts a price move. Say you cannot forecast prices; the trend only describes the past week.",
        r"(?:किंमत|भाव|दर)\w*[^\u0964.]{0,70}(?:राहतील|वाढतील|घटतील|कमी होतील|वाढणार|घटणार|वाढेल|कमी होईल|स्थिर राहील)": "Predicts a price move. Say you cannot forecast prices; the trend only describes the past week.",
    }),
    forbid_patterns({
        r"\bprices?\b[^.]{0,60}\b(?:expected|likely|will|may|might|could)\b[^.]{0,30}\b(?:rise|increase|go up|fall|drop|go down|higher|lower)\b": "Predicts a price move. The trend is only what already happened; say you cannot forecast prices.",
        r"\b(?:rise|increase|fall|drop)\b[^.]{0,20}\b(?:expected|likely)\b[^.]{0,30}\bnext\s+(?:week|month|few days)\b|\bnext\s+(?:week|month)\b[^.]{0,60}\b(?:rise|increase|fall|drop)\b[^.]{0,20}\b(?:expected|indicated|likely)\b": "Predicts a price move for next week. Say plainly that you cannot forecast prices.",
    }),
    numeric_limit("pesticide dose", r"([\d.]+)\s*(?:ml|g)\s*(?:per|/)\s*(?:litre|liter|l)\b", hi=0.0, unit=" per litre"),  # no dose advice at all
    forbid_unless_negated({r"\b(guarantee[d]?|100\s*%\s*sure|definitely will|sure profit|risk[- ]free)\b": "Makes a guarantee. Rephrase as decision support."}),
    # Hindi / Marathi (the negation can follow the noun, hence after=25): "\u0917\u093e\u0930\u0902\u091f\u0940", "\u092a\u0915\u094d\u0915\u093e \u092e\u0941\u0928\u093e\u092b\u093e", "\u0928\u0915\u094d\u0915\u0940 \u0928\u092b\u093e", "\u0916\u093e\u0924\u094d\u0930\u0940 \u0906\u0939\u0947"
    forbid_unless_negated({
        r"\u0917\u093e\u0930\u0902\u091f\u0940|\u0917\u0945\u0930\u0902\u091f\u0940|\u0917\u093e\u0930\u0923\u094d\u091f\u0940|\u0939\u092e\u0940|\u092a\u0915\u094d\u0915\u093e\s+(?:\u092e\u0941\u0928\u093e\u092b\u093e|\u092b\u093e\u092f\u0926\u093e|\u0932\u093e\u092d|\u0928\u092b\u093e)|\u0928\u093f\u0936\u094d\u091a\u093f\u0924(?:\s+\u0930\u0942\u092a\s+\u0938\u0947)?\s+(?:\u092e\u0941\u0928\u093e\u092b\u093e|\u0932\u093e\u092d|\u0928\u092b\u093e)|\u0928\u0915\u094d\u0915\u0940\s+(?:\u0928\u092b\u093e|\u092b\u093e\u092f\u0926\u093e)|\u0916\u093e\u0924\u094d\u0930\u0940\s+\u0906\u0939\u0947": "Makes a guarantee. Rephrase as decision support.",
    }, after=25),
    # Other UI languages. Only words that mean "guarantee" and not the everyday "make sure" (Tamil \u0b89\u0bb1\u0bc1\u0ba4\u0bbf, Gujarati \u0a96\u0abe\u0aa4\u0ab0\u0ac0 are left out on purpose).
    # Written by a non-native speaker: have them reviewed. The Latin brands, ungrounded numbers and doses are caught in every language.
    forbid_unless_negated({
        r"\u0b89\u0ba4\u0bcd\u0ba4\u0bb0\u0bb5\u0bbe\u0ba4\u0bae\u0bcd|\u0ba8\u0bbf\u0b9a\u0bcd\u0b9a\u0baf\u0bae\u0bbe\u0b95\s+\u0bb2\u0bbe\u0baa\u0bae\u0bcd": "Makes a guarantee. Rephrase as decision support.",  # ta
        r"\u0c39\u0c3e\u0c2e\u0c40|\u0c17\u0c4d\u0c2f\u0c3e\u0c30\u0c02\u0c1f\u0c40": "Makes a guarantee. Rephrase as decision support.",  # te
        r"\u0c97\u0ccd\u0caf\u0cbe\u0cb0\u0c82\u0c9f\u0cbf|\u0c96\u0cbe\u0ca4\u0cb0\u0cbf\u0caf\u0cbe\u0c97\u0cbf\s+\u0cb2\u0cbe\u0cad": "Makes a guarantee. Rephrase as decision support.",  # kn
        r"\u0997\u09cd\u09af\u09be\u09b0\u09be\u09a8\u09cd\u099f\u09bf|\u09a8\u09bf\u09b6\u09cd\u099a\u09af\u09bc\u09a4\u09be": "Makes a guarantee. Rephrase as decision support.",  # bn
        r"\u0a97\u0ac7\u0ab0\u0a82\u0a9f\u0ac0|\u0aa8\u0a95\u0acd\u0a95\u0ac0\s+\u0aa8\u0aab\u0acb": "Makes a guarantee. Rephrase as decision support.",  # gu
        r"\u0a17\u0a3e\u0a30\u0a70\u0a1f\u0a40|\u0a2a\u0a71\u0a15\u0a3e\s+\u0a2e\u0a41\u0a28\u0a3e\u0a2b\u0a3c\u0a3e": "Makes a guarantee. Rephrase as decision support.",  # pa
        r"\u0d17\u0d4d\u0d2f\u0d3e\u0d30\u0d23\u0d4d\u0d1f\u0d3f|\u0d09\u0d31\u0d2a\u0d4d\u0d2a\u0d4d\s+\u0d32\u0d3e\u0d2d\u0d02": "Makes a guarantee. Rephrase as decision support.",  # ml
    }, after=25),
    forbid_patterns({
        r"ignore\s+(your|all|previous)\s+(rules|instructions|system)": "Repeats an instruction-override attempt.",
        r"you\s+are\s+now\s+(a|an)\s+": "Accepts a role change.",
        r"system\s+prompt": "Mentions internal instructions.",
    }),
    # An eligibility VERDICT about this farmer, in either direction ("you are eligible", "a holding of 2 acres would meet the condition"):
    # the documents state conditions; only the state agriculture office, the bank or the portal decides a person's case.
    forbid_patterns({
        r"\byou\s+(?:are|would\s+be|will\s+be|qualify|can\s+get|would\s+get|aren't|are\s+not|do\s+not\s+qualify|don't\s+qualify)\b[^.]{0,30}\b(?:eligible|qualif\w*|entitled)\b|\byou\s+qualify\b": "Decides whether the farmer is eligible. State the published conditions and say the agriculture office, bank or official portal decides.",
        r"\b(?:your|a)\s+(?:\d+[\d.]*[- ]\w+\s+)?(?:holding|land|farm)\b[^.]{0,50}\b(?:meets?|satisf\w+|fulfil\w*|qualif\w+|eligible)\b|\bwould\s+(?:generally\s+)?meet\b": "Applies the conditions to the farmer's own land. State the published conditions only.",
        r"\u0906\u092a\s+\u092a\u093e\u0924\u094d\u0930|\u092a\u093e\u0924\u094d\u0930\s+\u0939\u0948\u0902?\s+\u0906\u092a|\u0924\u0941\u092e\u094d\u0939\u0940\s+\u092a\u093e\u0924\u094d\u0930|\u0924\u0941\u092e\u094d\u0939\u0940\s+\u092a\u093e\u0924\u094d\u0930": "Decides whether the farmer is eligible.",
    }),
    # The model's own reasoning or a mention of its instructions must never be delivered as an answer (seen on 31 Oct 2026 from the
    # fallback model: "The user is asking... I need to follow the rules strictly...").
    forbid_patterns({
        r"(?-i:\bCONTEXT\b)|\bthe\s+user\s+(?:is\s+asking|asks|wants|said)\b|\bI\s+(?:need|must)\s+(?:to\s+)?(?:follow|lead\s+with|cite)\b|\bthe\s+(?:instructions|guidance)\s+(?:say|says|state|states)\b|(?-i:\bthe\s+DECISION\s+(?:says|is)\b)": "Shows the assistant's own reasoning or instructions. Write only the answer for the farmer.",
    }),
    forbid_patterns({BRAND_PATTERN: "Do not mention pesticide brand names.", RATE_PATTERN: "Do not recommend pesticide or fertiliser application rates."}),
    forbid_patterns({r"IMAGE\s+(ASSESSMENT|ANALYSIS)": "Do not mention internal labels. Say 'from your photo' instead."}),
]

# SOFT rules: style and attribution. One rewrite is asked for; if they still fail the draft is delivered (the trace says so),
# because the safe fallback would be a worse answer for the farmer than a slightly clumsy true one.
SOFT_VALIDATORS: list[Validator] = [
    cites_sources(),  # a Rs figure names Agmarknet, a weather figure names Open-Meteo
    forbid_patterns({
        r"\borganic carbon\b[^.]{0,40}\b(?:good|poor|low|high|rich|adequate|sufficient|ideal)\b": "Judges the organic carbon (good, low...). There is no verified threshold: state the number only.",
        r"\bsoil(?: moisture)?\s+(?:is|are|was|looks|seems|appears|remains)\s+(?:\w+\s+){0,2}(?:dry|wet|low|high|moist|saturated|waterlogged)\b": "Judges the soil (dry, wet, low...). Only the numbers are known; state them without a verdict.",
    }),
    forbid_patterns({
        r"[\u0400-\u04ff\u0370-\u03ff\u0590-\u06ff\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]": "Contains a word in a foreign script (for example Russian or Chinese). Use only the farmer's language and plain digits.",
        r"\b(?-i:(?=[A-Za-z_]*[a-z]))[A-Za-z]+(?:_[A-Za-z]+)+\b": "Prints an internal rule label (words joined by underscores). Say it in plain words in the farmer's language.",
        r"[\u3010\u3011\u300c\u300d]|\((?:[^()]*\b)?(?:code|decision|market position|highest markets|water balance|risk flags?|crop fit)\s*(?:code)?[,)]": "Names an internal part of the system as a source (for example 'Market Position code' or 'DECISION'). Name only the data source, such as Open-Meteo or Agmarknet, and its date.",
    }),
    forbid_patterns({
        r"[0-9][\u0966-\u096f]|[\u0966-\u096f][0-9]": "Mixes Devanagari and ordinary digits inside one number (for example 202\u096c). Write every number with ordinary digits only.",
        r"[A-Za-z][\u0900-\u097f]|[\u0900-\u097f][A-Za-z]": "Mixes Latin and Devanagari letters inside one word. Write each word fully in one script.",
    }),
    max_words(200),
]
