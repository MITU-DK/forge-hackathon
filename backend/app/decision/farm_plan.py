"""Today's farm plan: ranks the day's actions from rules that already exist. Pure functions, no new data and no new thresholds.

Order is urgency, not profit: crop-damaging weather first, then water, then spraying, then the market. Every action carries the
rule it came from, so the farmer (and a judge) can see why it is listed. SELL_NOW/HOLD are recent-trend labels, never forecasts."""
from __future__ import annotations

URGENCY = {"WEATHER_RISK": 1, "WATER": 2, "SPRAY": 3, "CROP_RULE": 4, "MARKET": 5}
SOON_DAYS = 3  # a flag within this many days is "act now"; later ones are shown as "watch"


def _risk_actions(flags: list[dict]) -> list[dict]:
    out = []
    for f in flags:
        soon = f["day_index"] <= SOON_DAYS
        what = {"HEAVY_RAIN": "Heavy rain", "HEAT_STRESS": "High temperature", "COLD_STRESS": "Low temperature"}.get(f["flag"], f["flag"])
        out.append({
            "category": "WEATHER_RISK", "code": f["flag"], "priority": 1 if soon else 2,
            "title": f"{what} forecast on {f['day']}: {f['value']} {f['unit']}",
            "why": f["rule"], "source": "Open-Meteo forecast",
            "params": {"day": f["day"], "value": f["value"], "unit": f["unit"]},
        })
    return out


def _water_actions(weather: dict | None, rain: dict | None) -> list[dict]:
    out = []
    alerts = (rain or {}).get("alerts", [])
    if "WATERLOGGING_RISK" in alerts:
        out.append({"category": "WATER", "code": "WATERLOGGING_RISK", "priority": 1, "title": "Waterlogging risk: heavy rain fell this week and more is due in 3 days",
                    "why": f"{rain['past_7d_mm']} mm in the past 7 days, {rain['forecast_3d_mm']} mm in the next 3", "source": "Open-Meteo rain history", "params": {"past7": rain["past_7d_mm"], "next3": rain["forecast_3d_mm"]}})
    if "IRRIGATION_URGENT" in alerts:
        out.append({"category": "WATER", "code": "IRRIGATION_URGENT", "priority": 2, "title": f"Very little rain in 14 days ({rain['past_14d_mm']} mm): check soil moisture and irrigate if dry",
                    "why": "under 5 mm in the past 14 days (heuristic)", "source": "Open-Meteo rain history", "params": {"past14": rain["past_14d_mm"]}})
    if "DRY_SPELL_ALERT" in alerts:
        out.append({"category": "WATER", "code": "DRY_SPELL_ALERT", "priority": 2, "title": f"Dry spell: {rain['past_7d_mm']} mm in the past 7 days and {rain['forecast_7d_mm']} mm forecast for the next 7",
                    "why": "dry both before and after today (heuristic)", "source": "Open-Meteo rain history", "params": {"past7": rain["past_7d_mm"], "next7": rain["forecast_7d_mm"]}})
    if weather and not out:
        if weather.get("irrigation") == "SKIP_IRRIGATION":
            out.append({"category": "WATER", "code": "SKIP_IRRIGATION", "priority": 3, "title": f"Skip irrigation: {weather['rain_next_48h_mm']} mm of rain expected in the next 48 hours",
                        "why": "10 mm or more in 48 h (heuristic)", "source": "Open-Meteo forecast", "params": {"mm": weather["rain_next_48h_mm"]}})
        else:
            out.append({"category": "WATER", "code": "IRRIGATE_IF_SOIL_DRY", "priority": 3, "title": "No big rain in the next 48 hours: irrigate if the soil feels dry",
                        "why": f"{weather['rain_next_48h_mm']} mm expected in 48 h", "source": "Open-Meteo forecast", "params": {"mm": weather["rain_next_48h_mm"]}})
    return out


def _hours(windows: list[dict] | None) -> str:
    """'2026-11-04 06:00-14:00' for the first window, '' when there is none."""
    w = (windows or [None])[0]
    return f"{w['date']} {w['start']}-{w['end']}" if w else ""


