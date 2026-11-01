"""A farmer's session (saved place and crop, the last few turns) kept in SQLite, so a restart or a sleeping free-tier host does not make
the agent forget them. Only the farmer's own questions and the agent's answers are stored, for SESSION_MAX_AGE_S, never keys.

Never raises: a broken or read-only disk just means sessions live in memory only.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("sessions")
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SESSION_MAX_AGE_S = 7 * 86400
SESSION_MAX_ROWS = 2000
SCHEMA = "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, ts REAL NOT NULL, profile TEXT NOT NULL, history TEXT NOT NULL)"


class SessionStore:
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
                self._db.execute("DELETE FROM sessions WHERE ts < ?", (time.time() - SESSION_MAX_AGE_S,))
                self._db.execute("DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY ts DESC LIMIT ?)", (SESSION_MAX_ROWS,))
                self._db.commit()
            except Exception as e:
                log.warning("session store disabled: %s", type(e).__name__)
                self._failed, self._db = True, None
        return self._db

    def load(self, session_id: str) -> tuple[dict, list[dict]]:
        """-> (profile, history); both empty for an unknown or expired session."""
        with self._lock:
            db = self._connection()
            if db is None:
                return {}, []
            try:
                row = db.execute("SELECT ts, profile, history FROM sessions WHERE id = ?", (session_id,)).fetchone()
            except Exception as e:
                log.warning("session read failed: %s", type(e).__name__)
                return {}, []
        if row is None or row[0] < time.time() - SESSION_MAX_AGE_S:
            return {}, []
        try:
            profile, history = json.loads(row[1]), json.loads(row[2])
        except ValueError:
            return {}, []
        return (profile if isinstance(profile, dict) else {}), (history if isinstance(history, list) else [])

    def save(self, session_id: str, profile: dict, history: list[dict]) -> None:
        with self._lock:
            db = self._connection()
            if db is None:
                return
            try:
                db.execute(
                    "INSERT OR REPLACE INTO sessions (id, ts, profile, history) VALUES (?, ?, ?, ?)",
                    (session_id, time.time(), json.dumps(profile, ensure_ascii=False), json.dumps(history, ensure_ascii=False)),
                )
                db.commit()
            except Exception as e:
                log.warning("session write failed: %s", type(e).__name__)
