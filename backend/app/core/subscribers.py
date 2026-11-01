"""Telegram farmers who asked to be watched: chat id, place, crop, language and the last numbers seen (for "what changed").
Stores no message text and no keys. Never raises: a broken disk means the list lives in memory until restart."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("subscribers")
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
MAX_ROWS = 5000
FIELDS = ("place", "crop", "lang", "watching", "snapshot", "last_alert", "quantity")
SCHEMA = ("CREATE TABLE IF NOT EXISTS subscribers (chat_id TEXT PRIMARY KEY, ts REAL NOT NULL, place TEXT NOT NULL DEFAULT '', crop TEXT NOT NULL DEFAULT '', "
          "lang TEXT NOT NULL DEFAULT '', watching INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '', last_alert REAL NOT NULL DEFAULT 0, quantity TEXT NOT NULL DEFAULT '')")


class SubscriberStore:
    def __init__(self, path: str):
        self.path = None if not path else (Path(path) if Path(path).is_absolute() else BACKEND_DIR / path)
        self._lock = threading.Lock()
        self._mem: dict[str, dict] = {}
        self._db: sqlite3.Connection | None = None
        self._failed = not self.path
        self._loaded = False

    def _connection(self) -> sqlite3.Connection | None:
        if self._db is None and not self._failed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
                self._db.execute(SCHEMA)
                if "quantity" not in {r[1] for r in self._db.execute("PRAGMA table_info(subscribers)")}:
                    self._db.execute("ALTER TABLE subscribers ADD COLUMN quantity TEXT NOT NULL DEFAULT ''")  # file made by an older version
                self._db.commit()
            except Exception as e:
                log.warning("subscriber store disabled: %s", type(e).__name__)
                self._failed, self._db = True, None
        return self._db

    def _load_all(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        db = self._connection()
        if db is None:
            return
        try:
            for row in db.execute("SELECT chat_id, place, crop, lang, watching, snapshot, last_alert, quantity FROM subscribers").fetchall():
                snap = json.loads(row[5]) if row[5] else None
                self._mem[row[0]] = {"chat_id": row[0], "place": row[1], "crop": row[2], "lang": row[3], "watching": bool(row[4]), "snapshot": snap, "last_alert": row[6], "quantity": row[7]}
        except Exception as e:
            log.warning("subscriber read failed: %s", type(e).__name__)

    def get(self, chat_id: str | int) -> dict:
        """The subscriber, or a fresh empty record (never None)."""
        with self._lock:
            self._load_all()
            return dict(self._mem.get(str(chat_id)) or {"chat_id": str(chat_id), "place": "", "crop": "", "lang": "", "watching": False, "snapshot": None, "last_alert": 0.0, "quantity": ""})

    def update(self, chat_id: str | int, **fields) -> dict:
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise ValueError(f"unknown subscriber fields: {unknown}")
        with self._lock:
            self._load_all()
            rec = self._mem.get(str(chat_id)) or {"chat_id": str(chat_id), "place": "", "crop": "", "lang": "", "watching": False, "snapshot": None, "last_alert": 0.0, "quantity": ""}
            rec.update(fields)
            self._mem[str(chat_id)] = rec
            if len(self._mem) > MAX_ROWS:
                self._mem.pop(next(iter(self._mem)))
            db = self._connection()
            if db is not None:
                try:
                    db.execute("INSERT OR REPLACE INTO subscribers (chat_id, ts, place, crop, lang, watching, snapshot, last_alert, quantity) VALUES (?,?,?,?,?,?,?,?,?)",
                               (rec["chat_id"], time.time(), rec["place"], rec["crop"], rec["lang"], int(rec["watching"]), json.dumps(rec["snapshot"]) if rec["snapshot"] else "", rec["last_alert"], rec["quantity"]))
                    db.commit()
                except Exception as e:
                    log.warning("subscriber write failed: %s", type(e).__name__)
            return dict(rec)

    def delete(self, chat_id: str | int) -> None:
        with self._lock:
            self._load_all()
            self._mem.pop(str(chat_id), None)
            db = self._connection()
            if db is not None:
                try:
                    db.execute("DELETE FROM subscribers WHERE chat_id = ?", (str(chat_id),))
                    db.commit()
                except Exception as e:
                    log.warning("subscriber delete failed: %s", type(e).__name__)

    def watched(self) -> list[dict]:
        with self._lock:
            self._load_all()
            return [dict(r) for r in self._mem.values() if r["watching"] and r["place"]]
