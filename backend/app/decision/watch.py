"""Watch mode: what changed on the farm since the last look. Pure functions over two snapshots of numbers and rule codes.

The thresholds are HEURISTICS (no official source) and are named here so they can be defended and tuned. A change is reported only
when BOTH snapshots had the value: a source that was down yesterday never produces a fake 'change'."""
from __future__ import annotations

RAIN_48H_CHANGE_MM = 10.0  # same size as the irrigation-skip rule (weather_rules.IRRIGATION_SKIP_RAIN_MM)
PRICE_CHANGE_PCT = 5.0  # a move this big in the latest modal price is worth telling; smaller is market noise


def snapshot(weather: dict | None, rain: dict | None, price: dict | None) -> dict:
    """Compact, comparable view of a plan's inputs. None = that source was not read this time."""
    w, r, p = weather, rain, price
    return {
        "rain_48h_mm": w["rain_next_48h_mm"] if w else None,
        "flags": sorted({f"{f['flag']}@{f['day']}" for f in w["flags"]}) if w else None,
        "spray_days": len(w["spray_ok_days"]) if w else None,
        "alerts": sorted(r["alerts"]) if r else None,
        "price": p.get("latest") if p else None,
        "signal": p.get("signal") if p else None,
    }


def changes(old: dict | None, new: dict) -> list[dict]:
    """Ranked list of {code, severity, params}. Severity 1 = act now, 2 = good to know."""
    if not old:
        return []
    out: list[dict] = []
    if old.get("flags") is not None and new.get("flags") is not None:
        for f in sorted(set(new["flags"]) - set(old["flags"])):
            name, day = f.split("@")
            out.append({"code": "NEW_RISK", "severity": 1, "params": {"flag": name, "day": day}})
        for f in sorted(set(old["flags"]) - set(new["flags"])):
            name, day = f.split("@")
            out.append({"code": "RISK_GONE", "severity": 2, "params": {"flag": name, "day": day}})
    if old.get("rain_48h_mm") is not None and new.get("rain_48h_mm") is not None:
        if abs(new["rain_48h_mm"] - old["rain_48h_mm"]) >= RAIN_48H_CHANGE_MM:
            out.append({"code": "RAIN_CHANGED", "severity": 1 if new["rain_48h_mm"] > old["rain_48h_mm"] else 2,
                        "params": {"old": old["rain_48h_mm"], "new": new["rain_48h_mm"]}})
    if old.get("alerts") is not None and new.get("alerts") is not None:
        for a in sorted(set(new["alerts"]) - set(old["alerts"])):
            out.append({"code": "NEW_ALERT", "severity": 1, "params": {"alert": a}})
    if old.get("price") and new.get("price"):
        pct = (new["price"] - old["price"]) / old["price"] * 100
        if abs(pct) >= PRICE_CHANGE_PCT:
            out.append({"code": "PRICE_CHANGED", "severity": 2, "params": {"old": old["price"], "new": new["price"], "pct": round(pct, 1)}})
    if old.get("signal") and new.get("signal") and old["signal"] != new["signal"]:
        out.append({"code": "SIGNAL_CHANGED", "severity": 2, "params": {"old": old["signal"], "new": new["signal"]}})
    if old.get("spray_days") is not None and new.get("spray_days") is not None:
        if old["spray_days"] > 0 and new["spray_days"] == 0:
            out.append({"code": "SPRAY_WINDOW_LOST", "severity": 2, "params": {}})
        elif old["spray_days"] == 0 and new["spray_days"] > 0:
            out.append({"code": "SPRAY_WINDOW_OPENED", "severity": 2, "params": {"days": new["spray_days"]}})
    return sorted(out, key=lambda c: c["severity"])
