"""Crop-specific rules. ONLY values read in a primary source are here; every rule carries its citation.

Onion has no rule on purpose: the ICAR-DOGR season table could not be read (site unreachable) and the
secondary sources disagreed (bulbing 20-25 C vs 21-30 C). No source, no rule.
"""
from __future__ import annotations

import datetime as dt

NHB_TOMATO = "NHB tomato climate bulletin (https://nhb.gov.in/bulletin_files/vegetable/tomato/tom012.pdf)"
ICAR_WHEAT = "Indian Farming 73(5), ICAR, May 2023, 'Terminal heat stress in wheat' (https://epubs.icar.org.in/index.php/IndFarm/article/download/133793/50127/361734)"

TOMATO_HEAT_C = 32.0  # NHB: "Temperatures above 32 C adversely affects the fruit set and development"
TOMATO_COLD_C = 10.0  # NHB: "Temperature below 10 C adversely affects plant tissues"
WHEAT_STATES = {"Punjab"}  # source is Punjab KVK / IGP guidance: do not apply it to Maharashtra etc.
WHEAT_SOW_START = (10, 25)  # 25 October
WHEAT_SOW_END = (11, 15)  # 15 November
WHEAT_GRAIN_FILL_MEAN_C = 31.0  # "terminal heat stress occurs when mean temperature during grain filling goes above 31 C"
WHEAT_GRAIN_FILL_MONTHS = (2, 3, 4)  # Feb-Apr in the cited Punjab context


def _tomato(days: list[dict]) -> list[dict]:
    out = []
    hot = [d for d in days if d.get("tmax") is not None and d["tmax"] > TOMATO_HEAT_C]
    cold = [d for d in days if d.get("tmin") is not None and d["tmin"] < TOMATO_COLD_C]
    if hot:
        d = max(hot, key=lambda x: x["tmax"])
        text = f"Forecast max {d['tmax']} C on {d['date']} (above {TOMATO_HEAT_C} C on {len(hot)} day(s)); temperatures above {TOMATO_HEAT_C} C adversely affect tomato fruit set and development."
        out.append({"code": "TOMATO_HEAT_FRUIT_SET", "text": text, "source": NHB_TOMATO, "params": {"tmax": d["tmax"], "day": d["date"], "n": len(hot), "limit": TOMATO_HEAT_C}})
    if cold:
        d = min(cold, key=lambda x: x["tmin"])
        text = f"Forecast min {d['tmin']} C on {d['date']} (below {TOMATO_COLD_C} C on {len(cold)} day(s)); temperatures below {TOMATO_COLD_C} C adversely affect tomato plant tissues."
        out.append({"code": "TOMATO_COLD", "text": text, "source": NHB_TOMATO, "params": {"tmin": d["tmin"], "day": d["date"], "n": len(cold), "limit": TOMATO_COLD_C}})
    return out


def _wheat(days: list[dict], today: dt.date) -> list[dict]:
    out = []
    md = (today.month, today.day)
    if WHEAT_SOW_START <= md <= WHEAT_SOW_END:
        out.append({"code": "WHEAT_SOWING_WINDOW_OPEN", "text": "Today is inside the recommended wheat sowing window (25 October to 15 November); early sowing avoids terminal heat stress.", "source": ICAR_WHEAT, "params": {}})
    elif WHEAT_SOW_END < md <= (12, 31):
        out.append({"code": "WHEAT_SOWING_LATE", "text": "The recommended wheat sowing window (25 October to 15 November) has passed; late-sown wheat faces more temperature stress.", "source": ICAR_WHEAT, "params": {}})
    if today.month in WHEAT_GRAIN_FILL_MONTHS:
        means = [(d["date"], (d["tmax"] + d["tmin"]) / 2) for d in days if d.get("tmax") is not None and d.get("tmin") is not None]
        hot = [(dte, m) for dte, m in means if m > WHEAT_GRAIN_FILL_MEAN_C]
        if hot:
            dte, m = max(hot, key=lambda x: x[1])
            out.append({"code": "WHEAT_TERMINAL_HEAT", "text": f"Forecast mean temperature {round(m, 1)} C on {dte} is above {WHEAT_GRAIN_FILL_MEAN_C} C, the level at which terminal heat stress occurs during grain filling.", "source": ICAR_WHEAT, "params": {"mean": round(m, 1), "day": dte, "limit": WHEAT_GRAIN_FILL_MEAN_C}})
    return out


def crop_advice(crop: str, state: str, days: list[dict], today: dt.date | None = None) -> list[dict]:
    """[{code, text, source}] for rules that fire now. Empty list = no verified rule fires (not 'all is well')."""
    c = (crop or "").strip().title()
    if c == "Tomato":
        return _tomato(days)
    if c == "Wheat" and (state or "").strip().title() in WHEAT_STATES:
        return _wheat(days, today or dt.date.today())
    return []


def has_rules(crop: str, state: str) -> bool:
    c = (crop or "").strip().title()
    return c == "Tomato" or (c == "Wheat" and (state or "").strip().title() in WHEAT_STATES)
