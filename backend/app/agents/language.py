"""Which language a farmer is writing in, and whether a keyword occurs in their message.

Used by the router: its LLM answer is trusted for the intent, but the script and the unambiguous Hindi or Marathi words
in the message have the last word on the language (the LLM confuses the two Devanagari languages).
"""
from __future__ import annotations

import re

LANG_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "kn": "Kannada", "ml": "Malayalam"}


# Unicode block -> language, used only when the router LLM is down (Devanagari defaults to Hindi; Marathi shares the script).
SCRIPT_LANG = [("\u0900", "\u097f", "hi"), ("\u0980", "\u09ff", "bn"), ("\u0a00", "\u0a7f", "pa"), ("\u0a80", "\u0aff", "gu"),
               ("\u0b80", "\u0bff", "ta"), ("\u0c00", "\u0c7f", "te"), ("\u0c80", "\u0cff", "kn"), ("\u0d00", "\u0d7f", "ml")]


# Hindi and Marathi share Devanagari, and the router LLM confuses them (a Marathi question was answered in Hindi on 31 Oct 2026).
# Words that exist in only one of the two settle it; a message with markers of both, or neither, is left to the caller.
_MARATHI_WORDS = re.compile(r"(?<![\u0900-\u097f])(?:आहे|आहेत|आहात|काय|मला|माझ\w*|आणि|पाऊस|विकू|थांबू|करू|करावे|किती|नाही|तुमच\w*|आमच\w*)(?![\u0900-\u097f])")
_HINDI_WORDS = re.compile(r"(?<![\u0900-\u097f])(?:है|हैं|क्या|मुझे|मेरा|मेरी|मेरे|और|बारिश|बेचूं|बेचूँ|रुकूं|करूं|कितना|कितनी|नहीं|आपका|आपके)(?![\u0900-\u097f])")


def devanagari_lang(text: str) -> str | None:
    mr, hi = bool(_MARATHI_WORDS.search(text)), bool(_HINDI_WORDS.search(text))
    return "mr" if mr and not hi else "hi" if hi and not mr else None


def _has_keyword(k: str, low: str) -> bool:
    """Latin keywords match at the start of a word ("irrigat" finds "irrigation", but "heat" no longer finds "wheat"); native-script ones as substrings."""
    return bool(re.search(r"(?<![a-z])" + re.escape(k), low)) if k.isascii() else k in low


def is_romanised(text: str, chosen_language: str | None, lang: str) -> bool:
    """Hindi or Marathi typed in Roman letters ("pyaz ka bhav kya hai"): answer in Roman letters too. Only when the farmer did not
    pick a language in the UI (a chosen language is respected) and the message has no native script at all."""
    return chosen_language is None and lang in ("hi", "mr") and script_lang(text or "") == "en" and any(c.isalpha() for c in (text or ""))


def script_lang(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        for lo, hi, code in SCRIPT_LANG:
            if lo <= ch <= hi:
                counts[code] = counts.get(code, 0) + 1
    code = max(counts, key=counts.get) if counts else "en"
    return (devanagari_lang(text) or "hi") if code == "hi" else code
