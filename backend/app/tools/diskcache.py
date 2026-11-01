"""Survives a restart: the last REAL response of every live source, kept in a small SQLite file.

Why: the in-memory cache is empty after a restart (or a free-tier host waking from sleep). If a source is down at that moment the
agent would have nothing to show, although it held real prices an hour ago. With this file it serves that response, labelled
stale with its true timestamp (see http.py); it never invents anything. Nothing secret is stored: the key is a hash of the request
without its api-key parameter, and only the response body is saved.

Never raises: a broken or read-only disk just means no persistence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("tool.diskcache")
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
MAX_AGE_S = 7 * 86400  # older than a week is no longer worth showing, even labelled stale
MAX_ROWS = 2000
SCHEMA = "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, ts REAL NOT NULL, ttl REAL, kind TEXT NOT NULL, body BLOB NOT NULL)"


def cache_key(url: str, params: dict | None, secret_params: set[str]) -> str:
    """Stable id of a request that does not depend on (or reveal) the api key."""
    public = sorted((k, str(v)) for k, v in (params or {}).items() if k.lower() not in secret_params)
    return hashlib.sha256(json.dumps([url, public], default=str).encode()).hexdigest()


class DiskCache:
    def __init__(self, path: str):
        file = Path(path)
        self.path = file if file.is_absolute() else BACKEND_DIR / file
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._failed = False

    def _connection(self) -> sqlite3.Connection | None:
        if self._db is None and not self._failed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
                self._db.execute(SCHEMA)
                self._prune(self._db)
            except Exception as e:
                log.warning("disk cache disabled: %s", type(e).__name__)
                self._failed, self._db = True, None
        return self._db

    @staticmethod
    def _prune(db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM cache WHERE ts < ?", (time.time() - MAX_AGE_S,))
        db.execute("DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY ts DESC LIMIT ?)", (MAX_ROWS,))
        db.commit()

    def get(self, key: str) -> tuple[float, object, float | None] | None:
        """-> (fetched at, data, own ttl or None) or None."""
        with self._lock:
            db = self._connection()
            if db is None:
                return None
            try:
                row = db.execute("SELECT ts, ttl, kind, body FROM cache WHERE key = ?", (key,)).fetchone()
            except Exception as e:
                log.warning("disk cache read failed: %s", type(e).__name__)
                return None
        if row is None:
            return None
        ts, ttl, kind, body = row
        try:
            return ts, (bytes(body) if kind == "bytes" else json.loads(body)), ttl
        except ValueError:
            return None

    def put(self, key: str, ts: float, data: object, ttl: float | None) -> None:
        kind, body = ("bytes", data) if isinstance(data, (bytes, bytearray)) else ("json", json.dumps(data))
        with self._lock:
            db = self._connection()
            if db is None:
                return
            try:
                db.execute("INSERT OR REPLACE INTO cache (key, ts, ttl, kind, body) VALUES (?, ?, ?, ?, ?)", (key, ts, ttl, kind, body))
                db.commit()
            except Exception as e:
                log.warning("disk cache write failed: %s", type(e).__name__)
