"""In-memory sliding-window rate limit for chat and other quota-spending routes (single process; enough for a demo deployment)."""
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from app.core.config import get_settings

WINDOW_S = 60
IP_BUCKET_FACTOR = 3  # a shared venue network must not be punished for one busy client

_hits: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def check_rate(key: str, limit: int, now: float | None = None) -> bool:
    """Record a hit for `key`; False when `limit` hits already happened in the last WINDOW_S seconds."""
    now = time.time() if now is None else now
    with _lock:
        recent = [t for t in _hits[key] if now - t < WINDOW_S]
        allowed = len(recent) < limit
        if allowed:
            recent.append(now)
        _hits[key] = recent
        return allowed


def enforce(request: Request, session_id: str) -> None:
    """Two buckets: per client IP (3x the limit) and per IP+session. The IP bucket is what stops a client that
    simply rotates session_id. Best-effort anti-abuse: behind a proxy the IP comes from X-Forwarded-For, which a
    determined client can forge."""
    limit = get_settings().rate_limit_per_min
    if not limit:
        return
    ip = request.client.host if request.client else "?"
    if not check_rate(f"ip|{ip}", limit * IP_BUCKET_FACTOR) or not check_rate(f"{ip}|{session_id}", limit):
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.", headers={"Retry-After": str(WINDOW_S)})
