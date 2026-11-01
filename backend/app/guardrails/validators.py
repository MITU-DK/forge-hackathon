"""Deterministic guardrails. The LLM proposes, plain code disposes.

(Pattern from Demeter: `validate_hard_limits` + reject-with-critique loop.)
A validator takes the draft answer text and returns a list of violation strings.
Empty list == OK. Violations are fed back to the agent once as a critique; if it
still fails, the orchestrator returns the domain's safe fallback instead.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable

Validator = Callable[[str], list[str]]


def _wants_context(check):
    """Mark a validator that takes (text, context): run_all passes the CONTEXT block only to those."""
    check.wants_context = True
    return check


def forbid_patterns(rules: dict[str, str]) -> Validator:
    """rules: {regex: message}. Flags any regex that matches (case-insensitive)."""
    compiled = [(re.compile(p, re.I), m) for p, m in rules.items()]

    def check(text: str) -> list[str]:
        return [m for rx, m in compiled if rx.search(text)]

    return check


# The LLM writes typographic apostrophes ("can\u2019t"), so both quote characters must count. Hindi and Marathi put the negation
# AFTER the noun ("\u0917\u093e\u0930\u0902\u091f\u0940 \u0928\u0939\u0940\u0902 \u0926\u0947 \u0938\u0915\u0924\u093e", "\u0939\u092e\u0940 \u0926\u0947\u0924\u093e \u092f\u0947\u0924 \u0928\u093e\u0939\u0940"), so those rules also look ahead.
_NEGATION = re.compile(
    r"\b(?:not|no|never|cannot|cant|unable|without|nobody|nothing)\b|n['\u2019]t"
    r"|(?<![\u0900-\u097f])(?:\u0928\u0939\u0940\u0902|\u0928\u0939\u0940|\u0928\u093e\u0939\u0940|\u0928\u0915\u094b|\u092c\u093f\u0928\u093e|\u0936\u093f\u0935\u093e\u092f|\u0915\u092d\u0940 \u0928\u0939\u0940\u0902)(?![\u0900-\u097f])",
    re.I,
)
# Negations in Tamil, Telugu, Kannada, Bengali, Gujarati, Punjabi and Malayalam (plain substrings: these scripts have no word-boundary class here).
_NEGATION_OTHER = re.compile("|".join([
    "\u0b87\u0bb2\u0bcd\u0bb2\u0bc8", "\u0bae\u0bc1\u0b9f\u0bbf\u0baf\u0bbe\u0ba4\u0bc1", "\u0b85\u0bb2\u0bcd\u0bb2",  # ta: illai, mudiyathu, alla
    "\u0c32\u0c47\u0c26\u0c41", "\u0c15\u0c3e\u0c26\u0c41", "\u0c07\u0c35\u0c4d\u0c35\u0c32\u0c47\u0c28\u0c41",  # te: ledu, kadu, ivvalenu
    "\u0c87\u0cb2\u0ccd\u0cb2", "\u0cb8\u0cbe\u0ca7\u0ccd\u0caf\u0cb5\u0cbf\u0cb2\u0ccd\u0cb2",  # kn: illa, saadhyavilla
    "\u09a8\u09c7\u0987", "\u09a8\u09be\u0983?(?![\u0980-\u09ff])",  # bn: nei, na
    "\u0aa8\u0aa5\u0ac0",  # gu: nathi
    "\u0a28\u0a39\u0a40\u0a02", "\u0a28\u0a39\u0a40",  # pa: nahin
    "\u0d07\u0d32\u0d4d\u0d32", "\u0d15\u0d34\u0d3f\u0d2f\u0d3f\u0d32\u0d4d\u0d32",  # ml: illa, kazhiyilla
]))
_CLAUSE_END = r"[.!?;\n\u0964]"  # \u0964 = danda, the Hindi/Marathi full stop


def forbid_unless_negated(rules: dict[str, str], window: int = 30, after: int = 0) -> Validator:
    """Like forbid_patterns, but a match is ignored when the same clause negates it ("I cannot guarantee profit",
    "this is not risk-free"). Needed so a correct refusal is not rejected as if it were the forbidden claim.
    `after` > 0 also looks that many characters past the match, for languages that negate after the noun."""
    compiled = [(re.compile(p, re.I), m) for p, m in rules.items()]

    def clause_around(text: str, m: re.Match) -> str:
        clause = re.split(_CLAUSE_END, text[max(0, m.start() - window):m.start()])[-1]
        if after:
            clause += " " + re.split(_CLAUSE_END, text[m.end():m.end() + after])[0]
        return clause

    def negated(clause: str) -> bool:
        return bool(_NEGATION.search(clause) or _NEGATION_OTHER.search(clause))

    def check(text: str) -> list[str]:
        out = []
        for rx, msg in compiled:
            if any(not negated(clause_around(text, m)) for m in rx.finditer(text)):
                out.append(msg)
        return out

    return check


def numeric_limit(label: str, pattern: str, lo: float | None = None, hi: float | None = None, unit: str = "") -> Validator:
    """Extract a number via regex group 1 and enforce a hard range.

    numeric_limit("pH", r"pH\\s*(?:of|to|=)?\\s*([\\d.]+)", 5.0, 7.5)
    """
    rx = re.compile(pattern, re.I)

    def check(text: str) -> list[str]:
        out = []
        for m in rx.finditer(text):
            try:
                value = float(m.group(1))
            except ValueError:
                continue
            if lo is not None and value < lo:
                out.append(f"{label}={value}{unit} is below the safe minimum {lo}{unit}.")
            if hi is not None and value > hi:
                out.append(f"{label}={value}{unit} is above the safe maximum {hi}{unit}.")
        return out

    return check


def max_words(n: int) -> Validator:
    def check(text: str) -> list[str]:
        words = len(text.split())
        return [f"Answer is {words} words; limit is {n}. Shorten it."] if words > n else []

    return check


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _ascii_digits(s: str) -> str:
    out = []
    for c in s:
        if c.isdigit() and not c.isascii():
            try:
                c = str(unicodedata.digit(c))
            except ValueError:
                pass
        out.append(c)
    return "".join(out)


def _norm_num(n: str) -> str:
    return (str(float(n)).rstrip("0").rstrip(".")) if "." in n else str(int(n))


# LLMs (Hindi and Marathi answers especially) write 5500 as "5 500" or with a thin/no-break space. Read as two numbers that gave
# "500 is not in the data", the critique never explained why, and every rewrite repeated it until the safe fallback. Left side
# is 1-3 digits not preceded by a digit, dot, comma, hyphen, colon or slash, so "2026-11-02 100 mm" is never merged.
_GROUPED = re.compile(r"(?<![\d.,:/\-])\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?!\d)")


def _plain_number_text(s: str) -> str:
    s = _GROUPED.sub(lambda m: re.sub(r"\D", "", m.group(0)), _ascii_digits(s))
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)  # 2,450 -> 2450


def numbers_in(s: str) -> set[str]:
    s = _plain_number_text(s)
    return {_norm_num(n) for n in _NUM.findall(s)}


def _with_roundings(numbers: set[str]) -> set[str]:
    """The numbers plus their integer and rounded forms (33.1 also allows 33)."""
    out = set(numbers)
    for n in numbers:
        f = float(n)
        out.update({str(int(f)), str(round(f))})
    return out


def _is_checked(n: str, min_value: float) -> bool:
    """Small whole numbers (counts, days) are exempt; anything with decimals or at least `min_value` is checked."""
    return float(n) >= min_value or "." in n


def grounded_numbers(min_value: float = 10.0) -> Validator:
    """Anti-hallucination: every number >= min_value (or with decimals) in the answer must appear in the
    CONTEXT (tool outputs + user message). Rounded versions of context numbers are allowed (33.1 -> 33)."""

    def check(text: str, context: str = "") -> list[str]:
        allowed = _with_roundings(numbers_in(context))
        bad = sorted(n for n in numbers_in(text) if _is_checked(n, min_value) and n not in allowed)
        return [f"These numbers are not in the provided data: {', '.join(bad)}. Use only numbers from the CONTEXT."] if bad else []

    return _wants_context(check)


_AMT = r"(\d+(?:\.\d+)?)"
_MONEY = re.compile(rf"(?:Rs\.?|\u20b9)\s*{_AMT}|{_AMT}\s*(?:rupees|\u0930\u0941\u092a\u092f\u0947|\u0930\u0941\u092a\u092f\u093e|\u0930\u0941\u092a\u090f|\u0930\u0941\.?(?!\w))", re.I)
_PERCENT = re.compile(rf"{_AMT}\s*(?:%|\u091f\u0915\u094d\u0915\u0947|\u092a\u094d\u0930\u0924\u093f\u0936\u0924|percent)", re.I)
_MM = re.compile(rf"{_AMT}\s*(?:mm\b|\u092e\u093f\u092e\u0940|\u092e\u093f\.\u092e\u0940)", re.I)


def _unit_pairs(text: str) -> set[tuple[str, str]]:
    text = _plain_number_text(text)
    out = set()
    for unit, rx in (("Rs", _MONEY), ("%", _PERCENT), ("mm", _MM)):
        for m in rx.finditer(text):
            n = next(g for g in m.groups() if g)
            out.add((unit, _norm_num(n)))
    return out


def grounded_units(min_value: float = 10.0) -> Validator:
    """A rupee, percent or millimetre figure must be that same kind of figure in the CONTEXT. grounded_numbers only checks that
    the digits occur somewhere, so "Rs 3.7" (a percent change) or "3700 mm" (a price) would pass it. Same small-integer
    exemption and rounding allowance as grounded_numbers; unrecognised units/languages are simply not checked here."""

    def check(text: str, context: str = "") -> list[str]:
        allowed = set(pairs := _unit_pairs(context))
        for u, n in pairs:
            f = float(n)
            allowed.update({(u, str(int(f))), (u, str(round(f)))})
        # Official documents carry bare numbers in tables ("Wheat | 2610 | 1264"): whatever unit the farmer is told, the figure is theirs.
        doc_numbers = numbers_in("\n".join(seg for seg in context.split("\n---\n") if seg.lstrip().startswith("[")))
        known = numbers_in(context)  # a number absent from the context entirely is grounded_numbers' finding, not ours
        bad = sorted(
            f"{n} {u}" for u, n in _unit_pairs(text)
            if _is_checked(n, min_value) and n in known and n not in doc_numbers and (u, n) not in allowed
        )
        return [f"These figures are in the data but not as that kind of amount: {', '.join(bad)}. Use each number only with its own unit from the CONTEXT."] if bad else []

    return _wants_context(check)


_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|your\s+|the\s+|any\s+)?(?:previous\s+|prior\s+|above\s+)?(?:rules|instructions|system|prompt)"
    r"|you\s+are\s+now\s+(?:a|an)\s+|disregard\s+(?:your|the|all)\s+|reveal\s+(?:your\s+)?(?:system\s+)?prompt|system\s+prompt",
    re.I,
)


def looks_like_injection(text: str) -> bool:
    """Input-side screen for instruction-override attempts. Flags only: grounding + output rules still decide."""
    return bool(_INJECTION.search(text or ""))


_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-"})


def _normalise_dates(text: str) -> str:
    """Marathi/Hindi digits and typographic hyphens become ASCII, so (२०२६-१०-३०) equals 2026-10-30."""
    return _ascii_digits(text).translate(_DASHES)


def grounded_dates() -> Validator:
    """Every ISO date (YYYY-MM-DD) in the answer must appear in the CONTEXT. grounded_numbers alone would accept a
    made-up date whose parts (2026, 11, 03) happen to occur elsewhere in the data. Handles typographic hyphens."""

    def check(text: str, context: str = "") -> list[str]:
        known = set(_ISO_DATE.findall(_normalise_dates(context)))
        bad = sorted({d for d in _ISO_DATE.findall(_normalise_dates(text)) if d not in known})
        return [f"These dates are not in the provided data: {', '.join(bad)}. Use only dates from the CONTEXT."] if bad else []

    return _wants_context(check)


_MD_LINE_START = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*\u2022][ \t]+)", re.MULTILINE)
_MD_INLINE = re.compile(r"\*\*|__|`")
_LABEL = re.compile(r"\b[A-Z]{2,}(?:_[A-Z]{2,})+\b")  # internal rule labels such as ABOVE_STATE_MEDIAN


def _humanize(m: re.Match) -> str:
    return m.group(0).replace("_", " ") if m.group(0) == "SELL_NOW" else m.group(0).replace("_", " ").lower()


def plain_text(text: str, local: dict[str, str] | None = None) -> str:
    """Strip markdown symbols (the reply may be read aloud, and the prompt alone cannot guarantee it). Touches symbols
    only: hyphens inside dates and ranges, and every number, stay exactly as they were. Internal rule labels never reach the
    farmer: `local` maps a label to a phrase in the answer's own language; any label it lacks is written as plain English words."""
    local = local or {}

    def swap(m: re.Match) -> str:
        return local.get(m.group(0)) or _humanize(m)

    return _LABEL.sub(swap, _MD_INLINE.sub("", _MD_LINE_START.sub("", text or ""))).strip()


def run_all(validators: list[Validator], text: str, context: str = "") -> list[str]:
    out: list[str] = []
    for v in validators:
        out.extend(v(text, context) if getattr(v, "wants_context", False) else v(text))
    return out
