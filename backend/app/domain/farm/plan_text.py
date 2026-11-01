"""The plan and the watch alerts as sentences in the farmer's language, for places that have no React UI (the Telegram bot).

The sentences are generated from the UI's own tables (`node frontend/scripts/dump-plan-words.mjs` writes plan_words.json), so the app and
the bot say the same thing. A code without a template falls back to the English title the rules produced, never to a blank line."""
from __future__ import annotations

import json
import re
from pathlib import Path

WORDS: dict = json.loads((Path(__file__).with_name("plan_words.json")).read_text(encoding="utf-8"))
LANGS = tuple(WORDS)
_MONTHS = {
    "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),
    "hi": "जन॰ फ़र॰ मार्च अप्रैल मई जून जुल॰ अग॰ सित॰ अक्टू॰ नव॰ दिस॰".split(),
    "mr": "जाने फेब्रु मार्च एप्रि मे जून जुलै ऑग सप्टें ऑक्टो नोव्हें डिसें".split(),
}
_FILL = re.compile(r"\{(\w+)\}")


def words(lang: str) -> dict:
    return WORDS.get(lang) or WORDS["en"]


def day(iso: str, lang: str) -> str:
    """'2026-11-03' -> '3 Nov' (in the language's month name)."""
    try:
        y, m, d = (int(x) for x in iso[:10].split("-"))
        return f"{d} {_MONTHS.get(lang, _MONTHS['en'])[m - 1]}"
    except (ValueError, IndexError):
        return iso


def _fill(template: str, values: dict) -> str:
    return _FILL.sub(lambda m: str(values.get(m.group(1), "")), template)


def _what(code: str, lang: str) -> str:
    return words(lang)["flags"].get(code) or WORDS["en"]["flags"].get(code) or code


def action_text(a: dict, lang: str) -> str:
    w = words(lang)
    tpl = w.get(a["code"])
    if not isinstance(tpl, str) or not isinstance(a.get("params"), dict):
        return a["title"]
    p = dict(a["params"])
    p["what"] = _what(a["code"], lang)
    if p.get("day"):
        p["day"] = day(p["day"], lang)
    if isinstance(p.get("days"), list):
        p["days"] = ", ".join(day(d, lang) for d in p["days"])
    hours = p.get("hours")
    if isinstance(hours, str) and re.match(r"\d{4}-\d{2}-\d{2} ", hours):
        p["hours"] = f"{day(hours[:10], lang)} {hours[11:]}"
    p["dir"] = w["dir"].get(p.get("dir", ""), p.get("dir", ""))
    p["crop"] = w.get("crops", {}).get(str(p.get("crop", "")).lower(), p.get("crop", ""))
    out = _fill(tpl, p)
    return out if p.get("hours") or "hours" not in tpl else re.sub(r"[.।] [^.।]*: $", "", out)


def change_text(c: dict, lang: str) -> str:
    w = words(lang)
    tpl = w["c"].get(c["code"])
    if not tpl:
        return c["code"]
    p = dict(c["params"])
    p["what"] = _what(p.get("flag", ""), lang)
    if p.get("day"):
        p["day"] = day(p["day"], lang)
    if "alert" in p:
        p["alert"] = w["alerts"].get(p["alert"], p["alert"])
    if c["code"] == "SIGNAL_CHANGED":
        p["old"], p["new"] = w["sig"].get(p["old"], p["old"]), w["sig"].get(p["new"], p["new"])
    return _fill(tpl, p)
