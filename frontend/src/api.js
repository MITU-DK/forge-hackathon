// All network access in one file. Components never call fetch directly.
const j = (r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)));

/** AbortController that fires after `ms`. Call done() once the request has finished. */
function deadline(ms) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return { signal: c.signal, done: () => clearTimeout(t) };
}

export class TimeoutError extends Error {}

export const getDomain = () => fetch("/api/domain").then(j);
export const getHealth = () => fetch("/api/health").then(j);

/** Streams pipeline events. onTrace(step) per agent step; resolves with the final result.
 *  Rejects with TimeoutError after `timeoutMs`, or with an error carrying `retryAfter` (seconds) on HTTP 429. */
export async function chatStream({ message, sessionId, language, imageB64, profile }, onTrace, timeoutMs = 90000) {
  const d = deadline(timeoutMs);
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, language, image_b64: imageB64 || null, profile }),
      signal: d.signal,
    });
    if (res.status === 429) throw Object.assign(new Error("rate limited"), { retryAfter: Number(res.headers.get("Retry-After")) || 60 });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let final = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const p of parts) {
        if (!p.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(p.slice(6)); } catch { continue; } // ignore a malformed event, keep streaming
        if (ev.type === "trace" && ev.step) onTrace(ev.step);
        else if (ev.type === "final") final = ev.result;
      }
    }
    if (!final) throw new Error("stream ended without a result");
    return final;
  } catch (e) {
    if (e?.name === "AbortError") throw new TimeoutError("timeout");
    throw e;
  } finally {
    d.done();
  }
}

export async function transcribe(blob, language, ext = "webm") {
  const fd = new FormData();
  fd.append("file", blob, `audio.${ext}`); // the extension tells the speech model which container this is
  if (language) fd.append("language", language);
  const d = deadline(30000);
  try {
    return await fetch("/api/voice/transcribe", { method: "POST", body: fd, signal: d.signal }).then(j);
  } finally {
    d.done();
  }
}

export const liveCheck = (place, crop) => fetch(`/api/live-check?place=${encodeURIComponent(place)}&crop=${encodeURIComponent(crop)}`).then(j);

/** "Use my location": coordinates go to /api/locate only, which returns a place name. */
export const locate = (lat, lon) => fetch(`/api/locate?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`).then(j);

/** Place autocomplete: Open-Meteo geocoding (live, keyless, CORS-enabled). Returns [] on any failure so typing still works. */
export async function placeSuggestions(query) {
  const q = (query || "").split(",")[0].trim();
  if (q.length < 2) return [];
  const d = deadline(5000);
  try {
    const r = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=6&language=en&format=json&countryCode=IN`, { signal: d.signal });
    const data = await j(r);
    return (data.results || []).map((x) => ({
      id: x.id,
      place: [x.name, x.admin1].filter(Boolean).join(", "),
      detail: [x.admin2, x.admin1].filter(Boolean).join(", "),
      name: x.name,
    }));
  } catch {
    return [];
  } finally {
    d.done();
  }
}

/** Today's plan + what changed since this device last looked (no AI involved). */
export const getPlan = (clientId, profile, lang = "en") =>
  fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: clientId, profile, lang }) }).then(j);

export const getStats = () => fetch("/api/stats").then(j);

/** The real upstream answer for a source listed under an answer ("show raw source"). */
export const getRaw = (url) => fetch(`/api/raw?url=${encodeURIComponent(url)}`).then(j);
