"""Telegram bot: the same agent as the web app, on the farmer's own phone, plus a watcher that messages when something changes.

* Text and voice notes go through the normal pipeline (router, live tools, guardrails): the same answers as the web app.
* /plan is today's ranked plan with no AI, in the farmer's language.
* /watch on makes a background thread re-check the farm every WATCH_INTERVAL_MIN minutes and send only what changed.
Long polling, so no public URL is needed. The token only ever appears in the request URL (which is never logged here)."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import logging
import threading
import time

import httpx

from app.bot.texts import t
from app.core.subscribers import SubscriberStore
from app.decision.market_rank import parse_quantity
from app.decision.timeline import replan
from app.decision.watch import changes
from app.domain.farm.plan_text import action_text, change_text
from app.domain.farm.timeline_text import change_text as move_text
from app.tools import NeedsInput

log = logging.getLogger("telegram")
API = "https://api.telegram.org"
POLL_TIMEOUT_S = 25
MAX_MESSAGE = 3800  # Telegram's limit is 4096 characters
MAX_ALERT_LINES = 5
SEND_ATTEMPTS, RETRY_PAUSE_S = 3, 1.0
ALERT_MIN_GAP_S = 3600  # a second alert within an hour is sent only if it has an urgent (severity 1) change
LANGS = ("en", "hi", "mr")
LANG_NAMES = {"en": "English", "hi": "हिन्दी", "mr": "मराठी"}


def _ist(iso_utc: str) -> str:
    """'2026-11-01T10:58:00Z' -> '16:28 IST' (the farmer's clock, not UTC)."""
    try:
        t = dt.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S") + dt.timedelta(hours=5, minutes=30)
        return t.strftime("%H:%M IST")
    except ValueError:
        return iso_utc


class TelegramBot:
    def __init__(self, token: str, orchestrator, llm, store: SubscriberStore, *, interval_min: int = 30, transport: httpx.BaseTransport | None = None):
        self.token, self.orch, self.llm, self.store, self.interval_s = token, orchestrator, llm, store, max(1, interval_min) * 60
        self._http = httpx.Client(timeout=POLL_TIMEOUT_S + 10, transport=transport)
        self._stop = threading.Event()
        self._offset = 0
        self._pool = cf.ThreadPoolExecutor(4, thread_name_prefix="tg")

    # ---------------------------------------------------------------- telegram api
    def _call(self, method: str, **params) -> dict | None:
        """Telegram answers, or None. Dropped connections (common on flaky links) are retried; the URL holds the token, so only the error type is logged."""
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                r = self._http.post(f"{API}/bot{self.token}/{method}", json=params)
                data = r.json()
                return data.get("result") if data.get("ok") else None
            except Exception as e:
                log.warning("telegram %s failed (attempt %d): %s", method, attempt, type(e).__name__)
                if attempt < SEND_ATTEMPTS and not self._stop.wait(RETRY_PAUSE_S * attempt):
                    continue
        return None

    def send(self, chat_id, text: str) -> bool:
        """True only if every part of the message was accepted by Telegram."""
        ok = True
        for i in range(0, max(len(text), 1), MAX_MESSAGE):
            ok = self._call("sendMessage", chat_id=chat_id, text=text[i:i + MAX_MESSAGE]) is not None and ok
        return ok

    def _download(self, file_id: str) -> bytes | None:
        info = self._call("getFile", file_id=file_id)
        if not info or not info.get("file_path"):
            return None
        try:
            r = self._http.get(f"{API}/file/bot{self.token}/{info['file_path']}")
            return r.content if r.status_code == 200 else None
        except Exception as e:
            log.warning("telegram file download failed: %s", type(e).__name__)
            return None

    # ---------------------------------------------------------------- polling
    def poll_once(self) -> int:
        updates = self._call("getUpdates", offset=self._offset, timeout=POLL_TIMEOUT_S, allowed_updates=["message"]) or []
        for u in updates:
            self._offset = max(self._offset, u["update_id"] + 1)
            self._pool.submit(self._safe_handle, u)
        return len(updates)

    def _safe_handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        try:
            self.handle(update)
        except Exception as e:  # a bug must never kill the bot or go silent on the farmer
            log.error("bot handler error: %s", type(e).__name__, exc_info=True)
            if chat is not None:
                self.send(chat, t(self.store.get(chat)["lang"] or "en", "busy"))

    def run(self) -> None:
        log.info("telegram bot polling")
        failures = 0
        while not self._stop.is_set():
            try:
                self.poll_once()
                failures = 0
            except Exception as e:
                failures += 1
                log.warning("poll failed: %s", type(e).__name__)
            if failures:
                self._stop.wait(min(30, 2 ** failures))

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- messages
    def handle(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        sub = self.store.get(chat_id)
        lang = sub["lang"] or "en"
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            return self._command(chat_id, sub, text)
        if msg.get("voice") or msg.get("audio"):
            return self._voice(chat_id, sub, (msg.get("voice") or msg.get("audio"))["file_id"])
        if msg.get("photo"):
            return self.send(chat_id, t(lang, "photo"))
        if text:
            self._ask(chat_id, sub, text)

    def _command(self, chat_id, sub: dict, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd, arg, lang = cmd.split("@")[0].lower(), arg.strip(), sub["lang"] or "en"
        if cmd in ("/start", "/help"):
            return self.send(chat_id, t(lang, "welcome" if cmd == "/start" else "help"))
        if cmd == "/place":
            if not arg:
                return self.send(chat_id, t(lang, "need_place"))
            self.store.update(chat_id, place=arg[:100], snapshot=None)
            return self.send(chat_id, t(lang, "place_set", place=arg[:100]))
        if cmd == "/crop":
            if not arg:
                return self.send(chat_id, t(lang, "status", **self._status_vars(sub)))
            self.store.update(chat_id, crop=arg[:60], snapshot=None)
            return self.send(chat_id, t(lang, "crop_set", crop=arg[:60]))
        if cmd in ("/qty", "/quantity"):
            q = parse_quantity(arg)
            if q is None:
                return self.send(chat_id, t(lang, "qty_bad"))
            self.store.update(chat_id, quantity=f"{q:g}")
            return self.send(chat_id, t(lang, "qty_set", qty=f"{q:g}"))
        if cmd == "/lang":
            if arg.lower() not in LANGS:
                return self.send(chat_id, t(lang, "lang_bad"))
            self.store.update(chat_id, lang=arg.lower())
            return self.send(chat_id, t(arg.lower(), "lang_set"))
        if cmd == "/watch":
            return self._watch(chat_id, sub, arg.lower())
        if cmd == "/status":
            return self.send(chat_id, t(lang, "status", **self._status_vars(sub)))
        if cmd == "/plan":
            return self.send(chat_id, self.plan_message(sub))
        if cmd == "/stop":
            self.store.delete(chat_id)
            return self.send(chat_id, t(lang, "stopped"))
        self.send(chat_id, t(lang, "help"))

    def _status_vars(self, sub: dict) -> dict:
        lang = sub["lang"] or "en"
        return {"place": sub["place"] or t(lang, "unset"), "crop": sub["crop"] or t(lang, "unset"), "language": LANG_NAMES.get(sub["lang"], "auto"),
                "watch": t(lang, "on" if sub["watching"] else "off")}

    def _watch(self, chat_id, sub: dict, arg: str) -> None:
        lang = sub["lang"] or "en"
        if arg != "on":
            self.store.update(chat_id, watching=False)
            return self.send(chat_id, t(lang, "watch_off"))
        if not sub["place"]:
            return self.send(chat_id, t(lang, "watch_need"))
        self.store.update(chat_id, watching=True, snapshot=None)  # the first check after this only records a baseline
        self.send(chat_id, t(lang, "watch_on", place=sub["place"], mins=self.interval_s // 60))
        self.check_one(self.store.get(chat_id), baseline_only=True)

    def _profile(self, sub: dict) -> dict:
        return {k: v for k, v in (("place", sub["place"]), ("crop", sub["crop"]), ("quantity", sub["quantity"])) if v}

    def _ask(self, chat_id, sub: dict, text: str) -> None:
        self._call("sendChatAction", chat_id=chat_id, action="typing")
        r = self.orch.run(text, f"tg:{chat_id}", sub["lang"] or None, None, self._profile(sub))
        self._learn(chat_id, sub, r)
        self.send(chat_id, self._format(r, sub["lang"] or r.language))

    def _learn(self, chat_id, sub: dict, r) -> None:
        """Remember the place, crop and language the answer was based on, like the web app's profile bar does."""
        new = {}
        if not sub["place"] and r.profile.get("place"):
            new["place"] = ", ".join(x for x in (r.profile["place"], r.profile.get("state")) if x)
        if not sub["crop"] and r.profile.get("crop"):
            new["crop"] = r.profile["crop"]
        if not sub["lang"] and r.language in LANGS:
            new["lang"] = r.language
        if new:
            self.store.update(chat_id, **new)

    @staticmethod
    def _format(r, lang: str) -> str:
        out = r.answer.strip()
        stamps, seen = [], set()
        for src in r.live_sources:  # each data source once, newest first; the place lookup is plumbing, not data the farmer asked for
            if src["name"] in seen or "geocoding" in src["name"].lower() or not src.get("fetched_at"):
                continue
            seen.add(src["name"])
            stamps.append(f"{src['name']} {_ist(src['fetched_at'])}")
        if stamps:
            out += f"\n\n{t(lang, 'sources')}: " + " · ".join(stamps[:4])
        return out

    def _voice(self, chat_id, sub: dict, file_id: str) -> None:
        lang = sub["lang"] or "en"
        audio = self._download(file_id)
        if audio is None or not hasattr(self.llm, "transcribe"):
            return self.send(chat_id, t(lang, "voice_off"))
        try:
            heard = self.llm.transcribe(audio, "voice.ogg", sub["lang"] or None).strip()
        except Exception as e:
            log.warning("transcription failed: %s", type(e).__name__)
            return self.send(chat_id, t(lang, "voice_off"))
        if not heard:
            return self.send(chat_id, t(lang, "no_voice"))
        self.send(chat_id, t(lang, "heard", text=heard))
        self._ask(chat_id, sub, heard)

    # ---------------------------------------------------------------- plan and watcher
    def _run_plan(self, sub: dict):
        return self.orch.domain.tools["farm_plan"]("", self._profile(sub))

    def plan_message(self, sub: dict) -> str:
        lang = sub["lang"] or "en"
        if not sub["place"]:
            return t(lang, "need_place")
        try:
            r = self._run_plan(sub)
        except NeedsInput:
            return t(lang, "need_place")
        if not r.ok:
            return t(lang, "plan_fail")
        card = r.cards[0]
        lines = [t(lang, "plan_head", place=sub["place"]), ""]
        lines += [f"{a['rank']}. {action_text(a, lang)}" for a in card["actions"][:6]] or [t(lang, "plan_none")]
        if card.get("skipped"):
            from app.domain.farm.plan_text import words
            names = [words(lang)["checks"].get(k, k) for k in card.get("skipped_codes", [])]
            lines += ["", t(lang, "plan_skipped", what="; ".join(names))]
        return "\n".join(lines)

    def check_one(self, sub: dict, baseline_only: bool = False) -> bool:
        """One watch round for one farm. Sends an alert only when a real change was found. Returns True if it did."""
        lang = sub["lang"] or "en"
        try:
            r = self._run_plan(sub)
        except Exception as e:
            log.warning("watch check failed: %s", type(e).__name__)
            return False
        if not r.ok:
            return False
        tl = r.facts.get("timeline")
        snap = {**r.facts["snapshot"], "_tl": tl}  # the timeline rides along in the stored picture
        before = sub["snapshot"] or None
        found = [] if baseline_only else changes(before, snap)
        moves = [] if baseline_only or not before else replan(before.get("_tl"), tl) if tl else []
        if not found and not moves:
            self.store.update(sub["chat_id"], snapshot=snap)
            return False
        urgent = any(c["severity"] == 1 for c in found)
        if not urgent and time.time() - sub["last_alert"] < ALERT_MIN_GAP_S:
            self.store.update(sub["chat_id"], snapshot=snap)
            return False
        body = "\n".join([f"• {change_text(c, lang)}" for c in found[:MAX_ALERT_LINES]] + [f"🗓 {move_text(m, lang)}" for m in moves[:MAX_ALERT_LINES]])
        if not self.send(sub["chat_id"], f"{t(lang, 'alert_head', place=sub['place'])}\n\n{body}\n\n{t(lang, 'alert_foot')}"):
            log.warning("alert not delivered; will try again next round")
            return False  # the stored picture stays old, so the same change is found and sent again next round
        self.store.update(sub["chat_id"], snapshot=snap, last_alert=time.time())
        return True

    def check_all(self) -> int:
        sent = 0
        for sub in self.store.watched():
            sent += self.check_one(sub)
        return sent

    def run_watcher(self) -> None:
        log.info("telegram watcher every %d s", self.interval_s)
        while not self._stop.wait(self.interval_s):
            try:
                self.check_all()
            except Exception as e:
                log.error("watcher round failed: %s", type(e).__name__, exc_info=True)

    def start(self) -> list[threading.Thread]:
        threads = [threading.Thread(target=self.run, daemon=True, name="tg-poll"), threading.Thread(target=self.run_watcher, daemon=True, name="tg-watch")]
        for th in threads:
            th.start()
        return threads
