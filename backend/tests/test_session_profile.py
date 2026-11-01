"""3.4 session memory: follow-ups reuse place/crop; never for the shared default session; bad places are not remembered."""
from app.agents.orchestrator import Orchestrator
from tests.helpers import FakeLLM, StubStore
from tests.test_pipeline import domain, good


def orch(tools=None):
    seen = []

    def spy(text, profile):
        seen.append(dict(profile))
        return good(text, profile)

    return Orchestrator(domain(["spy"], tools={"spy": spy, **(tools or {})}), FakeLLM(["Max 33 C."]), StubStore()), seen


def test_follow_up_without_place_or_crop_uses_remembered_values():
    o, seen = orch()
    o.run("will it rain?", session_id="s1", profile={"place": "Nashik", "crop": "Onion"})
    r = o.run("and tomorrow?", session_id="s1", profile={})
    assert seen[1]["place"] == "Nashik" and seen[1]["crop"] == "Onion"
    assert any(t.agent == "Retriever" and t.action == "remembered profile" and "Nashik" in t.detail for t in r.trace)


def test_explicit_values_override_memory_and_get_remembered():
    o, seen = orch()
    o.run("x", session_id="s2", profile={"place": "Nashik", "crop": "Onion"})
    o.run("x", session_id="s2", profile={"crop": "Tomato"})
    assert seen[1] == {"place": "Nashik", "crop": "Tomato"}
    o.run("x", session_id="s2", profile={})
    assert seen[2]["crop"] == "Tomato"


def test_sessions_are_isolated_and_default_session_never_remembers():
    o, seen = orch()
    o.run("x", session_id="a", profile={"place": "Nashik", "crop": "Onion"})
    o.run("x", session_id="b", profile={})
    assert seen[1] == {}
    o.run("x", session_id="default", profile={"place": "Pune", "crop": "Wheat"})
    o.run("x", session_id="default", profile={})
    assert seen[3] == {} and "default" not in o.profiles


def test_place_that_was_not_resolved_is_not_remembered():
    from app.tools import ToolResult

    o, seen = orch({"spy": lambda t, p: ToolResult("WEATHER FAILED: place not found (asdfgh)", False)})
    o.run("x", session_id="s3", profile={"place": "asdfgh", "crop": "Onion"})
    assert o.profiles["s3"] == {"crop": "Onion"}


def test_memory_store_is_capped():
    o, _ = orch()
    for i in range(1005):
        o.run("x", session_id=f"s{i}", profile={"place": "Nashik", "crop": "Onion"})
    assert len(o.profiles) == 1000 and "s0" not in o.profiles and "s1004" in o.profiles
