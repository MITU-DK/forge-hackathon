"""Saved place/crop and recent turns survive a restart of the process (a new Orchestrator on the same file)."""
from app.agents.orchestrator import Orchestrator
from app.core.config import Settings
from app.domain.farm import DOMAIN
from tests.helpers import FakeLLM, StubStore


def make(path):
    o = Orchestrator(DOMAIN, FakeLLM(), StubStore())
    o.s = Settings(session_db_path=str(path))
    from app.core.sessions import SessionStore

    o.sessions = SessionStore(str(path))
    return o


def test_profile_and_history_come_back_after_a_restart(tmp_path):
    path = tmp_path / "s.sqlite"
    first = make(path)
    first.run("hello", "farmer-1", "en", None, {"place": "Nashik, Maharashtra", "crop": "Onion"})
    assert first.profiles.get("farmer-1", {}).get("crop") == "Onion"

    second = make(path)  # a new process: nothing in memory
    assert "farmer-1" not in second.profiles
    res = second.run("and tomorrow?", "farmer-1", "en", None, {})
    assert res.profile.get("crop") == "Onion"  # the saved crop was used for a follow-up that did not repeat it
    assert any(m["content"] == "hello" for m in second.history["farmer-1"])


def test_the_shared_anonymous_session_is_never_saved(tmp_path):
    path = tmp_path / "s.sqlite"
    make(path).run("hello", "default", "en", None, {"place": "Nashik, Maharashtra", "crop": "Onion"})
    from app.core.sessions import SessionStore

    assert SessionStore(str(path)).load("default") == ({}, [])


def test_unwritable_path_does_not_break_answers(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    o = make(blocker / "sub" / "s.sqlite")
    assert o.run("hello", "farmer-2", "en", None, {"crop": "Onion"}).answer
