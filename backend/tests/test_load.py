"""20 farmers asking at once: no exception, no hang, no answer leaking between sessions."""
import concurrent.futures as cf

import httpx

from app.agents.orchestrator import Orchestrator
from app.domain.farm import DOMAIN
from app.tools import http
from tests.helpers import FakeLLM, StubStore
from tests.test_chaos import PROFILE, _fast_and_keyed, world  # noqa: F401  (fixture: fake mandi key, no retry delay)

CROPS = ["Onion", "Tomato", "Wheat", "Soybean"]  # the fake geocoder only knows Nashik, so sessions differ by crop


import pytest


@pytest.mark.usefixtures("_fast_and_keyed")
def test_twenty_simultaneous_questions_all_answer_and_stay_separate():
    http._client = httpx.Client(transport=httpx.MockTransport(world(set())))
    o = Orchestrator(DOMAIN, FakeLLM(off=True, route="sell_or_hold"), StubStore())

    def one(i):
        prof = {**PROFILE, "crop": CROPS[i % 4]}
        return i, o.run("Should I sell my onions now?", session_id=f"s{i}", profile=prof)

    with cf.ThreadPoolExecutor(20) as pool:
        results = [f.result(timeout=60) for f in [pool.submit(one, i) for i in range(20)]]
    assert len(results) == 20
    for i, r in results:
        assert r.answer.strip() and "prices" in {c["type"] for c in r.cards}
    assert all(o.profiles[f"s{i}"]["crop"] == CROPS[i % 4] for i in range(20))
