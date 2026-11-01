"""The ONLY place tools touch the network.

Live GET with a short TTL cache. If the live call fails but we hold an earlier
REAL response, we return it flagged `stale=True` with its true timestamp (the UI
shows "data from 12 min ago"). We never fabricate: no cache + failure -> SourceUnavailable.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.tools import SourceUnavailable
from app.tools.diskcache import DiskCache, cache_key

_cache: dict[str, tuple[float, object, float | None]] = {}  # key -> (fetched at, data, own ttl or None for the caller's)
EMPTY_TTL_S = 60.0  # an empty record list may be a blip of the shared free API: do not let it stand for half an hour
_lock = threading.Lock()
CACHE_MAX = 2000  # entries; oldest evicted first (unique places x days would otherwise grow without bound)

# Circuit breaker per host: a source that failed in two separate "rounds" (parallel calls that fail together count as one,
# and the orchestrator's single retry is the second) is not queried again for BREAKER_S seconds. Without it every question
# during an outage waits the full timeout twice. Stale-but-real cached data is still served while it is open.
BREAKER_S = 30.0
ROUND_GAP_S = 3.0
_fails: dict[str, dict] = {}
_client: httpx.Client | None = None
RAW_MAX, RAW_CHARS = 200, 6000
_raw: dict[str, dict] = {}  # public URL -> {fetched_at, body}: the real upstream answer, for the "show raw source" view
_disk: dict[str, DiskCache] = {}  # path -> open cache (one per configured path)
SECRET_PARAMS = {"api-key", "api_key", "apikey", "key"}
# data.gov.in's gateway hangs (502 after ~60 s) on the default "python-httpx" User-Agent but answers in <1 s when the
# client identifies itself. An honest, descriptive agent is also what API operators ask for.
USER_AGENT = "KisaanMitra/1.0 (FORGE 2026 hackathon; farm advisory)"


@dataclass
class Fetched:
    data: object
    url: str  # public URL (secrets removed) - safe to show in UI
    fetched_at: str  # ISO-8601 UTC of the real fetch
    stale: bool
    age_s: int


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(url: str, params: dict | None) -> str:
    safe = {k: v for k, v in (params or {}).items() if k.lower() not in SECRET_PARAMS}
    return url + ("?" + "&".join(f"{k}={v}" for k, v in safe.items()) if safe else "")


def _redact(msg: str, params: dict | None) -> str:
    for k, v in (params or {}).items():
        if k.lower() in SECRET_PARAMS and v:
            msg = msg.replace(str(v), "***")
    return msg


def _host(url: str) -> str:
    return httpx.URL(url).host


def _breaker_wait(host: str, now: float) -> float:
    st = _fails.get(host)
    if st and st["rounds"] >= 2 and now - st["last"] < BREAKER_S:
        return BREAKER_S - (now - st["last"])
    return 0.0


def _note_failure(host: str, now: float) -> None:
    with _lock:
        st = _fails.get(host)
        if st is None or now - st["last"] > 120:  # a long-quiet source starts from scratch
            _fails[host] = {"rounds": 1, "round_start": now, "last": now}
        else:
            if now - st["round_start"] > ROUND_GAP_S:
                st["rounds"] += 1
                st["round_start"] = now
            st["last"] = now


def _worth_counting(e: Exception) -> bool:
    """Outages count; a 400/403/404 is our request's fault and must not shut the source off."""
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500 or e.response.status_code == 429
    return isinstance(e, (httpx.TransportError, httpx.TimeoutException))


def get_json(url: str, params: dict | None = None, ttl: float | None = None, timeout: float | None = None) -> Fetched:
    return _get(url, params, ttl, timeout, as_bytes=False)


def get_bytes(url: str, params: dict | None = None, ttl: float | None = None, timeout: float | None = None) -> Fetched:
    """Same cache, breaker and stale rules as get_json, for a binary body (a tiny raster from a map service)."""
    return _get(url, params, ttl, timeout, as_bytes=True)


def _cache_key(url: str, params: dict | None) -> str:
    return url + "|" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))


