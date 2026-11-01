"""Shared fakes. No network, no keys, no real LLM (these test OUR logic, not the providers)."""
from app.llm.client import LLMResult
from app.memory.store import Hit


class StubStore:
    def __init__(self, hits=None):
        self.hits, self.mem = hits or [], []

    def count(self, collection):
        return len(self.hits) if collection == "knowledge" else len(self.mem)

    def search(self, collection, q, k=None, session_id=None):
        return self.hits if collection == "knowledge" else []

    def remember(self, session_id, text, kind="turn"):
        self.mem.append(text)

    def add(self, *a, **k):
        return 0


class FakeLLM:
    """chat_json -> router; chat -> scripted answers (last repeats). off=True simulates provider outage."""

    def __init__(self, answers=None, route="weather_advice", off=False, place="", crop=""):
        self.answers, self.route, self.n, self.off, self.last_messages, self.place, self.crop = answers or ["ok"], route, 0, off, None, place, crop

    def chat_json(self, msgs, **kw):
        if self.off:
            return None, LLMResult("", "offline", degraded=True)
        return {"intent": self.route, "language": "en", "place": self.place, "crop": self.crop, "reason": "test"}, LLMResult("{}", "primary")

    def chat(self, msgs, offline="", **kw):
        self.last_messages = msgs
        if self.off:
            return LLMResult(offline, "offline", degraded=True)
        t = self.answers[min(self.n, len(self.answers) - 1)]
        self.n += 1
        return LLMResult(t, "primary", "m")

    def vision(self, *a, **k):
        return LLMResult("", "offline", degraded=True)


def hit(text="doc text", source="doc.md"):
    return Hit(text, 0.5, source, {})
