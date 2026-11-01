import pytest

from app.decision.market_rank import NEARBY_KM, haversine_km, rank_markets
from app.agents.orchestrator import Orchestrator
from app.domain.farm import DOMAIN
from tests.helpers import FakeLLM, StubStore
from tests.test_chaos import _fast_and_keyed, ask  # noqa: F401

NASHIK = (19.99, 73.78)


def test_haversine_known_distance():
    assert 160 < haversine_km(19.99, 73.78, 18.52, 73.86) < 170  # Nashik - Pune is about 165 km as the crow flies


def test_ranking_orders_by_price_keeps_nearest_and_never_invents_a_cost():
    rows = [{"market": "Near", "district": "A", "modal": 2000}, {"market": "Mid", "district": "B", "modal": 2400}, {"market": "Far", "district": "C", "modal": 3000}, {"market": "Lost", "district": "D", "modal": 9000}]
    places = {"Near": {"lat": 20.0, "lon": 73.8}, "Mid": {"lat": 20.3, "lon": 74.0}, "Far": {"lat": 23.0, "lon": 77.0}, "Lost": None}
    r = rank_markets(rows, places, NASHIK)
    assert [m["market"] for m in r["markets"]][:2] == ["Far", "Mid"] and r["unplaced"] == 1
    assert r["nearest"]["market"] == "Near" and r["nearest"]["vs_nearest"] == 0
    assert r["best_nearby"]["market"] == "Mid" and r["best_nearby"]["km"] <= NEARBY_KM
    assert all("cost" not in m and "profit" not in m for m in r["markets"])


def test_nothing_placed_gives_nothing_listed():
    r = rank_markets([{"market": "X", "district": "Y", "modal": 1}], {"X": None}, NASHIK)
    assert r["markets"] == [] and r["best_nearby"] is None


def test_keyword_router_finds_best_market():
    o = Orchestrator(DOMAIN, FakeLLM(off=True), StubStore())
    assert o._keyword_intent("which market pays the most for onion").name == "best_market"


def test_a_market_placed_only_by_its_district_is_never_the_nearest_or_best_nearby():
    rows = [{"market": "Town", "district": "A", "modal": 2000}, {"market": "Guess", "district": "B", "modal": 3000}]
    places = {"Town": {"lat": 20.2, "lon": 73.9}, "Guess": {"lat": 19.99, "lon": 73.78, "located_by": "district"}}
    r = rank_markets(rows, places, NASHIK)
    assert r["nearest"]["market"] == "Town" and r["best_nearby"]["market"] == "Town"


def test_lot_value_is_plain_multiplication_with_no_cost_added():
    from app.decision.market_rank import lot_value, parse_quantity
    rows = [{"market": "Near", "district": "A", "modal": 2000}, {"market": "Mid", "district": "B", "modal": 2615}]
    places = {"Near": {"lat": 20.0, "lon": 73.8}, "Mid": {"lat": 20.3, "lon": 74.0}}
    lot = lot_value(rank_markets(rows, places, NASHIK), 20)
    assert (lot["market"], lot["price"], lot["total"], lot["gap_per_quintal"], lot["gap_total"]) == ("Mid", 2615, 52300, 615, 12300)
    assert [parse_quantity(x) for x in ("20", "12,5", "0", "-3", "abc", "", "999999999")] == [20.0, 12.5, None, None, None, None, None]


def test_lot_value_reaches_the_answer_context_and_the_card():
    r = ask("which market pays most for onion", "best_market", set(), profile={"place": "Nashik, Maharashtra", "crop": "Onion", "quantity": "20"})
    card = next(c for c in r.cards if c["type"] == "best_market")
    assert card["lot"] and card["lot"]["quintals"] == 20 and card["lot"]["total"] == card["lot"]["price"] * 20
    assert any("LOT VALUE" in e["text"] for e in r.evidence) or "LOT VALUE" in str(r.trace)


def test_no_quantity_means_no_lot_line():
    r = ask("which market pays most for onion", "best_market", set())
    assert next(c for c in r.cards if c["type"] == "best_market")["lot"] is None
