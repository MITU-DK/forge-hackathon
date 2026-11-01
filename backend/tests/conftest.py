"""Tests must be hermetic (no network, no keys in any test): no network, no keys. Real keys in .env would otherwise leak into
Settings() and make tests call live APIs. Env vars take priority over the .env file, so blank them here."""
import os

for _k in ("LLM_API_KEY", "LLM_API_KEY2", "LLM_API_KEY3", "FALLBACK_API_KEY", "DATA_GOV_API_KEY", "QDRANT_URL", "QDRANT_API_KEY", "CACHE_DB_PATH", "SESSION_DB_PATH", "SNAPSHOT_DB_PATH",
           "FALLBACK2_API_KEY", "FALLBACK2_BASE_URL", "FALLBACK2_MODEL", "WARM_PLACES", "TELEGRAM_BOT_TOKEN", "SUBSCRIBER_DB_PATH"):
    os.environ[_k] = ""

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

import pytest  # noqa: E402

import app.tools.location as _location  # noqa: E402
import app.tools.mandi as _mandi  # noqa: E402
import app.tools.rain_history as _rain  # noqa: E402
import app.tools.crop_fit as _crop_fit  # noqa: E402
import app.tools.soil as _soil  # noqa: E402
import app.tools.weather as _weather  # noqa: E402

_MODULES = (_location, _mandi, _rain, _weather, _crop_fit, _soil)
_ORIGINAL = {m: {n: getattr(m, n) for n in ("get_json", "get_bytes", "get_settings", "fetch_summary", "_grid") if hasattr(m, n)} for m in _MODULES}


@pytest.fixture(autouse=True)
def _restore_tool_network():
    """Some tests replace get_json / get_settings on the tool modules; undo it so tests cannot leak into each other."""
    yield
    for m, attrs in _ORIGINAL.items():
        for n, fn in attrs.items():
            setattr(m, n, fn)


import app.tools.http as _http  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_http_state():
    """Circuit-breaker and cache state are process-wide; never let one test's outage leak into the next."""
    _http._fails.clear()
    _http._cache.clear()
    _soil._summaries.clear()
    yield
    _http._fails.clear()
    _http._cache.clear()
    _soil._summaries.clear()


class _NoNetwork:
    def get(self, url, **kw):
        raise AssertionError(f"a test reached the real network ({str(url)[:60]}): stub get_json/get_bytes or the module under test")


@pytest.fixture(autouse=True)
def _block_real_http():
    """The shared HTTP client is the only way tools reach the network. In tests it must never be used for real: a test that
    forgot to stub a tool silently called live APIs (found on 31 Oct 2026: the crop-fit tests were hitting the real archive)."""
    previous = _http._client
    _http._client = _NoNetwork()
    yield
    _http._client = previous
