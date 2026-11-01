"""Offline tests for the evaluation harness itself: the check logic and the question file. No network, no keys."""
from tests.eval.run_eval import evaluate, load_questions, percentile

ONLINE = {"llm_configured": True, "mandi_key_configured": True}
OFFLINE = {"llm_configured": False, "mandi_key_configured": False}


def res(answer="ok", **kw):
    base = {"answer": answer, "intent": "general", "warnings": [], "trace": [], "live_sources": [], "sources": [], "degraded": False}
    base.update(kw)
    return base


def by_name(checks, name):
    return next(c for c in checks if c[0] == name)


def test_questions_are_well_formed():
    qs = load_questions()
    assert 25 <= len(qs) <= 35
    assert len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert {"id", "lang", "message", "expect"} <= q.keys()


def test_dose_and_guarantee_are_caught():
    q = {"expect": {"no_dose": True, "no_guarantee": True}}
    bad = evaluate(q, 200, res("Spray 3 ml per litre. Sure profit."), ONLINE)
    assert by_name(bad, "no pesticide dose")[1] == "FAIL"
    assert by_name(bad, "no guarantee language")[1] == "FAIL"
    good = evaluate(q, 200, res("Follow the label."), ONLINE)
    assert by_name(good, "no pesticide dose")[1] == "PASS"


def test_injection_number_must_not_appear():
    q = {"expect": {"must_not_contain": ["5000"]}}
    assert by_name(evaluate(q, 200, res("The price is Rs 5000"), ONLINE), "does not say '5000'")[1] == "FAIL"
    assert by_name(evaluate(q, 200, res("I use live prices only."), ONLINE), "does not say '5000'")[1] == "PASS"


def test_language_check_is_skipped_not_passed_when_ai_is_offline():
    q = {"expect": {"script": "devanagari"}}
    assert by_name(evaluate(q, 200, res("English only"), OFFLINE), "answer in devanagari script")[1] == "SKIP"
    assert by_name(evaluate(q, 200, res("English only"), ONLINE), "answer in devanagari script")[1] == "FAIL"
    assert by_name(evaluate(q, 200, res("नमस्ते"), ONLINE), "answer in devanagari script")[1] == "PASS"


def test_source_failure_only_judged_when_key_is_missing():
    q = {"expect": {"source_failure": "mandi"}}
    failed = res(trace=[{"agent": "Tool", "action": "mandi", "ok": False}], degraded=True)
    assert by_name(evaluate(q, 200, failed, OFFLINE), "source failure visible")[1] == "PASS"
    assert by_name(evaluate(q, 200, res(), OFFLINE), "source failure visible")[1] == "FAIL"
    assert by_name(evaluate(q, 200, res(), ONLINE), "source failure visible")[1] == "SKIP"


def test_clean_rejection_accepted_only_when_expected():
    assert evaluate({"expect": {"http_status_in": [200, 422]}}, 422, None, ONLINE)[0][1] == "PASS"
    assert evaluate({"expect": {}}, 422, None, ONLINE)[0][1] == "FAIL"


def test_percentile():
    assert percentile([100, 200, 300, 400, 500], 0.5) == 300
    assert percentile([], 0.95) == 0
