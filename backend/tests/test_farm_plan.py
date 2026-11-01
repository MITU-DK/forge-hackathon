import httpx
import pytest

from app.agents.orchestrator import Orchestrator
from app.decision.farm_plan import build_plan
from app.domain.farm import DOMAIN
from app.tools import http
from tests.helpers import FakeLLM, StubStore
from tests.test_chaos import HOSTS, PROFILE, _fast_and_keyed, ask, world  # noqa: F401

HEAVY = {"flags": [{"flag": "HEAVY_RAIN", "day": "2026-11-02", "day_index": 1, "value": 70.0, "unit": "mm", "rule": ">= 64.5 mm"}],
         "spray_ok_days": ["2026-11-05"], "rain_next_48h_mm": 70.0, "irrigation": "SKIP_IRRIGATION"}
PRICE = {"signal": "SELL_NOW", "change_pct": -20.0, "window_days": 7}


def test_ranking_puts_crop_damaging_weather_before_spray_and_market():
    plan = build_plan(HEAVY, None, PRICE, None, ["Heavy rain on day 1: ..."], [], "Onion")
    assert [a["rank"] for a in plan] == list(range(1, len(plan) + 1))
    cats = [a["category"] for a in plan]
    assert cats[0] in ("WEATHER_RISK", "MARKET") and plan[0]["priority"] == 1
    assert cats.index("WEATHER_RISK") < cats.index("SPRAY")
    assert all(a["why"] and a["source"] for a in plan)


def test_missing_sources_give_no_invented_actions():
    assert build_plan(None, None, None, None, [], [], "Onion") == []


def test_a_trend_label_is_never_worded_as_a_forecast():
    text = " ".join(a["title"] for a in build_plan(None, None, PRICE, None, [], [], "Onion")).lower()
    assert "will" not in text and "not a forecast" in text


@pytest.mark.usefixtures("_fast_and_keyed")
def test_plan_through_the_pipeline_has_card_and_ranked_context():
    r = ask("What should I do today?", "farm_plan", set())
    assert "farm_plan" in {c["type"] for c in r.cards} and {"forecast", "prices"} <= {c["type"] for c in r.cards}
    plan = next(c for c in r.cards if c["type"] == "farm_plan")
    assert plan["actions"] and plan["actions"][0]["rank"] == 1 and not plan["skipped"]
    assert "TODAY'S FARM PLAN" in r.answer


@pytest.mark.usefixtures("_fast_and_keyed")
@pytest.mark.parametrize("down", ["mandi", "forecast", "climate"])
def test_plan_survives_one_source_down_and_says_what_was_not_checked(down):
    r = ask("What should I do today?", "farm_plan", {down})
    assert r.answer.strip() and any(c["type"] == "farm_plan" for c in r.cards)
    assert "NOT CHECKED" in r.answer or down == "climate"


@pytest.mark.usefixtures("_fast_and_keyed")
def test_plan_without_a_crop_still_works_and_asks_for_it_only_for_the_market():
    r = ask("What should I do today?", "farm_plan", set(), profile={"place": "Nashik, Maharashtra"})
    plan = next(c for c in r.cards if c["type"] == "farm_plan")
    assert any("choose a crop" in s for s in plan["skipped"]) and plan["actions"]


def test_keyword_router_finds_the_plan_without_an_llm():
    o = Orchestrator(DOMAIN, FakeLLM(off=True), StubStore())
    assert o._keyword_intent("what should I do today on my farm").name == "farm_plan"
    assert o._keyword_intent("आज क्या करें").name == "farm_plan"


# ---- /api/plan and watch mode
from fastapi.testclient import TestClient  # noqa: E402

from app.api import routes  # noqa: E402
from app.core.snapshots import SnapshotStore  # noqa: E402
from app.decision.watch import changes, snapshot  # noqa: E402
from app.main import create_app  # noqa: E402


