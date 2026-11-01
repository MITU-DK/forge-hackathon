import hashlib
import json
import logging
import secrets
import time
from functools import lru_cache

import httpx
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.orchestrator import ChatResult, TraceStep, get_orchestrator
from app.api.ratelimit import enforce
from app.core import stats
from app.core.config import get_settings
from app.core.snapshots import SnapshotStore
from app.decision.timeline import replan
from app.decision.watch import changes
from app.domain.farm.timeline_text import change_text as timeline_change, task_text
from app.llm.client import get_llm
from app.memory.store import KNOWLEDGE, MEMORY, get_store
from app.tools import NeedsInput, PlaceNotFound
from app.tools.http import raw_for
from app.tools.reverse_geo import locate

router = APIRouter(prefix="/api")
log = logging.getLogger("api")
MAX_AUDIO_BYTES = 5_000_000
MIN_AUDIO_BYTES = 1000  # smaller than this cannot hold a spoken question
MAX_INGEST_TEXTS, MAX_INGEST_CHARS = 50, 5000
LIVE_CHECK_TOOLS = ("weather", "mandi", "rain_history")
AUDIO_EXTS = {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "opus", "wav", "webm"}  # what the speech API accepts
AUDIO_TYPES = {
    "audio/webm": "webm", "video/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a", "audio/ogg": "ogg",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/flac": "flac",
}
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _error_result(language: str | None) -> dict:
    """A pipeline bug must degrade visibly, never become a 500 mid-demo (the answer must always degrade visibly)."""
    msg = "Something went wrong on my side. Please try again in a moment."
    step = TraceStep("Pipeline", "internal error", "safe answer returned", 0, False)
    return ChatResult(msg, "general", language or "en", degraded=True, trace=[step]).dict()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class ChatIn(BaseModel):
    message: str = Field("", max_length=4000)
    session_id: str = Field("default", max_length=100)
    language: str | None = None
    image_b64: str | None = Field(None, max_length=7_000_000)  # base64 JPEG (no data: prefix), ~5 MB
    profile: dict = Field(default_factory=dict)  # {place, crop, ...} from the UI profile bar


class PlanIn(BaseModel):
    lang: str = Field("en", max_length=5)  # language of the timeline sentences
    client_id: str = Field("", max_length=100)  # a random id the browser keeps, so "since last time" is per device
    profile: dict = Field(default_factory=dict)


@lru_cache
def get_snapshots() -> SnapshotStore:
    return SnapshotStore(get_settings().snapshot_db_path)


def _watch_key(client_id: str, profile: dict) -> str:
    """Per device AND per farm: changing the place or crop starts a fresh comparison instead of reporting a fake change."""
    raw = "|".join([client_id, str(profile.get("place", "")).strip().lower(), str(profile.get("crop", "")).strip().lower()])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _timeline(body: PlanIn, key: str, tl: dict | None) -> dict | None:
    """The next days' tasks in the farmer's language, plus what moved since this device last looked and why."""
    if not tl:
        return None
    lang = body.lang if body.lang in ("en", "hi", "mr") else "en"
    before = get_snapshots().load("tl:" + key) if body.client_id else None
    moves = replan(before[1], tl) if before else []
    if body.client_id:
        get_snapshots().save("tl:" + key, tl)
    return {"tasks": [{**t, "text": task_text(t, lang)} for t in tl["tasks"]], "changes": [{**c, "text": timeline_change(c, lang)} for c in moves]}


@router.post("/plan")
def todays_plan(body: PlanIn, request: Request):
    """Today's farm plan without the AI: ranked actions from all live sources, plus what changed since this device last looked.
    The UI calls it on open and every few minutes (watch mode). No LLM is involved, so it is fast and works with the AI offline."""
    enforce(request, "plan:" + (body.client_id or "anon"))
    profile = {k: str(v)[:100] for k, v in body.profile.items() if k in ("place", "crop", "sowing_date", "quantity") and v}
    try:
        r = get_orchestrator().domain.tools["farm_plan"]("", profile)
    except NeedsInput as e:
        return {"ok": False, "needs": e.field, "hint": e.hint}
    except PlaceNotFound:
        return {"ok": False, "error": "place_not_found"}
    except Exception as e:
        log.warning("plan failed: %s", type(e).__name__)
        return {"ok": False, "error": "unavailable"}
    if not r.ok:
        return {"ok": False, "error": "place_not_found" if "place not found" in r.text else "unavailable"}
    snap, key, now = r.facts["snapshot"], _watch_key(body.client_id, profile), time.time()
    before = get_snapshots().load(key) if body.client_id else None
    diff = changes(before[1], snap) if before else []
    if body.client_id:
        get_snapshots().save(key, snap)
    timeline = _timeline(body, key, r.facts.get("timeline"))
    return {
        "ok": True, "plan": r.cards[0], "timeline": timeline, "cards": r.cards[1:], "sources": r.sources, "changes": diff,
        "since": before[0] if before else None, "fetched_at": now, "location": r.facts["location"],
    }


@router.get("/raw")
def raw_source(request: Request, url: str = Query(..., max_length=600)):
    """'Show raw source': the real upstream answer (clipped) for a URL listed under an answer's live data. Only URLs this server has
    already fetched are known, so it cannot be used to reach any other address."""
    enforce(request, "raw")
    found = raw_for(url)
    if found is None:
        raise HTTPException(404, "No such source was fetched recently.")
    return found


