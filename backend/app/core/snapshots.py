"""Watch mode memory: the last farm snapshot per device, in SQLite, so "what changed since last time" survives a restart.
Stores only numbers and rule codes (no questions, no keys), for SNAPSHOT_MAX_AGE_S. Never raises: a broken disk means memory only."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("snapshots")
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SNAPSHOT_MAX_AGE_S = 14 * 86400
SNAPSHOT_MAX_ROWS = 2000
SCHEMA = "CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)"


class SnapshotStore:
    def __init__(self, path: str):
        self.path = None if not path else (Path(path) if Path(path).is_absolute() else BACKEND_DIR / path)
        self._lock = threading.Lock()
        self._mem: dict[str, tuple[float, dict]] = {}
        self._db: sqlite3.Connection | None = None
        self._failed = not self.path

    def _connection(self) -> sqlite3.Connection | None:
        if self._db is None and not self._failed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
                self._db.execute(SCHEMA)
                self._db.execute("DELETE FROM snapshots WHERE ts < ?", (time.time() - SNAPSHOT_MAX_AGE_S,))
                self._db.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY ts DESC LIMIT ?)", (SNAPSHOT_MAX_ROWS,))
                self._db.commit()
            except Exception as e:
                log.warning("snapshot store disabled: %s", type(e).__name__)
                self._failed, self._db = True, None
        return self._db

    def load(self, key: str) -> tuple[float, dict] | None:
        """-> (saved_at, snapshot) or None."""
        with self._lock:
            db = self._connection()
            if db is not None:
                try:
                    row = db.execute("SELECT ts, data FROM snapshots WHERE id = ?", (key,)).fetchone()
                    if row and row[0] >= time.time() - SNAPSHOT_MAX_AGE_S:
                        return row[0], json.loads(row[1])
                    return None
                except Exception as e:
                    log.warning("snapshot read failed: %s", type(e).__name__)
            return self._mem.get(key)

    def save(self, key: str, data: dict) -> None:
        now = time.time()
        with self._lock:
            self._mem[key] = (now, data)
            if len(self._mem) > SNAPSHOT_MAX_ROWS:
                self._mem.pop(next(iter(self._mem)))
            db = self._connection()
            if db is not None:
                try:
                    db.execute("INSERT OR REPLACE INTO snapshots (id, ts, data) VALUES (?, ?, ?)", (key, now, json.dumps(data)))
                    db.commit()
                except Exception as e:
                    log.warning("snapshot write failed: %s", type(e).__name__)
