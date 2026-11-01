"""Verify every live source works from THIS machine, with YOUR keys.  Run before building and before every demo:

    make check-live                       (or: cd backend && python -m scripts.check_live "Nashik, Maharashtra" Onion)

Prints PASS/FAIL per source and, for data.gov.in, the real field names so you can confirm the schema.
"""
import re
import sys

import httpx

from app.core.config import get_settings
from app.tools import NeedsInput
from app.tools.http import get_json
from app.tools.mandi import API, mandi
from app.tools.rain_history import rain_history
from app.tools.weather import weather


QUOTA_MODELS = (("main", "llm_model"), ("reserve/router", "llm_fast_model"))
LIMIT_USED = re.compile(r"\((\w+)\): Limit (\d+), Used (\d+)")


def _probe_body(model: str) -> dict:
    """The smallest request each model accepts (reasoning models need room for their reasoning tokens)."""
    body = {"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
    if model.startswith("openai/gpt-oss"):
        body.update(max_tokens=16, reasoning_effort="low")
    return body


def _probe_model(s, label: str, model: str) -> bool:
    """Prints PASS or FAIL for one model; True when it cannot answer."""
    try:
        r = httpx.post(f"{s.llm_base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {s.llm_api_key}"}, json=_probe_body(model), timeout=20)
    except Exception as e:
        print(f"FAIL LLM {label} ({model}): {type(e).__name__}")
        return True
    if r.status_code == 429:
        m = LIMIT_USED.search(r.text)
        detail = f" - {m.group(1)} limit {m.group(2)}, used {m.group(3)}" if m else " - rate limited"
        print(f"FAIL LLM {label} ({model}): quota spent{detail}")
        return True
    if r.is_success:
        print(f"PASS LLM {label} ({model}): answers")
        return False
    print(f"FAIL LLM {label} ({model}): HTTP {r.status_code}")
    return True


def llm_quota(s) -> int:
    """Send a 1-token request to each Groq model: the free daily token quota is the likeliest way a demo goes slow.

    Returns the number of models that cannot answer. The 429 text is reduced to its Limit/Used figures (it also names the organisation).
    """
    if not s.llm_api_key:
        print("LLM_API_KEY not set: answers will be 'live data only'.")
        return 0
    return sum(_probe_model(s, label, getattr(s, attr)) for label, attr in QUOTA_MODELS)


def _check_tool(name: str, tool, place: str, crop: str) -> int:
    """Runs one live tool and prints PASS/FAIL with its first lines; returns 1 when it failed."""
    try:
        r = tool("", {"place": place, "crop": crop})
    except NeedsInput as e:
        print(f"FAIL {name}: needs {e.field}")
        return 1
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {e}")
        return 1
    print(("PASS " if r.ok else "FAIL ") + name)
    print("     " + "\n     ".join(r.text.splitlines()[:6]))
    return 0 if r.ok else 1


def _probe_mandi_schema(s) -> None:
    """Shows the real field names data.gov.in returns, so the schema can be confirmed by eye."""
    if not s.data_gov_api_key:
        print("DATA_GOV_API_KEY not set: mandi prices will show 'source unavailable' (get a free key at data.gov.in).")
        return
    try:
        f = get_json(API.format(rid=s.mandi_resource_id), {"api-key": s.data_gov_api_key, "format": "json", "limit": 2}, ttl=0)
        d = f.data if isinstance(f.data, dict) else {}
        recs = d.get("records") or []
        print("data.gov.in raw probe: top-level keys =", sorted(d.keys()))
        if recs:
            print("  record fields =", list(recs[0].keys()))
            print("  example       =", recs[0])
            print("  -> if field names are Capitalised, set MANDI_FILTER_STYLE=title in .env (auto tries both).")
        else:
            print("  no records returned: check MANDI_RESOURCE_ID (find the right resource on data.gov.in -> API tab).")
    except Exception as e:
        print(f"data.gov.in raw probe FAILED: {type(e).__name__}: {e}")


LIVE_TOOLS = (("weather + soil (Open-Meteo)", weather), ("rain history (Open-Meteo)", rain_history), ("mandi prices (data.gov.in)", mandi))


def main() -> int:
    place = sys.argv[1] if len(sys.argv) > 1 else "Nashik, Maharashtra"
    crop = sys.argv[2] if len(sys.argv) > 2 else "Onion"
    s = get_settings()
    print(f"Place={place!r} Crop={crop!r}\n")

    bad = 0
    for name, tool in LIVE_TOOLS:
        bad += _check_tool(name, tool, place, crop)
        print()

    _probe_mandi_schema(s)
    print()
    failing = llm_quota(s)
    if failing == len(QUOTA_MODELS):  # both models out: every answer is slow or live-data-only
        bad += 1
    elif failing:
        print("     (the other model still answers, so the demo works; the main model is the better writer)")

    print("\nRESULT:", "ALL LIVE SOURCES OK" if bad == 0 else f"{bad} source(s) failing - fix before the demo")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
