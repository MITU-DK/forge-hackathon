"""What the pipeline hands back: the answer, the trace of every step, and the cards for the UI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class TraceStep:
    agent: str
    action: str
    detail: str = ""
    ms: int = 0
    ok: bool = True


@dataclass
class ChatResult:
    answer: str
    intent: str
    language: str
    sources: list[str] = field(default_factory=list)  # knowledge documents used
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False
    trace: list[TraceStep] = field(default_factory=list)
    live_sources: list[dict] = field(default_factory=list)  # {name,url,fetched_at,stale}
    cards: list[dict] = field(default_factory=list)  # forecast / price cards for the UI
    profile: dict = field(default_factory=dict)  # resolved place/state/lat/lon + crop
    evidence: list[dict] = field(default_factory=list)  # data lines the answer was written from, for tap-a-number proof

    def dict(self) -> dict:
        return asdict(self)