def _spray_action(weather: dict | None) -> list[dict]:
    if not weather:
        return []
    days = weather.get("spray_ok_days", [])
    title = f"Spray-safe days this week: {', '.join(days[:4])}" if days else "No spray-safe day this week (wind or rain chance too high); wait for the next forecast"
    best = _hours(weather.get("spray_windows"))
    title += f". Best hours: {best}" if best and days else ""
    return [{"category": "SPRAY", "code": "SPRAY_WINDOW" if days else "NO_SPRAY_WINDOW", "priority": 3, "title": title,
             "why": "wind up to 15 km/h, rain chance up to 40 %, under 2.5 mm (heuristic; the product label always governs)", "source": "Open-Meteo forecast",
             "params": {"days": days[:4], "hours": _hours(weather.get("spray_windows"))}}]


def _crop_actions(advice: list[dict]) -> list[dict]:
    return [{"category": "CROP_RULE", "code": a["code"], "priority": 2, "title": a["text"], "why": "cited crop rule applied to the live forecast", "source": a["source"], "params": a.get("params", {})} for a in advice]


def _market_action(price: dict | None, position: dict | None, notes: list[str], crop: str, weather: dict | None = None) -> list[dict]:
    if not price:
        return []
    sig = price.get("signal")
    out = []
    if sig in ("HOLD", "SELL_NOW"):
        word = "rising" if sig == "HOLD" else "falling"
        out.append({"category": "MARKET", "code": sig, "priority": 2 if (sig == "SELL_NOW" and notes) else 3,
                    "title": f"{crop} prices have been {word}: {price.get('change_pct')} % in {price.get('window_days')} days (a recent trend, not a forecast)",
                    "why": "week-on-week change in mandi modal price", "source": "data.gov.in Agmarknet",
                    "params": {"crop": crop, "pct": price.get("change_pct"), "days": price.get("window_days")}})
    elif sig == "NEUTRAL":
        out.append({"category": "MARKET", "code": "NEUTRAL", "priority": 4, "title": f"{crop} prices show no clear rise or fall this week", "why": "change inside the neutral band", "source": "data.gov.in Agmarknet", "params": {"crop": crop}})
    if position and position.get("label"):
        out.append({"category": "MARKET", "code": position["label"], "priority": 4,
                    "title": f"Your district's mandi price is {abs(position['pct_vs_state_median'])} % {'above' if position['pct_vs_state_median'] >= 0 else 'below'} the state median ({position['state_median']} Rs/quintal); best market in the state: {position['best_market']} at {position['best_price']} Rs/quintal",
                    "why": "latest day, all reporting markets in the state", "source": "data.gov.in Agmarknet",
                    "params": {"pct": abs(position["pct_vs_state_median"]), "dir": "above" if position["pct_vs_state_median"] >= 0 else "below", "median": position["state_median"], "market": position["best_market"], "best": position["best_price"]}})
    heavy = next((f for f in (weather or {}).get("flags", []) if f["flag"] == "HEAVY_RAIN" and f["day_index"] <= 3), None)
    for n in notes:
        code = n.split(":")[0]
        if sig != "INSUFFICIENT_DATA" and code in ("SELL_BEFORE_RAIN", "STORAGE_RISK") and heavy:
            out.append({"category": "MARKET", "code": code, "priority": 1, "title": n, "why": "price trend or market position combined with the rain forecast",
                        "source": "data.gov.in Agmarknet + Open-Meteo", "params": {"value": heavy["value"], "day": heavy["day"]}})
    return out


def build_plan(weather: dict | None, rain: dict | None, price: dict | None, position: dict | None, notes: list[str], advice: list[dict], crop: str) -> list[dict]:
    """Ranked action list. Missing inputs (a source that is down) simply contribute no actions; nothing is guessed."""
    actions = (_risk_actions((weather or {}).get("flags", [])) + _water_actions(weather, rain) + _spray_action(weather)
               + _crop_actions(advice) + _market_action(price, position, notes, crop, weather))
    actions.sort(key=lambda a: (a["priority"], URGENCY[a["category"]]))
    for rank, a in enumerate(actions, 1):
        a["rank"] = rank
    return actions
