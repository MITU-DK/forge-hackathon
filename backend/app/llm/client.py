"""One LLM client for every provider that speaks the OpenAI chat format.

Degradation ladder (the demo must NEVER crash on stage):
    primary provider -> fallback provider -> offline answer (caller-supplied)

`LLMResult.degraded` tells the UI which rung answered so it can show a badge.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

import base64

import httpx

from app.core.config import Settings, get_settings

log = logging.getLogger("llm")

MIN_REASONING_TOKENS = 400
_DROPPED = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError)  # not timeouts: retrying those would double a 25 s wait


@dataclass
class LLMResult:
    text: str
    provider: str  # "primary" | "fallback" | "offline"
    model: str = ""
    ms: int = 0
    degraded: bool = False
    error: str = ""
    raw: dict = field(default_factory=dict)


DEFAULT_OFFLINE_TEXT = "I'm having trouble reaching my AI service right now. Please try again in a moment."
REASONING_MODEL_PREFIX = "openai/gpt-oss"  # reasoning models: reasoning tokens count against max_tokens
DROPPED_RETRY_DELAY_S = 0.5
MAX_ATTEMPTS = 3  # per provider call: the first try plus up to two retries
BACKOFF_S = 1.0  # waits 1 s, then 2 s, when the provider gives no Retry-After

# Circuit breaker per provider+model. A model the key may not use (403/404) or an endpoint that is unreachable would otherwise cost every
# question a failed call (or a full timeout) before the next rung answers. A rate limit (429) and a malformed reply are NOT outages:
# they never open the breaker. Once the window has passed, one question tries the provider again.
BREAKER_PERMANENT_S = 300.0  # 401/403/404: a configuration problem, it will not fix itself within minutes
BREAKER_OUTAGE_S = 15.0  # connection failures, timeouts, 5xx: short, so one network blip does not switch the AI off for long
_PERMANENT_STATUS = {401, 403, 404}


class LLMClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.s = settings or get_settings()
        self._http = httpx.Client(timeout=self.s.llm_timeout_s, transport=transport)
        self._open_until: dict[tuple[str, str], float] = {}  # (provider, model) -> time until which it is skipped

    # ------------------------------------------------------------------ chat
    def _providers(self, fast: bool, primary_only: bool) -> list[tuple[str, str, str, str]]:
        """The ladder to try, in order: (name, base url, api key, model)."""
        s = self.s
        providers = []
        if s.llm_api_key:
            providers.append(("primary", s.llm_base_url, s.llm_api_key, s.llm_fast_model if fast else s.llm_model))
            if not fast and s.llm_fast_model != s.llm_model:
                # Groq limits tokens per model: when the big model's daily quota is spent, the small one
                # (own quota, ~1 s) still answers, instead of the 20-35 s free fallback.
                providers.append(("reserve", s.llm_base_url, s.llm_api_key, s.llm_fast_model))
        if s.llm_api_key:
            # same provider, own daily quota each: the next key takes over silently when the one before is spent (not "degraded": same model)
            for rung, key in (("primary2", s.llm_api_key2), ("primary3", s.llm_api_key3)):
                if key:
                    providers.append((rung, s.llm_base_url, key, s.llm_fast_model if fast else s.llm_model))
        if not primary_only:
            # Each configured model is its own rung, so when one is rate limited or down the next is tried (breaker is per model).
            for base, key, models in ((s.fallback_base_url, s.fallback_api_key, s.fallback_model), (s.fallback2_base_url, s.fallback2_api_key, s.fallback2_model)):
                if base and key:
                    providers += [("fallback", base, key, m.strip()) for m in models.split(",") if m.strip()]
        return providers

    def _breaker_window(self, error: Exception) -> float:
        """Seconds to skip a provider after this failure; 0 when the failure says nothing about the provider's health."""
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status in _PERMANENT_STATUS:
                return BREAKER_PERMANENT_S
            return BREAKER_OUTAGE_S if status >= 500 else 0.0
        return BREAKER_OUTAGE_S if isinstance(error, (httpx.TransportError, httpx.TimeoutException)) else 0.0

    def _usable(self, providers: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
        now = time.time()
        return [p for p in providers if self._open_until.get((p[0], p[3]), 0.0) <= now]

    def _body(self, name: str, model: str, messages: list[dict], temperature: float | None, max_tokens: int, json_mode: bool) -> dict:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.s.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        is_reasoning = model.startswith(REASONING_MODEL_PREFIX)
        if is_reasoning:
            body["reasoning_effort"] = "low"
        if name == "fallback" or is_reasoning:
            # the free fallback reasons too: at the router's 80 tokens it cut JSON off, losing Marathi detection
            body["max_tokens"] = max(max_tokens, MIN_REASONING_TOKENS)
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, base: str, key: str, body: dict, name: str = "primary") -> httpx.Response:
        """POST with up to MAX_ATTEMPTS tries. Retried: a connection the provider dropped, a 5xx, and a 429 that either carries a short
        Retry-After or comes from the free fallback (busy shared capacity). A primary 429 with a long wait is a used-up quota: no retry."""
        url = f"{base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                response = self._http.post(url, headers=headers, json=body)
            except _DROPPED:  # a dropped connection is not an outage
                if last:
                    raise
                time.sleep(DROPPED_RETRY_DELAY_S * attempt)
                continue
            if last or not _worth_retrying(response, name):
                return response
            time.sleep(_short_retry_after(response) or BACKOFF_S * attempt)
        return response  # unreachable: the last attempt always returns or raises


    def _ask(self, name: str, base: str, key: str, model: str, body: dict) -> LLMResult:
        """One provider, one question. Raises on any failure so the caller can move down the ladder."""
        t0 = time.time()
        for attempt in (1, 2):  # a free reasoning model sometimes spends its whole budget thinking and returns blank text: ask once more
            response = self._post(base, key, body, name)
            response.raise_for_status()
            data = response.json()
            text = strip_think(data["choices"][0]["message"]["content"] or "")
            if text.strip():
                break
            if attempt == 2:
                raise ValueError("the provider returned an empty answer")  # never hand a blank answer on as a success
        return LLMResult(
            text=text,
            provider=name,
            model=model,
            ms=int((time.time() - t0) * 1000),
            degraded=name not in ("primary", "primary2", "primary3", "reserve"),  # reserve is the same provider; the trace names the model
            raw=data,
        )

    def chat(
        self,
        messages: list[dict],
        *,
        fast: bool = False,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 700,
        offline: str = DEFAULT_OFFLINE_TEXT,
        primary_only: bool = False,  # optional steps: a safe default beats waiting on the slow fallback
    ) -> LLMResult:
        providers = self._providers(fast, primary_only)
        last_err = "no provider configured (set LLM_API_KEY)"
        usable = self._usable(providers)
        if providers and not usable:
            last_err = "every provider is temporarily skipped after repeated failures"
        for name, base, key, model in usable:
            try:
                body = self._body(name, model, messages, temperature, max_tokens, json_mode)
                return self._ask(name, base, key, model, body)
            except Exception as e:  # network, 429, 5xx, bad JSON...
                last_err = f"{name}: {type(e).__name__}: {e}"
                window = self._breaker_window(e)
                if window:
                    self._open_until[(name, model)] = time.time() + window
                log.warning("LLM %s failed: %s%s", name, last_err, f" (skipped for {int(window)}s)" if window else "")
        return LLMResult(text=offline, provider="offline", degraded=True, error=last_err)

    def chat_json(self, messages: list[dict], **kw) -> tuple[dict | None, LLMResult]:
        res = self.chat(messages, json_mode=True, **kw)
        return parse_json(res.text), res

    # ---------------------------------------------------------------- vision
    def vision(self, image_b64: str, question: str, mime: str = "image/jpeg") -> LLMResult:
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            }
        ]
        s = self.s
        # Each account can have a different set of models enabled (one key had the vision model blocked), so every configured key is tried,
        # then Gemini, which reads images through the same OpenAI-style request.
        tries = [("primary", s.llm_base_url, k, s.llm_vision_model) for k in (s.llm_api_key, s.llm_api_key2, s.llm_api_key3) if k]
        if s.fallback2_api_key and s.fallback2_base_url:
            tries.append(("fallback", s.fallback2_base_url, s.fallback2_api_key, (s.fallback2_model or "").split(",")[0].strip()))
        if not tries:
            return LLMResult(text="", provider="offline", degraded=True, error="no LLM_API_KEY")
        error = ""
        for name, base, key, model in tries:
            t0 = time.time()
            try:
                r = self._http.post(f"{base.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                    json={"model": model, "messages": msgs, "max_tokens": 500})
                r.raise_for_status()
                text = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if text:
                    return LLMResult(text=text, provider=name, model=model, ms=int((time.time() - t0) * 1000), degraded=name != "primary")
                error = "the provider returned an empty answer"
            except Exception as e:
                error = f"{type(e).__name__}"
                log.warning("vision via %s failed: %s", name, error)
        return LLMResult(text="", provider="offline", degraded=True, error=error)

    # ----------------------------------------------------------------- audio
    def _whisper(self, audio: bytes, filename: str = "audio.webm", language: str | None = None, prompt: str | None = None, key: str | None = None) -> str:
        """Speech -> text with Whisper. verbose_json gives per-segment silence scores: on silence or room noise Whisper
        otherwise invents phrases such as "Thank you.", which would be sent on as the farmer's question."""
        s = self.s
        if not s.llm_api_key:
            return ""
        data = {"model": s.stt_model, "response_format": "verbose_json", "temperature": "0"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt  # vocabulary hint (crop and market words in the right script); it does not steer the content
        for attempt in (1, 2):
            try:
                r = self._http.post(
                    f"{s.llm_base_url.rstrip('/')}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key or s.llm_api_key}"},
                    data=data,
                    files={"file": (filename, audio)},
                )
                break
            except _DROPPED:  # a connection the provider dropped: one retry, as for chat
                if attempt == 2:
                    raise
                time.sleep(0.5)
        r.raise_for_status()
        return speech_text(r.json())

    def transcribe(self, audio: bytes, filename: str = "audio.webm", language: str | None = None, prompt: str | None = None) -> str:
        """Whisper on the primary provider; when that is blocked or down, the same audio goes to Gemini (FALLBACK2) so voice never depends on one key."""
        keys = [k for k in (self.s.llm_api_key, self.s.llm_api_key2, self.s.llm_api_key3) if k]
        first: Exception | None = None
        for key in keys:  # each key may sit in a different account: one can have the speech model blocked while the other allows it
            try:
                return self._whisper(audio, filename, language, prompt, key)
            except Exception as e:
                first = first or e
                log.warning("whisper failed (%s)", type(e).__name__)
        if not self.s.fallback2_api_key:
            if first:
                raise first
            return ""
        log.warning("trying Gemini speech-to-text")
        return self._gemini_transcribe(audio, filename, language)

    def _gemini_transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        """Gemini's native audio input (the OpenAI-compatible route only takes wav/mp3). Returns "" for silence; raises on failure."""
        s = self.s
        model = (s.fallback2_model or "gemini-3.1-flash-lite").split(",")[0].strip()
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = {"ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "mp3": "audio/mp3", "wav": "audio/wav", "m4a": "audio/aac", "mp4": "audio/aac"}.get(ext, "audio/ogg")
        script = {"hi": "Hindi in Devanagari script", "mr": "Marathi in Devanagari script", "en": "English"}.get(language or "", "the language spoken (Devanagari script for Hindi or Marathi)")
        ask = (f"Transcribe this voice message exactly as spoken, in {script}. Output only the words spoken, with no commentary, no translation and no quotation marks. "
               f"If the audio contains no clear human speech (silence, a tone, music or noise), output exactly {NO_SPEECH_MARK} and nothing else. Never guess or invent words.")
        body = {"contents": [{"parts": [{"text": ask}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}}]}],
                "generationConfig": {"temperature": 0}}
        r = self._http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": s.fallback2_api_key}, json=body)
        r.raise_for_status()
        parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        text = " ".join(str(p.get("text", "")).strip() for p in parts).strip().strip('"')
        return "" if NO_SPEECH_MARK in text or _is_hallucination(text) else text


