"""Today's farm plan: one tool that gathers weather, rain history, mandi prices and crop rules, then ranks the actions in code.

A source that is down only removes its own actions; the plan says which checks could not be done. It reuses the other tools'
cached calls, so asking for a plan does not hit the live APIs twice."""
from __future__ import annotations

import concurrent.futures as cf

from app.decision.farm_plan import build_plan
from app.decision import crop_fit as cropfit
from app.decision.timeline import build_timeline
from app.decision.watch import snapshot
import datetime as dt
from app.tools import NeedsInput, PlaceNotFound, SourceUnavailable, ToolResult, guarded
from app.tools.crop_fit import parse_sowing_date, table_key
from app.tools.crop_stage import get_crop_stage
from app.tools.mandi import crop_key, get_cross_check, get_mandi
from app.tools.rain_history import get_rain_history
from app.tools.weather import get_weather

CHECKS = {"weather": "the weather forecast", "rain": "the rain history", "market": "the mandi prices"}


def _try(fn):
    """(result, None) or (None, reason). NeedsInput means 'not asked for', every other failure means 'could not check'."""
    try:
        return fn(), None
    except NeedsInput as e:
        return None, f"needs {e.field}"
    except PlaceNotFound:
        return None, "place"
    except SourceUnavailable:
        return None, "unavailable"


def _card(where: str, actions: list[dict], skipped: list[str], skipped_codes: list[str]) -> dict:
    return {"type": "farm_plan", "title": f"Today's farm plan - {where}", "actions": actions, "skipped": skipped, "skipped_codes": skipped_codes}


def _harvest(profile: dict, today: dt.date) -> dict | None:
    """Harvest status from the farmer's sowing date and the EcoCrop cycle length; None without a readable date or a known crop."""
    sowing = parse_sowing_date((profile.get("sowing_date") or "").strip(), today)
    crop = cropfit.load_crops().get(table_key(profile.get("crop") or ""))
    return cropfit.harvest_status(crop, sowing, today) if sowing and crop else None


def get_farm_plan(profile: dict) -> ToolResult:
    with cf.ThreadPoolExecutor(3) as pool:
        jobs = {"weather": pool.submit(_try, lambda: get_weather(profile)), "rain": pool.submit(_try, lambda: get_rain_history(profile)),
                "market": pool.submit(_try, lambda: get_mandi(profile))}
        got = {k: j.result() for k, j in jobs.items()}
    weather, rain, market = (got[k][0] for k in ("weather", "rain", "market"))
    results = [r for r in (weather, rain, market) if r]
    if not results:
        reasons = {got[k][1] for k in got}
        if "place" in reasons:
            raise PlaceNotFound("the place could not be matched")
        if reasons == {"needs crop"} or all(v[1] and v[1].startswith("needs") for v in got.values()):
            raise NeedsInput("place", "the village or town of the farm")
        raise SourceUnavailable("none of the live sources for the plan could be reached")
    notes: list[str] = []
    if market and weather:
        notes = get_cross_check(profile).facts.get("notes", [])
    advice = get_crop_stage(profile).facts.get("crop_advice", []) if weather else []
    crop = (crop_key(profile.get("crop") or "") or "").title() or "your crop"
    actions = build_plan({**weather.facts["analysis"], "spray_windows": weather.facts["spray_windows"]} if weather else None, rain.facts["rain_history"] if rain else None,
                         market.facts["price"] if market else None, market.facts.get("position") if market else None, notes, advice, crop)
    skipped = [CHECKS[k] if got[k][1] == "unavailable" else f"{CHECKS[k]} (choose a crop first)" for k in got if got[k][0] is None]
    skipped_codes = [k if got[k][1] == "unavailable" else f"{k}_needs_crop" for k in got if got[k][0] is None]
    loc = next(r.facts["location"] for r in results if "location" in r.facts)
    where = f"{loc['name']}, {loc['state']}".strip(", ")
    lines = [f"TODAY'S FARM PLAN for {where} (ranked in code by urgency, not by profit; the first line matters most):"]
    lines += [f"{a['rank']}. [{a['category']}] {a['title']} (rule: {a['why']}; source: {a['source']})" for a in actions] or ["No action was triggered by the data that could be read."]
    if skipped:
        lines.append("NOT CHECKED (say so plainly, do not guess): " + "; ".join(skipped))
    sources, seen = [], set()
    for r in results:
        for s in r.sources:
            if (s["name"], s["url"]) not in seen:
                seen.add((s["name"], s["url"]))
                sources.append(s)
    cards = [_card(where, actions, skipped, skipped_codes)] + [c for r in results for c in r.cards]
    watched = snapshot(weather.facts["analysis"] if weather else None, rain.facts["rain_history"] if rain else None, market.facts["price"] if market else None)
    timeline = None
    if weather:
        today = dt.date.today()  # the timeline starts on the farm's own first forecast day
        timeline = build_timeline((weather.facts["irrigation_plan"] or [{"date": today.isoformat()}])[0]["date"], weather.facts["analysis"]["flags"],
                                  weather.facts["spray_windows"], weather.facts["irrigation_plan"], _harvest(profile, today))
    return ToolResult("\n".join(lines), True, sources, cards, {"location": loc, "plan": actions, "snapshot": watched, "timeline": timeline})


farm_plan = guarded("farm_plan")(lambda text, profile: get_farm_plan(profile))
