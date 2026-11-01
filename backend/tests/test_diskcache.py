"""The persistent cache: real responses survive a restart and are served labelled stale; no secret is written."""
import httpx
import pytest

from app.core.config import get_settings
from app.tools import SourceUnavailable, http


class _Client:
    def __init__(self, body=None, fail=False):
        self.body, self.fail, self.calls = body, fail, 0

    def get(self, url, **kw):
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=self.body, request=httpx.Request("GET", url))


@pytest.fixture
def disk(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("CACHE_DB_PATH", str(path))
    get_settings.cache_clear()
    http._disk.clear()
    yield path
    monkeypatch.setenv("CACHE_DB_PATH", "")
    get_settings.cache_clear()
    http._disk.clear()


def _restart():
    """What a process restart does to the in-memory state."""
    http._cache.clear()
    http._fails.clear()
    http._disk.clear()  # a new process opens the file again


def test_last_real_response_is_served_stale_after_a_restart_when_the_source_is_down(disk):
    http._client = _Client({"records": [{"modal_price": 3500}]})
    first = http.get_json("https://api.data.gov.in/resource/x", {"api-key": "SECRET123", "state": "Maharashtra"})
    assert not first.stale

    _restart()
    http._client = _Client(fail=True)
    again = http.get_json("https://api.data.gov.in/resource/x", {"api-key": "SECRET123", "state": "Maharashtra"}, ttl=0)
    assert again.stale and again.data == {"records": [{"modal_price": 3500}]}
    assert again.fetched_at == first.fetched_at  # the true time of the real fetch, never "now"


def test_fresh_entry_on_disk_avoids_a_live_call_after_a_restart(disk):
    http._client = _Client({"a": 1})
    http.get_json("https://x.test/y", {"q": "1"}, ttl=3600)
    _restart()
    client = http._client = _Client({"a": 2})
    got = http.get_json("https://x.test/y", {"q": "1"}, ttl=3600)
    assert got.data == {"a": 1} and not got.stale and client.calls == 0


def test_without_any_cached_copy_a_dead_source_is_still_unavailable(disk):
    http._client = _Client(fail=True)
    with pytest.raises(SourceUnavailable):
        http.get_json("https://x.test/never-fetched")


def test_the_api_key_is_never_written_to_the_file(disk):
    http._client = _Client({"records": []})
    http.get_json("https://api.data.gov.in/resource/x", {"api-key": "SECRET123"})
    assert disk.exists() and b"SECRET123" not in disk.read_bytes()


def test_binary_bodies_round_trip(disk):
    http._client = type("B", (), {"get": lambda self, url, **kw: httpx.Response(200, content=b"II*\x00tiff", request=httpx.Request("GET", url))})()
    http.get_bytes("https://x.test/tiff")
    _restart()
    http._client = _Client(fail=True)
    assert http.get_bytes("https://x.test/tiff", ttl=0).data == b"II*\x00tiff"


def test_an_unwritable_path_disables_persistence_without_raising(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("CACHE_DB_PATH", str(blocker / "sub" / "cache.sqlite"))  # a "directory" that is a file
    get_settings.cache_clear()
    http._disk.clear()
    http._client = _Client({"a": 1})
    assert http.get_json("https://x.test/z").data == {"a": 1}
    monkeypatch.setenv("CACHE_DB_PATH", "")
    get_settings.cache_clear()
    http._disk.clear()