@router.get("/health")
def health():
    s = get_settings()
    st = get_store()
    return {
        "status": "ok",
        "domain": s.domain,
        "llm_configured": bool(s.llm_api_key),
        "mandi_key_configured": bool(s.data_gov_api_key),
        "fallback_configured": bool(s.fallback_api_key or s.fallback2_api_key),
        "knowledge_chunks": st.count(KNOWLEDGE),
        "memories": st.count(MEMORY),
    }


@router.get("/stats")
def get_stats():
    """Aggregate counters since process start (no user text)."""
    return stats.snapshot()


@router.get("/domain")
def domain():
    d = get_orchestrator().domain
    return {
        "title": d.title,
        "tagline": d.tagline,
        "suggested_prompts": d.suggested_prompts,
        "suggested_prompts_by_lang": d.suggested_prompts_by_lang,
        "languages": d.languages,
        "profile_fields": d.profile_fields,
    }


@router.post("/chat")
def chat(body: ChatIn, request: Request):
    enforce(request, body.session_id)
    try:
        return get_orchestrator().run(body.message, body.session_id, body.language, body.image_b64, body.profile).dict()
    except Exception:
        log.error("chat pipeline error", exc_info=True)
        return _error_result(body.language)


@router.post("/chat/stream")
def chat_stream(body: ChatIn, request: Request):
    """Server-sent events: one `trace` event per pipeline step, then one `final`."""
    enforce(request, body.session_id)
    orch = get_orchestrator()

    def gen():
        try:
            for ev in orch.stream(body.message, body.session_id, body.language, body.image_b64, body.profile):
                if ev["type"] == "final":
                    ev = {"type": "final", "result": ev["result"].dict()}
                yield _sse(ev)
        except Exception:
            log.error("chat stream error", exc_info=True)
            yield _sse({"type": "final", "result": _error_result(body.language)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=STREAM_HEADERS)


@router.get("/live-check")
def live_check(request: Request, place: str = "Nashik, Maharashtra", crop: str = "Onion"):
    """Calls each live tool once and reports ok/source/first line. Use before every demo."""
    enforce(request, "live-check")  # it spends live-API quota, so it is rate limited like chat
    profile = {"place": place[:100], "crop": crop[:60]}
    tools = get_orchestrator().domain.tools
    return {name: _check_tool(tools[name], profile) for name in LIVE_CHECK_TOOLS if name in tools}


def _check_tool(tool, profile: dict) -> dict:
    try:
        r = tool("", profile)
    except NeedsInput as e:
        return {"ok": False, "first_line": f"needs {e.field}"}
    except Exception as e:
        return {"ok": False, "first_line": f"{type(e).__name__}: {e}"}
    return {"ok": r.ok, "first_line": r.text.splitlines()[0] if r.text else "", "sources": r.sources}


def audio_name(filename: str | None, content_type: str | None) -> str:
    """Name for the speech API, whose container detection follows the extension: Safari sends mp4, other browsers webm."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in AUDIO_EXTS:
        ext = AUDIO_TYPES.get((content_type or "").split(";")[0].strip().lower(), "webm")
    return f"audio.{ext}"


@router.post("/voice/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), language: str | None = Form(None)):
    enforce(request, "voice")
    try:
        audio = await file.read(MAX_AUDIO_BYTES + 1)  # never buffer an unbounded upload
        if len(audio) > MAX_AUDIO_BYTES:
            return {"text": "", "error": "Audio is too large (limit 5 MB). Please record a shorter question."}
        if len(audio) < MIN_AUDIO_BYTES:
            return {"text": "", "error": "Audio is too short. Please speak a little longer."}
        domain = get_orchestrator().domain
        lang = language if language in domain.languages else None  # never pass a client-chosen string on to the provider
        # the call blocks for seconds: off the event loop, or every other request waits behind one transcription
        text = await run_in_threadpool(get_llm().transcribe, audio, audio_name(file.filename, file.content_type), lang, domain.stt_prompts.get(lang or ""))
        return {"text": text}
    except Exception as e:
        log.warning("transcription failed: %s", type(e).__name__)  # type only: the message holds the provider URL
        busy = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
        return {"text": "", "error": "The speech service is busy. Please try again in a minute." if busy else "Speech recognition failed. Please type your question."}


@router.get("/locate")
def locate_place(request: Request, lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    """'Use my location': coordinates -> a place name the weather and mandi tools resolve. Never stores or logs the coordinates."""
    enforce(request, "locate")
    try:
        return locate(lat, lon)
    except PlaceNotFound as e:
        return {"error": f"I could not match your location to a place in India ({e}). Please type your village or town."}
    except Exception as e:
        log.warning("locate failed: %s", type(e).__name__)
        return {"error": "The location service is not reachable right now. Please type your village or town."}


@router.post("/ingest")
def ingest(payload: dict, x_admin_key: str | None = Header(None)):
    """POST {"texts": ["...", "..."], "source": "name"} with header X-Admin-Key to add REAL knowledge live.
    Disabled unless INGEST_API_KEY is set: an open endpoint would let anyone plant text the agent then retrieves."""
    key = get_settings().ingest_api_key
    if not key:
        raise HTTPException(403, "Ingest is disabled (set INGEST_API_KEY to enable it).")
    if not x_admin_key or not secrets.compare_digest(x_admin_key.encode(), key.encode()):
        raise HTTPException(401, "Invalid admin key.")
    raw = payload.get("texts", [])
    texts = [t[:MAX_INGEST_CHARS] for t in (raw if isinstance(raw, list) else []) if isinstance(t, str) and t.strip()][:MAX_INGEST_TEXTS]
    source = str(payload.get("source", "live-upload"))[:100]
    return {"added": get_store().add(KNOWLEDGE, texts, [{"source": source} for _ in texts])}
