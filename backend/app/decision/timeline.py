"""Self-re-planning timeline: the next days' farm tasks, built in code from the forecast, and what moved since the last look and WHY.

Pure functions. A task is {code, date, params}. The reason for a move is only ever a fact present in both the old and the new
forecast (rain that was added on the old date), never a guess: when no such fact exists the move is reported as "forecast changed"."""
from __future__ import annotations

import datetime as dt

HORIZON_DAYS = 7
RAIN_REASON_MM = 2.5  # one IMD "rainy day" (same value weather_rules uses): at least this much newly forecast rain explains a moved task


def build_timeline(today: str, flags: list[dict], spray_windows: list[dict], irrigation: list[dict], harvest: dict | None = None) -> dict:
    """-> {"today", "tasks": [...sorted by date], "rain": {date: mm}} for the next HORIZON_DAYS days."""
    horizon = (dt.date.fromisoformat(today) + dt.timedelta(days=HORIZON_DAYS)).isoformat()
    tasks: list[dict] = []
    if spray_windows:
        w = spray_windows[0]
        tasks.append({"code": "SPRAY", "date": w["date"], "params": {"start": w["start"], "end": w["end"]}})
    for f in flags:
        if f.get("flag") == "HEAVY_RAIN":
            day = max(today, (dt.date.fromisoformat(f["day"]) - dt.timedelta(days=1)).isoformat())
            tasks.append({"code": "PROTECT", "date": day, "params": {"rain_day": f["day"], "mm": round(f["value"])}})
    for d in irrigation:
        if d.get("action") == "SKIP":
            tasks.append({"code": "SKIP_IRRIGATION", "date": d["date"], "params": {"mm": d.get("rain_mm")}})
    if harvest and harvest.get("status") in ("BEFORE_EARLIEST_HARVEST", "IN_HARVEST_WINDOW"):
        start = max(today, harvest["earliest"])
        if start <= horizon:
            tasks.append({"code": "HARVEST_WINDOW", "date": start, "params": {"until": harvest["latest"]}})
    tasks = [t for t in tasks if today <= t["date"] <= horizon]
    tasks.sort(key=lambda t: (t["date"], t["code"]))
    return {"today": today, "tasks": tasks, "rain": {d["date"]: d.get("rain_mm") for d in irrigation if d.get("rain_mm") is not None}}


def _rain_added(old: dict, new: dict, date: str) -> float | None:
    """mm of rain forecast on `date` now, when it is at least RAIN_REASON_MM more than before (both forecasts must have it)."""
    a, b = old.get("rain", {}).get(date), new.get("rain", {}).get(date)
    return round(b, 1) if a is not None and b is not None and b >= RAIN_REASON_MM and b - a >= RAIN_REASON_MM else None


def replan(old: dict | None, new: dict) -> list[dict]:
    """Changes between two timelines: [{code, task, params}] with code MOVED / ADDED / DROPPED. Only days still ahead are compared."""
    if not old:
        return []
    today = new["today"]
    out: list[dict] = []
    for code in ("SPRAY", "PROTECT", "HARVEST_WINDOW"):
        before = next((t for t in old["tasks"] if t["code"] == code and t["date"] >= today), None)
        after = next((t for t in new["tasks"] if t["code"] == code), None)
        if before and after and before["date"] != after["date"]:
            mm = _rain_added(old, new, before["date"])
            out.append({"code": "MOVED", "task": code, "params": {"from": before["date"], "to": after["date"], "reason": "RAIN_ADDED" if mm else "FORECAST", "mm": mm}})
        elif before and not after:
            mm = _rain_added(old, new, before["date"])
            out.append({"code": "DROPPED", "task": code, "params": {"from": before["date"], "reason": "RAIN_ADDED" if mm else "FORECAST", "mm": mm}})
        elif after and not before:
            out.append({"code": "ADDED", "task": code, "params": {"to": after["date"], "reason": "FORECAST", "mm": None}})
    old_skips = {t["date"] for t in old["tasks"] if t["code"] == "SKIP_IRRIGATION"}
    for t in new["tasks"]:
        if t["code"] == "SKIP_IRRIGATION" and t["date"] not in old_skips and t["date"] in old.get("rain", {}):
            out.append({"code": "ADDED", "task": "SKIP_IRRIGATION", "params": {"to": t["date"], "reason": "RAIN_ADDED", "mm": t["params"].get("mm")}})
    return out
