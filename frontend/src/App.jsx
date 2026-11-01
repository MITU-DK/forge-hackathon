import { useCallback, useEffect, useRef, useState } from "react";
import { TimeoutError, chatStream, getDomain, getHealth, transcribe } from "./api.js";
import Trace from "./components/Trace.jsx";
import Profile from "./components/Profile.jsx";
import Proof from "./components/Proof.jsx";
import Trust from "./components/Trust.jsx";
import Today from "./components/Today.jsx";
import { Cards, Sources, Verdict } from "./components/Cards.jsx";
import { hasTable, labels } from "./i18n.js";
import { prepareImage } from "./image.js";
import { listVoices, pickVoice, speak, stopSpeaking, ttsSupported } from "./speech.js";

const newId = () => crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`; // randomUUID is missing on plain-http phone tests
const SESSION = newId();
const store = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const wide = () => window.matchMedia?.("(min-width: 1000px)").matches ?? true;
/** Say what was actually degraded: stale data, a backup AI model, or (unknown) the generic line. */
function degradedWhy(meta, t) {
  const staleData = (meta.live_sources || []).some((s) => s.stale);
  const backupAi = (meta.trace || []).some((x) => x.agent === "Specialist" && String(x.detail || "").startsWith("fallback"));
  if (staleData) return t.degradedData;
  return backupAi ? t.degradedAi : t.degraded;
}

/** 'icar-dogr-onion-varieties.md' -> 'ICAR DOGR onion varieties': a readable name, not a file name. */
const ACRONYMS = new Set(["icar", "dogr", "msp", "pm", "pmfby", "kvk", "nhb", "fao"]);
const docTitle = (file) => String(file).replace(/^.*\//, "").replace(/\.md$/i, "").split(/[-_]+/).map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w)).join(" ");

const PHOTO_PROMPT = "Please check this crop photo."; // the API needs some text next to a photo

// Voice: Chrome/Firefox record webm, Safari/iPhone records mp4, so the recorder picks what the browser supports and the file name follows the real type.
const MAX_RECORD_MS = 60_000; // a forgotten mic must not grow past the server's 5 MB limit
const MIN_AUDIO_BYTES = 2000; // below this there is no speech worth sending
const AUDIO_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
const pickAudioType = () => AUDIO_TYPES.find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
const SPEECH_RMS = 0.015; // microphone level (0-1) that counts as sound: a quiet room stays below, speech is well above
const MIN_SPEECH_FRAMES = 3; // 100 ms each: at least 0.3 s of sound, or nothing is uploaded (the speech model answers "Thank you." to silence)
const audioExt = (type) => (/mp4|m4a|aac/.test(type) ? "m4a" : /ogg/.test(type) ? "ogg" : "webm");
// Level meter: counts 100 ms frames that were loud enough. Returns null where the browser has no analyser (then the clip is sent anyway).
function startMeter(stream) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const m = { frames: 0, stop: () => { clearInterval(timer); ctx.close().catch(() => {}); } };
    const timer = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      if (Math.sqrt(sum / buf.length) > SPEECH_RMS) m.frames += 1;
    }, 100);
    return m;
  } catch {
    return null;
  }
}

export default function App() {
  const [domain, setDomain] = useState(null);
  const [health, setHealth] = useState(null);
  const [lang, setLang] = useState(() => store.get("lang", "en"));
  const [profile, setProfile] = useState(() => store.get("profile", {}));
  const [autoRead, setAutoRead] = useState(() => store.get("autoRead", false));
  const [rate, setRate] = useState(() => store.get("rate", 0.9));
  const [msgs, setMsgs] = useState([]);
  const [trace, setTrace] = useState([]);
  const [traceOpen, setTraceOpen] = useState(wide);
  const [input, setInput] = useState("");
  const [photo, setPhoto] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState("idle"); // idle | recording | transcribing
  const [notice, setNotice] = useState("");
  const [voices, setVoices] = useState(listVoices);
  const [speakingId, setSpeakingId] = useState(null);
  const [clientId] = useState(() => { let id = store.get("clientId", ""); if (!id) { id = newId(); store.set("clientId", id); } return id; }); // stable per device: "since last time" needs it
  const recorder = useRef(null);
  const busyRef = useRef(false);
  const endRef = useRef(null);
  const fileRef = useRef(null);

  const t = labels(lang);
  const voiceOk = !ttsSupported() ? false : Boolean(pickVoice(voices, lang));
  busyRef.current = busy;

  const loadServer = useCallback(() => {
    getDomain().then(setDomain).catch(() => setDomain({ down: true, title: "KisaanMitra", tagline: "", suggested_prompts: [], languages: { en: "English" }, profile_fields: [] }));
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => { loadServer(); }, [loadServer]);
  useEffect(() => { document.documentElement.lang = lang; store.set("lang", lang); }, [lang]);
  useEffect(() => { store.set("profile", profile); }, [profile]);
  useEffect(() => { store.set("autoRead", autoRead); }, [autoRead]);
  useEffect(() => { store.set("rate", rate); }, [rate]);
  useEffect(() => { endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" }); }, [msgs, busy]); // braces: an effect must not return a value
  useEffect(() => { // browsers load voices asynchronously
    if (!ttsSupported()) return undefined;
    const on = () => setVoices(listVoices());
    speechSynthesis.addEventListener("voiceschanged", on);
    return () => speechSynthesis.removeEventListener("voiceschanged", on);
  }, []);
  useEffect(() => { if (!notice) return undefined; const id = setTimeout(() => setNotice(""), 8000); return () => clearTimeout(id); }, [notice]);
  useEffect(() => () => stopSpeaking(), []);

  const botSay = (text) => setMsgs((m) => [...m, { role: "bot", text, error: true }]);

  function listen(id, text, answerLang) {
    if (speakingId === id) { stopSpeaking(); setSpeakingId(null); return; }
    const ok = speak(text, lang, { voices, rate, answerLang, onEnd: () => setSpeakingId((c) => (c === id ? null : c)) });
    if (ok) setSpeakingId(id); else setNotice(voices.length === 0 ? t.noVoiceAny : t.noVoice);
  }

  async function send(text) {
    text = (text ?? input).trim() || (photo ? PHOTO_PROMPT : "");
    if (!text || busy) return;
    stopSpeaking();
    setSpeakingId(null);
    const image = photo;
    setInput("");
    setPhoto(null);
    setBusy(true);
    setTrace([]);
    const id = msgs.length + 1;
    setMsgs((m) => [...m, { role: "user", text: image && text === PHOTO_PROMPT ? "" : text, preview: image?.preview }]);
    // only what the API understands and the farmer filled in: place, crop and the optional sowing date
    const sendProfile = Object.fromEntries(["place", "crop", "quantity", "sowing_date"].map((k) => [k, profile[k]]).filter(([, v]) => v));
    try {
      const r = await chatStream(
        { message: text, sessionId: SESSION, language: lang, imageB64: image?.b64, profile: sendProfile },
        (s) => setTrace((tr) => [...tr, s]),
      );
      setMsgs((m) => [...m, { role: "bot", id, text: r.answer, meta: r }]);
      if (r.profile?.place && !profile.place) setProfile((p) => ({ ...p, place: r.profile.place }));
      if (autoRead && r.answer) {
        if (speak(r.answer, lang, { voices, rate, answerLang: r.language, onEnd: () => setSpeakingId((c) => (c === id ? null : c)) })) setSpeakingId(id);
        else setNotice(voices.length === 0 ? t.noVoiceAny : t.noVoice);
      }
    } catch (e) {
      const msg = e.retryAfter ? t.errWait.replace("{n}", e.retryAfter) : e instanceof TimeoutError ? t.errTimeout : t.errServer;
      setMsgs((m) => [...m, { role: "bot", id, text: msg, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  async function toggleMic() {
    if (rec === "recording") { recorder.current?.stop(); return; }
    if (rec !== "idle" || busy) return;
    if (!navigator.mediaDevices?.getUserMedia) { setNotice(window.isSecureContext ? t.errMic : t.errMicHttps); return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setNotice(window.isSecureContext ? t.errMic : t.errMicHttps); // denied, no microphone, or an http page
      return;
    }
    const type = pickAudioType();
    let mr;
    try {
      mr = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((tr) => tr.stop());
      setNotice(t.errMic);
      return;
    }
    const chunks = [];
    const meter = startMeter(stream);
    const stopTimer = setTimeout(() => mr.state === "recording" && mr.stop(), MAX_RECORD_MS);
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.onstop = async () => {
      clearTimeout(stopTimer);
      meter?.stop();
      stream.getTracks().forEach((tr) => tr.stop());
      const realType = mr.mimeType || type || "audio/webm";
      const blob = new Blob(chunks, { type: realType });
      if (blob.size < MIN_AUDIO_BYTES || (meter && meter.frames < MIN_SPEECH_FRAMES)) { setRec("idle"); setNotice(t.errHeard); return; } // nothing was said: do not upload silence
      setRec("transcribing");
      let heard = "";
      try {
        const out = await transcribe(blob, lang, audioExt(realType));
        if (out.error) setNotice(t.errTranscribe);
        heard = (out.text || "").trim();
        if (!heard && !out.error) setNotice(t.errHeard);
      } catch {
        setNotice(t.errTranscribe);
      }
      setRec("idle");
      if (!heard) return;
      if (busyRef.current) setInput(heard); // an answer is still being written: keep what was said instead of dropping it
      else send(heard);
    };
    recorder.current = mr;
    mr.start();
    setRec("recording");
  }

  async function onPhoto(e) {
    const f = e.target.files?.[0];
    e.target.value = ""; // lets the same file be picked again
    if (!f) return;
    try { setPhoto(await prepareImage(f)); } catch { setNotice(t.errServer); }
  }

  if (!domain) return <div className="app"><p className="muted pad">…</p></div>;

  const down = Boolean(domain.down);
  const prompts = domain.suggested_prompts_by_lang?.[lang] || (hasTable(lang) ? t.prompts : domain.suggested_prompts);
  const empty = msgs.length === 0;

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>{domain.title}</h1>
          {(t.tagline || domain.tagline) && <p>{t.tagline || domain.tagline}</p>}
        </div>
        <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">
          {Object.entries(domain.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </header>

      {down && (
        <div className="banner bad" role="alert">
          <b>{t.backendDown}</b> <span>{t.backendDownHint}</span>
          <button type="button" className="link" onClick={loadServer}>↻</button>
        </div>
      )}
      {!down && health && (
        <div className="status">
          <span className={`pill ${health.llm_configured ? "ok" : "warn"}`}>{health.llm_configured ? t.aiOnline : t.aiOffline}</span>
          {!health.mandi_key_configured && <span className="pill warn">{t.pricesOff}</span>}
        </div>
      )}

      <Profile profile={profile} onChange={setProfile} fields={domain.profile_fields || []} lang={lang} t={t} notify={setNotice} />

      <Today profile={profile} lang={lang} t={t} clientId={clientId} onAsk={send} busy={busy} />

      <div className="voice-row">
        <label className="switch">
          <input type="checkbox" checked={autoRead} onChange={(e) => setAutoRead(e.target.checked)} />
          <span>{t.autoRead}</span>
        </label>
        <label className="speed">
          <span>{t.speed}</span>
          <input type="range" min="0.6" max="1.3" step="0.1" value={rate} onChange={(e) => setRate(Number(e.target.value))} />
        </label>
      </div>
      {(autoRead || speakingId) && !voiceOk && <p className="notice" role="status">{voices.length === 0 ? t.noVoiceAny : t.noVoice}</p>}
      {notice && <p className="notice" role="alert">{notice}</p>}

      <div className="layout">
        <main className="feed" aria-live="polite">
          {empty && (
            <div className="suggest">
              {prompts.map((p) => <button key={p} type="button" onClick={() => send(p)} disabled={busy}>{p}</button>)}
            </div>
          )}
          {msgs.map((m, i) => (
            m.role === "user" ? (
              <div key={i} className="msg user">
                {m.preview && <img src={m.preview} alt="" />}
                {m.text && <p>{m.text}</p>}
              </div>
            ) : (
              <article key={i} className={`msg bot ${m.error ? "err" : ""}`}>
                <Verdict cards={m.meta?.cards} t={t} />
                {m.error ? <p className="answer">{m.text}</p> : <Proof text={m.text} evidence={m.meta?.evidence} lang={lang} t={t} />}
                {!m.error && m.id && (
                  <button type="button" className="listen" onClick={() => listen(m.id, m.text, m.meta?.language)} aria-pressed={speakingId === m.id}>
                    <span aria-hidden="true">{speakingId === m.id ? "■" : "🔊"}</span> {speakingId === m.id ? t.stop : t.listen}
                  </button>
                )}
                <Cards cards={m.meta?.cards} lang={lang} t={t} />
                <Sources list={m.meta?.live_sources} lang={lang} t={t} />
                {m.meta?.sources?.length > 0 && <p className="note">{t.documents}: {m.meta.sources.map(docTitle).join(" · ")}</p>}
                {m.meta?.warnings?.length > 0 && <p className="note warn">⚠ {m.meta.warnings.join(" · ")}</p>}
                {m.meta?.degraded && <p className="note warn">⚠ {degradedWhy(m.meta, t)}</p>}
              </article>
            )
          ))}
          {busy && <div className="msg bot typing" role="status">{t.thinking}</div>}
          <div ref={endRef} />
        </main>

        <aside className="trace-wrap">
          <details open={traceOpen} onToggle={(e) => setTraceOpen(e.currentTarget.open)}>
            <summary>{t.trace}</summary>
            <Trace steps={trace} t={t} busy={busy} />
          </details>
        </aside>
      </div>

      <Trust health={health} t={t} />

      {photo && (
        <div className="photo-strip">
          <img src={photo.preview} alt="" />
          <button type="button" className="link" onClick={() => setPhoto(null)}>{t.removePhoto}</button>
        </div>
      )}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <button type="button" className={`round mic ${rec === "recording" ? "on" : ""}`} onClick={toggleMic} disabled={rec === "transcribing"} aria-label={rec === "recording" ? t.micStop : t.mic}>
          <span aria-hidden="true">{rec === "recording" ? "■" : rec === "transcribing" ? "…" : "🎤"}</span>
        </button>
        <input type="text" value={input} onChange={(e) => setInput(e.target.value)} placeholder={t.ask} aria-label={t.ask} maxLength={1000} disabled={busy} />
        <button type="button" className="round" onClick={() => fileRef.current?.click()} aria-label={t.photo} disabled={busy}><span aria-hidden="true">📷</span></button>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={onPhoto} />
        <button type="submit" className="send" disabled={busy || (!input.trim() && !photo)}>{t.send}</button>
      </form>
    </div>
  );
}
