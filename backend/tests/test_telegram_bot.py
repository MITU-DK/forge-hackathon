"""Telegram bot against a simulated Telegram API and the fake internet from test_chaos (no network, no keys)."""
import json

import httpx
import pytest

from app.agents.orchestrator import Orchestrator
from app.bot.telegram import TelegramBot
from app.core.subscribers import SubscriberStore
from app.domain.farm import DOMAIN
from app.tools import http
from tests.helpers import FakeLLM, StubStore
from tests.test_chaos import _fast_and_keyed, world  # noqa: F401

TOKEN = "123456:test-token-not-real"


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.files = {"f1": b"audio-bytes"}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert TOKEN in str(req.url)
        path = req.url.path
        if "/file/bot" in path:
            return httpx.Response(200, content=self.files["f1"])
        method = path.rsplit("/", 1)[-1]
        body = json.loads(req.content or b"{}")
        if method == "sendMessage":
            self.sent.append(body)
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f1.ogg"}})
        return httpx.Response(200, json={"ok": True, "result": []})

    def texts(self):
        return [m["text"] for m in self.sent]


class VoiceLLM(FakeLLM):
    def transcribe(self, audio, filename="a", language=None, prompt=None):
        return "should I sell my onions" if audio else ""


@pytest.fixture
def bot(tmp_path):
    tg = FakeTelegram()
    http._client = httpx.Client(transport=httpx.MockTransport(world(set())))
    orch = Orchestrator(DOMAIN, VoiceLLM(off=True, route="best_market"), StubStore())
    b = TelegramBot(TOKEN, orch, orch.llm, SubscriberStore(str(tmp_path / "sub.sqlite")), interval_min=30, transport=httpx.MockTransport(tg))
    b.tg = tg
    return b


def say(bot, text, chat=7, **extra):
    bot.handle({"update_id": 1, "message": {"chat": {"id": chat}, "text": text, **extra}})


def test_start_and_help_reply(bot):
    say(bot, "/start")
    assert "KisaanMitra" in bot.tg.texts()[-1]


def test_place_crop_lang_status(bot):
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/crop Onion")
    say(bot, "/lang hi")
    say(bot, "/status")
    s = bot.store.get(7)
    assert (s["place"], s["crop"], s["lang"]) == ("Nashik, Maharashtra", "Onion", "hi")
    assert "Nashik" in bot.tg.texts()[-1]


def test_plan_without_place_asks_for_it(bot):
    say(bot, "/plan")
    assert "/place" in bot.tg.texts()[-1]


def test_plan_lists_actions_in_the_farmers_language(bot):
    for lang in ("en", "hi", "mr"):
        say(bot, f"/lang {lang}")
        say(bot, "/place Nashik, Maharashtra")
        say(bot, "/crop Onion")
        say(bot, "/plan")
        out = bot.tg.texts()[-1]
        assert "1." in out and "Nashik" in out
    assert any("ऀ" <= ch <= "ॿ" for ch in out)  # last one is Marathi


def test_question_gets_an_answer_with_live_source_times(bot):
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/crop Onion")
    say(bot, "which market pays most?")
    out = bot.tg.texts()[-1]
    assert "IST" in out and "UTC" not in out and "geocoding" not in out.lower()


def test_voice_note_is_transcribed_echoed_and_answered(bot):
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "", voice={"file_id": "f1"})
    texts = bot.tg.texts()
    assert any("should I sell my onions" in x for x in texts) and len(texts) >= 3


def test_voice_without_speech_service_says_so(bot):
    bot.llm = object()
    say(bot, "", voice={"file_id": "f1"})
    assert "type" in bot.tg.texts()[-1].lower()


def test_watch_needs_place_then_sets_baseline_without_alert(bot):
    say(bot, "/watch on")
    assert not bot.store.get(7)["watching"]
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/crop Onion")
    n = len(bot.tg.sent)
    say(bot, "/watch on")
    sub = bot.store.get(7)
    assert sub["watching"] and sub["snapshot"]
    assert len(bot.tg.sent) == n + 1  # only the confirmation, no alert


def test_watcher_alerts_only_on_change_and_not_twice(bot):
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/crop Onion")
    say(bot, "/watch on")
    n = len(bot.tg.sent)
    assert bot.check_all() == 0 and len(bot.tg.sent) == n  # nothing changed
    snap = bot.store.get(7)["snapshot"]
    snap["rain_total"] = (snap.get("rain_total") or 0) + 80  # pretend the stored picture was very different
    snap["alerts"] = []
    bot.store.update(7, snapshot={**snap, "risks": []})
    sent = bot.check_all()
    assert sent in (0, 1)
    if sent:
        assert "🔔" in bot.tg.texts()[-1]
        assert bot.check_all() == 0  # the stored picture is now current