def _disk_cache() -> DiskCache | None:
    """The persistent cache, or None when CACHE_DB_PATH is empty."""
    path = get_settings().cache_db_path
    if not path:
        return None
    if path not in _disk:
        _disk[path] = DiskCache(path)
    return _disk[path]


def _remembered(key: str, url: str, params: dict | None) -> tuple | None:
    """An entry from this process's memory, else from the disk file (which also warms the memory)."""
    with _lock:
        hit = _cache.get(key)
    if hit:
        return hit
    disk = _disk_cache()
    found = disk.get(cache_key(url, params, SECRET_PARAMS)) if disk else None
    if found:
        with _lock:
            _cache[key] = found
    return found


def _note_raw(public: str, fetched_iso: str, data: object) -> None:
    """Remember a clipped copy of what the source really answered (JSON only; never a secret: `public` has none)."""
    if isinstance(data, (bytes, bytearray)):
        return
    try:
        body = json.dumps(data, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        return
    clipped = len(body) > RAW_CHARS
    with _lock:
        _raw.pop(public, None)
        _raw[public] = {"url": public, "fetched_at": fetched_iso, "body": body[:RAW_CHARS], "clipped": clipped, "chars": len(body)}
        if len(_raw) > RAW_MAX:
            _raw.pop(next(iter(_raw)))


def raw_for(public_url: str) -> dict | None:
    """Only URLs this process has actually fetched can be looked up, so this can never be used to make the server call anywhere."""
    with _lock:
        return _raw.get(public_url)


def _from_cache(hit: tuple, url: str, params: dict | None, now: float, stale: bool) -> Fetched:
    fetched_at, data, _ = hit
    public = _public(url, params)
    _note_raw(public, _iso(fetched_at), data)
    return Fetched(data, public, _iso(fetched_at), stale, int(now - fetched_at))


def _is_empty_record_list(data: object) -> bool:
    return isinstance(data, dict) and isinstance(data.get("records"), list) and not data["records"]


def _session() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT})
    return _client


def _store(key: str, host: str, data: object, now: float, url: str = "", params: dict | None = None) -> None:
    own_ttl = EMPTY_TTL_S if _is_empty_record_list(data) else None
    disk = _disk_cache()
    if disk:
        disk.put(cache_key(url, params, SECRET_PARAMS), now, data, own_ttl)
    with _lock:
        _cache[key] = (now, data, own_ttl)
        if len(_cache) > CACHE_MAX:
            _cache.pop(next(iter(_cache)))  # oldest first
        _fails.pop(host, None)


def _get(url: str, params: dict | None, ttl: float | None, timeout: float | None, as_bytes: bool) -> Fetched:
    settings = get_settings()
    ttl = settings.cache_ttl_s if ttl is None else ttl
    key = _cache_key(url, params)
    now = time.time()
    hit = _remembered(key, url, params)
    if hit and now - hit[0] < (hit[2] if hit[2] is not None else ttl):
        return _from_cache(hit, url, params, now, stale=False)

    host = _host(url)
    with _lock:
        wait = _breaker_wait(host, now)
    if wait > 0:
        if hit:
            return _from_cache(hit, url, params, now, stale=True)
        raise SourceUnavailable(f"{host} is temporarily unreachable (not retrying for {int(wait) + 1}s)")

    try:
        response = _session().get(url, params=params, timeout=timeout or settings.tool_timeout_s)
        response.raise_for_status()
        data = response.content if as_bytes else response.json()
    except Exception as e:
        if _worth_counting(e):
            _note_failure(host, time.time())  # when it FAILED, not when it started (a timeout started seconds earlier)
        if hit:  # earlier REAL response, clearly labelled stale
            return _from_cache(hit, url, params, now, stale=True)
        raise SourceUnavailable(_redact(f"{type(e).__name__}: {e}", params)) from e
    _store(key, host, data, now, url, params)
    public = _public(url, params)
    _note_raw(public, _iso(now), data)
    return Fetched(data, public, _iso(now), False, 0)
