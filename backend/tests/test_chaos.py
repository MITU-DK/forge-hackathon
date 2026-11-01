"""Failure injection: switch each live source off in turn and run whole questions through the pipeline.

The promise being tested (a failing source must never break the answer): the farmer always gets a truthful answer. A dead source is named as unreachable, its numbers
are not made up, the sources that still work still show, and nothing is raised, leaked (host names, URLs, exception text, keys) or hung.
"""
import datetime as dt

import httpx
import pytest

import app.tools.mandi as mandi_mod
from app.agents import planner as planner_mod
from app.agents.orchestrator import Orchestrator
from app.core.config import get_settings
from app.domain.farm import DOMAIN
from app.tools import http
from tests.helpers import FakeLLM, StubStore
from tests.test_tools import GEO, forecast, mandi_records

HOSTS = {
    "geocoder": "geocoding-api.open-meteo.com",
    "forecast": "api.open-meteo.com",
    "climate": "archive-api.open-meteo.com",
    "soil": "maps.isric.org",
    "mandi": "api.data.gov.in",
}
KEY = "chaos-test-key-123"
PROFILE = {"place": "Nashik, Maharashtra", "crop": "Onion"}


def _rain_history_payload():
    n = 37  # 30 past days + 7 forecast days
    return {"daily": {"time": [f"2026-10-{i % 28 + 1:02d}" for i in range(n)], "precipitation_sum": [1.0] * n}}


def _climate_payload():
    today = dt.date.today()
    start, end = dt.date(today.year - 5, 1, 1), dt.date(today.year - 1, 12, 31)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    return {"daily": {
        "time": [d.isoformat() for d in days],
        "temperature_2m_mean": [24.0] * len(days), "temperature_2m_max": [30.0] * len(days),
        "temperature_2m_min": [18.0] * len(days), "precipitation_sum": [2.0] * len(days),
    }}


def _mandi_payload(params):
    if "filters[state]" not in params:
        return {"records": [], "total": 0}
    today = dt.date.today()
    recs = []
    for offset, prices in {0: [2500] * 6, 7: [2000] * 6}.items():
        recs += mandi_records({(today - dt.timedelta(days=offset)).strftime("%d/%m/%Y"): prices})
    return {"records": recs, "total": len(recs)}


def world(down: set[str]):
    """A fake internet where the hosts in `down` refuse connections and the rest answer with real-shaped data (soil always 503)."""
    dead = {HOSTS[name] for name in down}

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host in dead:
            raise httpx.ConnectError("connection refused")
        params = dict(req.url.params)
        if host == HOSTS["geocoder"]:
            return httpx.Response(200, json=GEO)
        if host == HOSTS["forecast"]:
            return httpx.Response(200, json=_rain_history_payload() if "past_days" in params else forecast(rain=[0, 70.0, 0, 0, 0, 0, 0]))
        if host == HOSTS["climate"]:
            return httpx.Response(200, json=_climate_payload())
        if host == HOSTS["mandi"]:
            return httpx.Response(200, json=_mandi_payload(params))
        return httpx.Response(503, text="service unavailable")  # soil

    return handler


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(planner_mod, "RETRY_DELAY_S", 0)
    monkeypatch.setattr(mandi_mod, "HISTORY_RETRY_DELAY_S", 0)
    yield
    monkeypatch.setenv("DATA_GOV_API_KEY", "")
    get_settings.cache_clear()


def ask(text: str, route: str, down: set[str], profile=None):
    http._client = httpx.Client(transport=httpx.MockTransport(world(down)))
    return Orchestrator(DOMAIN, FakeLLM(off=True, route=route), StubStore()).run(text, profile=profile or PROFILE)


def card_types(result) -> set[str]:
    return {c["type"] for c in result.cards}


# question, router intent, sources it needs, card each source feeds
QUESTIONS = {
    "sell": ("Should I sell my onions now?", "sell_or_hold", {"mandi": "prices", "forecast": "forecast"}),
    "weather": ("Is it safe to spray this week?", "weather_advice", {"forecast": "forecast"}),
    "crops": ("Which crop should I sow now?", "crop_suggest", {"climate": "crop_fit", "forecast": "forecast"}),
}


@pytest.mark.parametrize("question", list(QUESTIONS))
def test_baseline_everything_up_shows_every_card(question):
    text, route, needs = QUESTIONS[question]
    r = ask(text, route, set())
    assert r.answer and card_types(r) >= set(needs.values())


@pytest.mark.parametrize("question", list(QUESTIONS))
@pytest.mark.parametrize("source", list(HOSTS))
def test_one_source_down_never_breaks_or_leaks(question, source):
    text, route, needs = QUESTIONS[question]
    r = ask(text, route, {source})
    assert r.answer.strip()
    low = r.answer.lower()
    for leak in (*HOSTS.values(), "http", "traceback", "connecterror", "connection refused", KEY.lower()):
        assert leak not in low, f"{leak!r} leaked into the answer"
    if source in needs:
        assert needs[source] not in card_types(r), "a card was shown for a source that is down"
        assert r.degraded
    survivors = {card for src, card in needs.items() if src != source}
    if source != "geocoder":  # without the geocoder no place resolves, so no source can answer
        assert survivors <= card_types(r), f"healthy sources lost their cards: {survivors - card_types(r)}"


@pytest.mark.parametrize("question", list(QUESTIONS))
def test_everything_down_still_gives_one_honest_message(question):
    text, route, _ = QUESTIONS[question]
    r = ask(text, route, set(HOSTS))
    assert r.answer.strip() and r.degraded and not r.cards
    assert "not reachable" in r.answer or "could not be matched" in r.answer


def test_dead_mandi_never_invents_a_price():
    r = ask("What is the onion price today?", "market_price", {"mandi"})
    assert "Rs" not in r.answer and "prices" not in card_types(r)


def test_a_source_that_recovers_is_used_again_on_the_next_question():
    text, route, needs = QUESTIONS["weather"]
    down = ask(text, route, {"forecast"})
    assert "forecast" not in card_types(down)
    http._fails.clear()  # the breaker window passing
    http._cache.clear()
    up = ask(text, route, set())
    assert "forecast" in card_types(up)  # (degraded stays true here only because the AI is switched off in this test)