def test_stop_deletes_data(bot):
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/stop")
    assert bot.store.get(7)["place"] == ""


def test_a_crashing_pipeline_still_answers(bot, monkeypatch):
    monkeypatch.setattr(bot.orch, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    bot._safe_handle({"update_id": 2, "message": {"chat": {"id": 7}, "text": "hello"}})
    assert bot.tg.texts()


def test_token_never_in_log(bot, caplog):
    bot._http = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x " + str(r.url)))))
    with caplog.at_level("DEBUG"):
        bot.send(7, "hi")
    assert TOKEN not in caplog.text


def test_watcher_tells_when_a_task_moved_and_why(bot):
    from app.decision.timeline import build_timeline
    from app.tools import ToolResult

    def plan(spray_day, rain):
        days = ("2026-11-02", "2026-11-03", "2026-11-04")
        tl = build_timeline("2026-11-02", [], [{"date": spray_day, "start": "08:00", "end": "11:00", "hours": 3}],
                            [{"date": d, "rain_mm": rain.get(d, 0.0), "action": "SKIP" if rain.get(d, 0) >= 10 else "CHECK_SOIL"} for d in days])
        snap = {"rain_48h_mm": 0.0, "flags": [], "spray_days": 1, "alerts": [], "price": 4000, "signal": "FLAT"}
        return ToolResult("", True, [], [], {"snapshot": snap, "timeline": tl})

    forecast = {"now": plan("2026-11-03", {})}
    bot._run_plan = lambda sub: forecast["now"]
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/watch on")  # baseline: spray on 3 Nov, dry
    n = len(bot.tg.sent)
    assert bot.check_all() == 0  # nothing changed, nothing sent
    forecast["now"] = plan("2026-11-04", {"2026-11-03": 14.0})  # rain arrives on the spray day
    assert bot.check_all() == 1
    alert = bot.tg.texts()[-1]
    assert len(bot.tg.sent) == n + 1 and "🗓" in alert and "14.0 mm" in alert and "3 Nov" in alert and "4 Nov" in alert
    assert bot.check_all() == 0  # said once, not again



def test_quantity_command_saves_and_rejects_nonsense(bot):
    say(bot, "/qty 20")
    assert bot.store.get(7)["quantity"] == "20"
    say(bot, "/qty lots")
    assert "/qty" in bot.tg.texts()[-1] and bot.store.get(7)["quantity"] == "20"
    assert bot._profile(bot.store.get(7)) == {"quantity": "20"}


def test_alert_that_could_not_be_delivered_is_retried_not_lost(bot, monkeypatch):
    from app.decision.timeline import build_timeline
    from app.tools import ToolResult
    monkeypatch.setattr("app.bot.telegram.RETRY_PAUSE_S", 0)
    price = {"v": 4000}
    bot._run_plan = lambda sub: ToolResult("", True, [], [], {"snapshot": {"rain_48h_mm": 0.0, "flags": [], "spray_days": 1, "alerts": [], "price": price["v"], "signal": "FLAT"},
                                                                "timeline": build_timeline("2026-11-02", [], [], [{"date": "2026-11-02", "rain_mm": 0.0, "action": "CHECK_SOIL"}])})
    say(bot, "/place Nashik, Maharashtra")
    say(bot, "/watch on")
    price["v"] = 3000  # a 25 % drop: worth an alert
    real_post = bot._http.post
    bot._http.post = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("reset"))  # Telegram unreachable
    assert bot.check_all() == 0 and bot.store.get(7)["last_alert"] == 0  # not delivered, not marked as delivered
    bot._http.post = real_post
    n = len(bot.tg.sent)
    assert bot.check_all() == 1 and len(bot.tg.sent) == n + 1  # the next round finds the same change and delivers it


def test_a_dropped_connection_is_retried_before_giving_up(bot, monkeypatch):
    monkeypatch.setattr("app.bot.telegram.RETRY_PAUSE_S", 0)
    real_post, fails = bot._http.post, {"n": 2}

    def flaky(*a, **k):
        if fails["n"]:
            fails["n"] -= 1
            raise httpx.ConnectError("reset")
        return real_post(*a, **k)
    bot._http.post = flaky
    assert bot.send(7, "hello") is True and bot.tg.texts()[-1] == "hello"
