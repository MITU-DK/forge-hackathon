"""Backtest the price-trend signal on a year of real, dated Agmarknet prices.

    python -m scripts.backtest_signal [--cache DIR] [--days 365] [State/Commodity ...]
    (default series: Maharashtra/Onion Maharashtra/Tomato Maharashtra/Potato Karnataka/Onion Karnataka/Tomato Punjab/Potato)

Method (fixed before looking at results, so it cannot be tuned to look good):
  * For every day t the PRODUCTION function `trend_signal` sees only rows dated t-9 .. t and returns HOLD / SELL_NOW / NEUTRAL.
  * The outcome is the move of the SAME markets from t to about t+7 (median of per-market changes, >= 5 markets in common).
    A signal is "right" if the price then moved more than 1 % the called way (HOLD: up, SELL_NOW: down), "wrong" if more than
    1 % the other way, otherwise flat. The base rates (how often prices rose or fell at all) are printed next to it.
  * The newest 40 % of days are the TEST half. The older 60 % was used to choose the rule; the 7 days between are skipped.
  * Daily windows overlap, so the confidence interval resamples whole 7-day blocks, not single days.
  * The retired rule (today vs the 7-day daily average, +/-5 %, "above average = sell") is scored the same way for comparison.
It is slow on purpose when it downloads (data.gov.in rate limits bursts); use --cache to reuse a download.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import statistics as st
import sys
import time
from pathlib import Path

from app.core.config import get_settings
from app.decision import price_rules as pr
from app.tools.http import get_json

API = "https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24"
PAGE, PAUSE_S = 500, 3.0
DEFAULT = ["Maharashtra/Onion", "Maharashtra/Tomato", "Maharashtra/Potato", "Karnataka/Onion", "Karnataka/Tomato", "Punjab/Potato"]
HORIZON, TOL = 7, 1.0


def download(state: str, commodity: str, days: int) -> list[dict]:
    oldest, out = dt.date.today() - dt.timedelta(days=days), []
    for page in range(120):
        params = {"api-key": get_settings().data_gov_api_key, "format": "json", "limit": PAGE, "offset": page * PAGE,
                  "filters[State]": state, "filters[Commodity]": commodity, "sort[Arrival_Date]": "desc"}
        recs = (get_json(API, params, ttl=0, timeout=30).data or {}).get("records") or []
        rows = [{str(k).lower(): v for k, v in r.items()} for r in recs]
        out += [{"d": (pr.parse_date(r["arrival_date"]) or dt.date.min).isoformat(), "m": r.get("market"), "p": pr.to_float(r.get("modal_price"))}
                for r in rows if " ".join(str(r.get("commodity", "")).lower().split()) == commodity.lower()]
        dates = [d for d in (pr.parse_date(r.get("arrival_date", "")) for r in rows) if d]
        if len(recs) < PAGE or (dates and min(dates) < oldest):
            break
        time.sleep(PAUSE_S)
    return [r for r in out if r["d"] >= oldest.isoformat() and r["p"]]


def load(state: str, commodity: str, days: int, cache: Path | None) -> list[dict]:
    f = cache / f"{state}_{commodity}.json" if cache else None
    if f and f.exists():
        raw = json.loads(f.read_text())
    else:
        raw = download(state, commodity, days)
        if f:
            cache.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(raw))
    return [{"arrival_date": r["d"], "market": r["m"], "modal_price": r["p"]} for r in raw]


def old_rule(window: list[dict]) -> str:
    """The RETIRED rule, for comparison only: latest daily average vs the average of the earlier days, +/-5 %, above = sell."""
    ser = pr.build_series(window)
    if len(ser) < 3:
        return "INSUFFICIENT_DATA"
    base = sum(s["modal"] for s in ser[:-1]) / (len(ser) - 1)
    pct = (ser[-1]["modal"] - base) / base * 100
    return "SELL_NOW" if pct >= 5 else "HOLD" if pct <= -5 else "NEUTRAL"


def observations(records: list[dict]) -> list[dict]:
    days = pr.market_day_prices(records)
    order = sorted(days)
    by_day: dict[dt.date, list[dict]] = {}
    for r in records:
        if (d := pr.parse_date(r["arrival_date"])):
            by_day.setdefault(d, []).append(r)
    obs = []
    for t in order:
        later = [d for d in order if 6 <= (d - t).days <= 9]
        if not later:
            continue
        b = min(later, key=lambda d: abs((d - t).days - HORIZON))
        shared = [m for m in days[t] if m in days[b]]
        if len(shared) < pr.MIN_SHARED_MARKETS:
            continue
        window = [r for d in order if 0 <= (t - d).days <= 9 for r in by_day.get(d, [])]
        move = st.median((days[b][m] / days[t][m] - 1) * 100 for m in shared)
        obs.append({"t": t, "y": move, "new": pr.trend_signal(window)["signal"], "old": old_rule([r for r in window if (t - pr.parse_date(r["arrival_date"])).days < 7])})
    return obs


def split(obs: list[dict]):
    ts = sorted(o["t"] for o in obs)
    cut = ts[int(len(ts) * 0.6)]
    return [o for o in obs if o["t"] <= cut], [o for o in obs if o["t"] > cut + dt.timedelta(days=HORIZON)]


def calls(obs: list[dict], key: str) -> dict[str, list[float]]:
    c: dict[str, list[float]] = {"HOLD": [], "SELL_NOW": [], "NEUTRAL": []}
    for o in obs:
        if o[key] in c:
            c[o[key]].append(o["y"])
    return c


def _share(xs: list[float], ok) -> str:
    return f"{sum(map(ok, xs)) * 100 / len(xs):3.0f}%" if xs else " n/a"


def _mean(xs: list[float]) -> str:
    return f"{st.mean(xs):+6.2f}%" if xs else "   n/a"


def line(label: str, c: dict[str, list[float]]) -> str:
    h, s = c["HOLD"], c["SELL_NOW"]
    spread = f"{st.mean(h) - st.mean(s):+6.2f}pp" if h and s else "   n/a"
    return (f"{label:12} HOLD n={len(h):3} right {_share(h, lambda x: x > TOL)} wrong {_share(h, lambda x: x < -TOL)} mean {_mean(h)} | "
            f"SELL_NOW n={len(s):3} right {_share(s, lambda x: x < -TOL)} wrong {_share(s, lambda x: x > TOL)} mean {_mean(s)} | HOLD minus SELL_NOW {spread}")


def block_interval(parts: list[list[dict]], key: str, reps: int = 3000, seed: int = 11) -> tuple[float, float, int]:
    rng, blocks = random.Random(seed), []
    for obs in parts:
        by: dict[int, list[dict]] = {}
        for o in obs:
            by.setdefault((o["t"] - obs[0]["t"]).days // HORIZON, []).append(o)
        blocks += list(by.values())
    vals = []
    for _ in range(reps):
        c = calls([o for _ in blocks for o in rng.choice(blocks)], key)
        if c["HOLD"] and c["SELL_NOW"]:
            vals.append(st.mean(c["HOLD"]) - st.mean(c["SELL_NOW"]))
    vals.sort()
    return vals[int(len(vals) * 0.05)], vals[int(len(vals) * 0.95)], len(blocks)


def main(argv: list[str]) -> None:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return
    cache, days, names = None, 365, []
    it = iter(argv)
    for a in it:
        if a == "--cache":
            cache = Path(next(it))
        elif a == "--days":
            days = int(next(it))
        elif a.count("/") == 1 and not a.startswith("-"):
            names.append(a)
        else:
            sys.exit(f"unrecognised argument {a!r}: use [--cache DIR] [--days N] [State/Commodity ...] (see --help)")
    tests = {}
    for name in names or DEFAULT:
        state, commodity = name.split("/")
        obs = observations(load(state, commodity, days, cache))
        if len(obs) < 40:
            print(f"{name}: only {len(obs)} usable days, skipped")
            continue
        train, test = split(obs)
        tests[name] = test
        ys = [o["y"] for o in test]
        print(f"\n{name}: {len(obs)} days; TEST {len(test)} days, base rate: rose >1 % {sum(y > TOL for y in ys) * 100 / len(ys):.0f} %, fell >1 % {sum(y < -TOL for y in ys) * 100 / len(ys):.0f} %, mean move {st.mean(ys):+.2f} %")
        print("  " + line("new (shipped)", calls(test, "new")))
        print("  " + line("retired rule", calls(test, "old")))
    if tests:
        allt = [o for t in tests.values() for o in t]
        ys = [o["y"] for o in allt]
        print(f"\nPOOLED TEST, {len(tests)} series, {len(allt)} days; base rate rose >1 % {sum(y > TOL for y in ys) * 100 / len(ys):.0f} %, fell >1 % {sum(y < -TOL for y in ys) * 100 / len(ys):.0f} %, mean move {st.mean(ys):+.2f} %")
        for label, key in (("new (shipped)", "new"), ("retired rule", "old")):
            lo, hi, nb = block_interval(list(tests.values()), key)
            print("  " + line(label, calls(allt, key)) + f"\n  {'':12} 90 % interval of 'HOLD minus SELL_NOW' resampling {nb} weekly blocks: [{lo:+.2f}, {hi:+.2f}] pp (an interval containing 0 means: not proven)")


if __name__ == "__main__":
    main(sys.argv[1:])