# --------------------------------------------------------------------- utils
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

# Whisper's own silence test: a segment is not speech when it is probably silent AND the model is unsure of its own words.
# Groq's whisper-large-v3-turbo reports no_speech_prob = 0 for everything (checked 31 Oct 2026: pure silence came back as
# "Thank you." with probability 0 and avg_logprob -0.29), so that test cannot be relied on there. The phrase list below is
# what actually catches it; the browser also skips the upload when the microphone level never rose.
NO_SPEECH_P, LOW_LOGPROB = 0.6, -1.0
MAX_COMPRESSION = 2.4  # Whisper's own decoding-failure test: text that compresses this well is looping on one phrase
# What Whisper writes for silence or room noise (its training data was subtitled video). Dropping them costs nothing: none is a farm question.
HALLUCINATIONS = {"thank you", "thanks", "thank you very much", "thanks for watching", "thank you for watching", "please subscribe", "bye", "goodbye", "you",
                  "okay", "ok", "oh", "uh", "um", "subtitles by the amara.org community", "धन्यवाद", "शुक्रिया", "देखने के लिए धन्यवाद", "आभारी आहे"}


def _plain(text: str) -> str:
    """Lower-cased text without punctuation or symbols. By Unicode category, not a regex: Python's \\w drops Devanagari vowel signs and virama."""
    return " ".join("".join(c for c in text.lower() if unicodedata.category(c)[0] not in "PS").split())


