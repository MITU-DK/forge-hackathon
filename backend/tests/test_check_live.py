"""The pre-demo LLM quota probe: reports the Limit/Used figures from a 429 and never prints the organisation id."""
import httpx

from app.core.config import Settings
from scripts import check_live


def _post(responses):
    def post(url, **kw):
        return responses[kw["json"]["model"]]
    return post


def test_quota_probe_reports_spent_model_without_the_org_id(monkeypatch, capsys):
    spent = httpx.Response(429, text="Rate limit reached for model `big` in organization `org_SECRET` on tokens per day (TPD): Limit 200000, Used 199061, Requested 20.")
    ok = httpx.Response(200, json={"choices": []})
    monkeypatch.setattr(check_live.httpx, "post", _post({"big": spent, "small": ok}))
    failing = check_live.llm_quota(Settings(llm_api_key="k", llm_model="big", llm_fast_model="small"))
    out = capsys.readouterr().out
    assert failing == 1 and "limit 200000, used 199061" in out and "org_SECRET" not in out
    assert "PASS LLM reserve/router (small)" in out


def test_quota_probe_without_a_key_is_not_a_failure(capsys):
    assert check_live.llm_quota(Settings(llm_api_key="")) == 0
