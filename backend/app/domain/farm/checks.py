"""Two guardrails that need the CONTEXT block as well as the answer: own-market claims and source citations."""
from __future__ import annotations

import re

from app.guardrails.validators import Validator


def own_market_claim() -> "Validator":
    """The answer may call a market the farmer's own only when the tool found one whose name matches the farmer's place
    (YOUR MARKET line). Otherwise the model picks the first market of the district and calls it "your mandi"."""
    claim = re.compile(r"\byour (?:own |local )?(?:market|mandi)\b|आपकी (?:अपनी )?मंडी|आपके (?:अपने )?(?:बाजार|मंडी)|तुमचा बाजार|तुमची मंडी|आपली मंडी|तुमच्या (?:बाजारात|मंडीत)", re.I)

    def check(text: str, context: str = "") -> list[str]:
        if claim.search(text) and "YOUR MARKET" not in context:
            return ["Calls a market the farmer's own, but CONTEXT has no YOUR MARKET line. Say 'markets in <district>' instead."]
        return []

    check.wants_context = True  # type: ignore[attr-defined]
    return check


def cites_sources() -> "Validator":
    """A price or weather figure in the answer must come with the name of its source in the text. The UI card always lists
    sources with fetch times, but the answer is also read aloud and copied on its own, so the claim "a source on every number"
    is enforced here, per data source: Agmarknet for rupee amounts, Open-Meteo for rain, temperature and wind."""
    money = re.compile(r"(?:Rs\.?|\u20b9)\s*\d|\d\s*(?:\u0930\u0941\u092a\u092f\u0947|\u0930\u0941\b)")
    weather = re.compile(r"\d\s*(?:mm\b|\u00b0\s*C\b|km/h)")
    agmarknet = re.compile(r"agmarknet|data\.gov\.in|\u090f\u0917\u092e\u093e\u0930\u094d\u0915\u0928\u0947\u091f|\u090f\u0917\u094d\u092e\u093e\u0930\u094d\u0915\u0928\u0947\u091f|\u0905\u0945\u0917\u092e\u093e\u0930\u094d\u0915\u0928\u0947\u091f", re.I)
    meteo = re.compile(r"open[\s\-\u2010\u2011]?meteo|\u0913\u092a\u0928[\s\-]?\u092e\u0940\u091f\u093f\u092f\u094b", re.I)

    def check(text: str, context: str = "") -> list[str]:
        out = []
        if "MANDI PRICES" in context and money.search(text) and not agmarknet.search(text):
            out.append("Gives a price without naming its source. Say it comes from data.gov.in Agmarknet.")
        if "SOIL for" in context and re.search(r"\bpH\b|\u092a\u0940\u090f\u091a|texture|\u092c\u0928\u093e\u0935\u091f|\u0938\u092b\u093e\u0908", text, re.I) and not re.search(r"estimate|SoilGrids|model|\u0905\u0928\u0941\u092e\u093e\u0928|\u0905\u0902\u0926\u093e\u091c|\u092e\u0949\u0921\u0932", text, re.I):
            out.append("Gives soil figures without saying they are a model estimate (250 m, ISRIC SoilGrids), not a measurement of the field. Say so, and suggest a soil test.")
        if "WEATHER for" in context and weather.search(text) and not meteo.search(text):
            out.append("Gives a weather figure without naming its source. Say it comes from Open-Meteo.")
        return out

    check.wants_context = True  # type: ignore[attr-defined]
    return check
