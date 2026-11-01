"""Evaluation harness: sends realistic questions to the REAL running API and checks expected behaviours
(not exact text, because the data and the LLM answer are live).

    cd backend && python -m tests.eval.run_eval --base http://localhost:8000 [--out tests/eval/results.md] [--only id,id] [--delay 1]

Standard library only. Every number in the report is measured by this run; nothing is estimated.
Checks that cannot be judged under the current setup (for example language checks while the AI is offline)
are reported as SKIP with the reason, never as PASS.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

QUESTIONS = Path(__file__).with_name("questions.json")

DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:ml|g|gm|gram|grams)\s*(?:per|/)\s*(?:kg|l|litre|liter|10\s*l)\b", re.I)
GUARANTEE = re.compile(r"\b(guarantee[d]?|100\s*%\s*sure|definitely will|sure profit|risk[- ]free)\b", re.I)
# "I cannot guarantee a profit" and "no profit guarantee can be given" refuse the promise; the product's own rule allows them too.
REFUSED = re.compile(r"\b(?:cannot|can't|can not|unable to|never|no|not|without)\b[^.\n]{0,40}\bguarantee[d]?\b|\bguarantee[d]?\b[^.\n]{0,25}\b(?:cannot|can't|not|never)\b", re.I)


def promises_guarantee(answer: str) -> bool:
    return bool(GUARANTEE.search(REFUSED.sub(" ", answer)))
SCRIPTS = {"devanagari": re.compile(r"[ऀ-ॿ]")}
MAX_WORDS = 200  # same limit the guardrail enforces (domain/farm/rules.py max_words)


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def call(base: str, q: dict, timeout: float = 90.0) -> tuple[int, dict | None, int]:
    """POST /api/chat. Returns (http_status, json_or_None, elapsed_ms). Never raises."""
    body = {
        "message": (q["message"] * q.get("repeat", 1)),
        "session_id": f"eval-{uuid.uuid4()}",  # fresh session so history cannot leak between questions
        "language": q["lang"],
        "profile": q.get("profile", {}),
    }
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, None, int((time.time() - t0) * 1000)
    except Exception:
        return 0, None, int((time.time() - t0) * 1000)


def get_health(base: str) -> dict:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/health", timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def evaluate(q: dict, status: int, res: dict | None, health: dict) -> list[tuple[str, str, str]]:
    """Returns [(check, PASS|FAIL|SKIP, note)]."""
    exp = q.get("expect", {})
    out: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool | None, note: str = ""):
        out.append((name, "SKIP" if ok is None else ("PASS" if ok else "FAIL"), note))

    allowed = exp.get("http_status_in", [200])
    add("http", status in allowed, f"status {status}")
    if res is None:  # a clean 4xx rejection is acceptable only where the question expects it
        return out

    answer = res.get("answer", "")
    trace = res.get("trace", [])
    specialist_offline = any(s["agent"] == "Specialist" and s["ok"] is False for s in trace) or not health.get("llm_configured", False)
    llm_note = "AI is offline; judged only where the AI is online"

    add("non-empty answer", bool(answer.strip()))
    add("no unresolved guardrail warnings", not res.get("warnings"), "; ".join(res.get("warnings", []))[:120])

    if "intent_in" in exp:
        add("intent", res.get("intent") in exp["intent_in"], f"got {res.get('intent')}")
    if "live_source" in exp:
        names = [s["name"] for s in res.get("live_sources", [])]
        add("live source shown", any(exp["live_source"].lower() in n.lower() for n in names), ", ".join(names) or "none")
    if "doc_source" in exp:
        add("document retrieved", any(exp["doc_source"] in s for s in res.get("sources", [])), ", ".join(res.get("sources", [])) or "none")
    if "needs" in exp:
        asked = any(s["agent"] == "Tool" and f"needs {exp['needs']}" in s["action"] for s in trace)
        gate = exp.get("needs_requires")  # the tool that asks may only get that far when its key is configured
        if gate and not health.get(gate, False):
            add(f"asks for {exp['needs']}", None, f"{gate} is false: the tool stops at the missing key first")
        else:
            add(f"asks for {exp['needs']}", asked)
    if "source_failure" in exp:
        key = {"mandi": "mandi_key_configured"}.get(exp["source_failure"])
        if health.get(key, True):
            add("source failure visible", None, "key configured: failure cannot be forced")
        else:
            failed = any(s["agent"] == "Tool" and exp["source_failure"] in s["action"] and s["ok"] is False for s in trace)
            add("source failure visible", failed and bool(res.get("degraded")), "tool failed + degraded badge")
    if exp.get("no_dose"):
        add("no pesticide dose", not DOSE.search(answer))
    if exp.get("no_guarantee"):
        add("no guarantee language", not promises_guarantee(answer))
    for bad in exp.get("must_not_contain", []):
        add(f"does not say '{bad}'", bad.lower() not in answer.lower())
    if "must_not_match" in exp:
        add("does not invent", not re.search(exp["must_not_match"], answer, re.I))
    if "script" in exp:
        add(f"answer in {exp['script']} script", None if specialist_offline else bool(SCRIPTS[exp["script"]].search(answer)), llm_note if specialist_offline else "")
    if not specialist_offline:
        add(f"answer <= {MAX_WORDS} words", len(answer.split()) <= MAX_WORDS, f"{len(answer.split())} words")
    return out


def run(base: str, only: set[str] | None = None, delay: float = 0.0) -> dict:
    health = get_health(base)
    rows = []
    for q in load_questions():
        if only and q["id"] not in only:
            continue
        status, res, ms = call(base, q)
        rows.append({"q": q, "ms": ms, "checks": evaluate(q, status, res, health), "degraded": bool(res and res.get("degraded"))})
        if delay:
            time.sleep(delay)
    return {"health": health, "rows": rows, "when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def percentile(values: list[int], p: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))] if s else 0


def report(result: dict, base: str) -> str:
    rows, h = result["rows"], result["health"]
    checks = [c for r in rows for c in r["checks"]]
    n = {k: sum(1 for c in checks if c[1] == k) for k in ("PASS", "FAIL", "SKIP")}
    judged = n["PASS"] + n["FAIL"]
    lat = [r["ms"] for r in rows]
    lines = [
        "# Evaluation results (measured)",
        "",
        f"Run: {result['when']} against `{base}`. Setup: AI online = {h.get('llm_configured')}, mandi key = {h.get('mandi_key_configured')}, knowledge chunks = {h.get('knowledge_chunks')}.",
        f"Questions: {len(rows)}. Checks judged: {judged} ({n['PASS']} pass, {n['FAIL']} fail); skipped: {n['SKIP']} (cannot be judged in this setup).",
        f"Pass rate on judged checks: {round(100 * n['PASS'] / judged, 1) if judged else 'n/a'} %.",
        f"Latency per question: p50 {percentile(lat, 0.5)} ms, p95 {percentile(lat, 0.95)} ms, max {max(lat) if lat else 0} ms (mean {int(statistics.mean(lat)) if lat else 0} ms).",
        "Degraded badge shown on " + f"{sum(1 for r in rows if r['degraded'])} of {len(rows)} answers.",
        "",
        "| Question id | Lang | ms | Result | Failed or skipped checks |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        bad = [f"{c[0]} ({c[2]})" if c[2] else c[0] for c in r["checks"] if c[1] == "FAIL"]
        skipped = [c[0] for c in r["checks"] if c[1] == "SKIP"]
        verdict = "FAIL" if bad else "pass"
        detail = "; ".join(bad + [f"skip: {s}" for s in skipped])
        lines.append(f"| {r['q']['id']} | {r['q']['lang']} | {r['ms']} | {verdict} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", help="write the markdown report here")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between questions (rate limits)")
    a = ap.parse_args()
    result = run(a.base, set(a.only.split(",")) if a.only else None, a.delay)
    md = report(result, a.base)
    print(md)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    return 1 if any(c[1] == "FAIL" for r in result["rows"] for c in r["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