def _client(tmp_path, monkeypatch, down=()):
    http._client = httpx.Client(transport=httpx.MockTransport(world(set(down))))
    store = SnapshotStore(str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(routes, "get_snapshots", lambda: store)
    return TestClient(create_app())


@pytest.mark.usefixtures("_fast_and_keyed")
def test_plan_endpoint_returns_ranked_plan_without_any_llm(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/plan", json={"client_id": "dev1", "profile": PROFILE}).json()
    assert r["ok"] and r["plan"]["actions"] and r["changes"] == [] and r["since"] is None
    assert all("params" in a for a in r["plan"]["actions"])


@pytest.mark.usefixtures("_fast_and_keyed")
def test_plan_endpoint_asks_for_a_place_and_survives_total_outage(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/plan", json={"client_id": "d", "profile": {}}).json()["ok"] is False
    c = _client(tmp_path, monkeypatch, down=set(HOSTS))
    assert c.post("/api/plan", json={"client_id": "d", "profile": PROFILE}).json() == {"ok": False, "error": "unavailable"}


OLD = {"rain_48h_mm": 0.0, "flags": [], "spray_days": 3, "alerts": [], "price": 2000.0, "signal": "NEUTRAL"}


def test_changes_report_real_moves_only():
    new = {**OLD, "rain_48h_mm": 70.0, "flags": ["HEAVY_RAIN@2026-11-02"], "spray_days": 0, "price": 1700.0, "signal": "SELL_NOW"}
    codes = [c["code"] for c in changes(OLD, new)]
    assert codes[:2] == ["NEW_RISK", "RAIN_CHANGED"] or "NEW_RISK" in codes[:2]
    assert {"NEW_RISK", "RAIN_CHANGED", "PRICE_CHANGED", "SIGNAL_CHANGED", "SPRAY_WINDOW_LOST"} <= set(codes)
    assert changes(OLD, dict(OLD)) == []
    assert changes(None, new) == []


def test_a_source_that_was_down_never_makes_a_fake_change():
    blind = {**OLD, "price": None, "signal": None, "flags": None, "rain_48h_mm": None, "alerts": None, "spray_days": None}
    assert changes(OLD, blind) == [] and changes(blind, OLD) == []
    assert snapshot(None, None, None) == blind


def test_snapshots_survive_a_restart(tmp_path):
    path = str(tmp_path / "s.sqlite")
    SnapshotStore(path).save("k", OLD)
    assert SnapshotStore(path).load("k")[1] == OLD
    assert SnapshotStore("").load("nope") is None


def test_rain_and_price_note_becomes_a_coded_action_with_numbers_and_crop_rules_keep_their_params():
    notes = ["SELL_BEFORE_RAIN: heavy rain (70.0 mm) forecast on 2026-11-02; prices fell 20.0 % ..."]
    plan = build_plan(HEAVY, None, PRICE, None, notes, [{"code": "TOMATO_COLD", "text": "t", "source": "NHB", "params": {"tmin": 8.0, "day": "2026-11-03", "n": 2, "limit": 10.0}}], "Tomato")
    rain = next(a for a in plan if a["code"] == "SELL_BEFORE_RAIN")
    assert rain["params"] == {"value": 70.0, "day": "2026-11-02"}
    assert next(a for a in plan if a["code"] == "TOMATO_COLD")["params"]["tmin"] == 8.0


def test_timeline_replans_with_a_reason_and_in_each_language():
    from app.decision.timeline import build_timeline, replan
    from app.domain.farm.timeline_text import change_text, task_text
    old = build_timeline("2026-11-02", [], [{"date": "2026-11-03", "start": "08:00", "end": "11:00", "hours": 3}],
                         [{"date": d, "rain_mm": 0.0, "action": "CHECK_SOIL"} for d in ("2026-11-02", "2026-11-03", "2026-11-04")])
    new = build_timeline("2026-11-02", [], [{"date": "2026-11-04", "start": "08:00", "end": "11:00", "hours": 3}],
                         [{"date": "2026-11-02", "rain_mm": 0.0, "action": "CHECK_SOIL"}, {"date": "2026-11-03", "rain_mm": 14.0, "action": "SKIP"}, {"date": "2026-11-04", "rain_mm": 0.0, "action": "SKIP"}])
    moves = replan(old, new)
    moved = next(m for m in moves if m["code"] == "MOVED")
    assert moved["params"] == {"from": "2026-11-03", "to": "2026-11-04", "reason": "RAIN_ADDED", "mm": 14.0}
    assert "14.0 mm" in change_text(moved, "en") and "3 Nov" in change_text(moved, "en")
    assert any("ऀ" <= ch <= "ॿ" for ch in change_text(moved, "mr")) and task_text(new["tasks"][0], "hi")
    assert replan(None, new) == [] and replan(new, new) == []  # first look and an unchanged forecast say nothing


def test_timeline_never_blames_rain_it_cannot_see():
    from app.decision.timeline import build_timeline, replan
    mk = lambda day, rain: build_timeline("2026-11-02", [], [{"date": day, "start": "08:00", "end": "10:00", "hours": 2}], [{"date": d, "rain_mm": rain.get(d, 0.0), "action": "CHECK_SOIL"} for d in ("2026-11-02", "2026-11-03", "2026-11-04")])
    m = replan(mk("2026-11-03", {}), mk("2026-11-04", {"2026-11-03": 1.0}))[0]
    assert m["params"]["reason"] == "FORECAST"  # 1 mm is below one rainy day: no rain reason is claimed


def test_api_plan_returns_a_timeline_and_remembers_it(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    body = {"client_id": "dev-1", "lang": "hi", "profile": {"place": "Nashik, Maharashtra", "crop": "Onion", "sowing_date": "01/10/2026"}}
    first = c.post("/api/plan", json=body).json()
    assert first["ok"] and first["timeline"]["tasks"] and first["timeline"]["changes"] == []
    assert all(t["text"] for t in first["timeline"]["tasks"])
    assert c.post("/api/plan", json=body).json()["timeline"]["changes"] == []  # same forecast: nothing moved


def test_plan_for_an_unknown_place_says_place_not_found(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(200, json={}) if "geocoding" in req.url.host else httpx.Response(503)
    http._client = httpx.Client(transport=httpx.MockTransport(handler))
    store = SnapshotStore(str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(routes, "get_snapshots", lambda: store)
    r = TestClient(create_app()).post("/api/plan", json={"profile": {"place": "Nowhereville, Zz", "crop": "Onion"}}).json()
    assert r == {"ok": False, "error": "place_not_found"}