def _is_hallucination(text: str) -> bool:
    plain = _plain(text)
    return not any(c.isalnum() for c in text) or plain in HALLUCINATIONS  # no letters or digits at all, or a stock phrase


def _is_speech(segment: dict) -> bool:
    silent = segment.get("no_speech_prob", 0) > NO_SPEECH_P and segment.get("avg_logprob", 0) < LOW_LOGPROB
    looping = segment.get("compression_ratio", 0) > MAX_COMPRESSION
    return not silent and not looping


def speech_text(result: dict) -> str:
    """Text of the segments that are speech. Falls back to the plain text when the provider sent no segment scores."""
    if not isinstance(result, dict):
        return ""
    segments = result.get("segments")
    if segments:
        kept = [str(g.get("text", "")).strip() for g in segments if _is_speech(g)]
        text = " ".join(t for t in kept if t).strip()
    else:
        text = str(result.get("text", "")).strip()
    return "" if _is_hallucination(text) else text


NO_SPEECH_MARK = "[NO_SPEECH]"
RETRY_AFTER_MAX_S = 4.0


def _worth_retrying(r: httpx.Response, name: str) -> bool:
    if r.status_code in (500, 502, 503, 504):
        return True
    if r.status_code == 429:  # a short wait (burst limit) is worth it; a long one means the day's quota is gone
        return _short_retry_after(r) is not None or name.startswith("fallback")
    return False


def _short_retry_after(r) -> float | None:
    """Seconds to wait if the 429 says the limit clears quickly; None if it is long or unspecified."""
    try:
        v = float(r.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= RETRY_AFTER_MAX_S else None


def strip_think(text: str) -> str:
    """Reasoning models (qwen3, deepseek-r1) leak <think> blocks. Remove them."""
    return _THINK.sub("", text).strip()


_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def _json_object(text: str) -> dict | None:
    try:
        out = json.loads(text)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def parse_json(text: str) -> dict | None:
    """Tolerant JSON parse: handles ```json fences and chatter around the object."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text.strip()).strip()
    whole = _json_object(cleaned)
    if whole is not None:
        return whole
    try:
        json.loads(cleaned)
        return None  # valid JSON that is not an object (a list, a number): no second guess
    except ValueError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    return _json_object(m.group(0)) if m else None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
