"""Timeline tasks and "what moved and why" as sentences in the farmer's language (templates in timeline_words.json; the Hindi and Marathi
wording is meant to be read through by a native speaker)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.domain.farm.plan_text import day

WORDS: dict = json.loads(Path(__file__).with_name("timeline_words.json").read_text(encoding="utf-8"))
_FILL = re.compile(r"\{(\w+)\}")


def _w(lang: str) -> dict:
    return WORDS.get(lang) or WORDS["en"]


def _fill(tpl: str, v: dict) -> str:
    return _FILL.sub(lambda m: str(v.get(m.group(1), "")), tpl)


def task_text(t: dict, lang: str) -> str:
    p = dict(t["params"])
    for k in ("rain_day", "until"):
        if p.get(k):
            p[k] = day(p[k], lang)
    if p.get("mm") is None:
        p["mm"] = ""
    return _fill(_w(lang)["tasks"].get(t["code"]) or WORDS["en"]["tasks"].get(t["code"], t["code"]), p)


def change_text(c: dict, lang: str) -> str:
    w, p = _w(lang), c["params"]
    why = _fill(w.get(p.get("reason"), ""), {"mm": p.get("mm") if p.get("mm") is not None else "", "rain_on": day(p.get("from") or p.get("to") or "", lang)}) if p.get("reason") else ""
    v = {"task": w["short"].get(c["task"], c["task"]), "from": day(p["from"], lang) if p.get("from") else "", "to": day(p["to"], lang) if p.get("to") else "", "why": why}
    return _fill(w[c["code"]], v)
