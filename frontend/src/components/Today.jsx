// The first thing a farmer sees: today's ranked actions (computed in code from live sources) and what changed since the last look.
// It calls /api/plan, which needs no AI, so it is fast and still works when the language model is offline.
import { useCallback, useEffect, useRef, useState } from "react";
import { getPlan } from "../api.js";
import { Cards, Sources, Verdict, fmtDay } from "./Cards.jsx";
import { actionText, changeText, planWords } from "../plan.js";
import { BCP47 } from "../i18n.js";

const REFRESH_MS = 10 * 60 * 1000; // watch mode: re-check while the page is open
const TOP = 3;
const lastKey = (clientId, profile) => `lastPlan:${clientId}:${profile.place || ""}:${profile.crop || ""}`;
const readLast = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };

export default function Today({ profile, lang, t, clientId, onAsk, busy }) {
  const w = planWords(lang);
  const [state, setState] = useState({ status: "idle" });
  const [all, setAll] = useState(false);
  const seq = useRef(0);
  const sendProfile = Object.fromEntries(["place", "crop", "quantity", "sowing_date"].map((k) => [k, profile[k]]).filter(([, v]) => v));
  const key = JSON.stringify(sendProfile);

  const load = useCallback(async () => {
    if (!sendProfile.place) { setState({ status: "needPlace" }); return; }
    const n = ++seq.current;
    setState((s) => (s.data ? { ...s, refreshing: true } : { status: "loading" }));
    try {
      const r = await getPlan(clientId, sendProfile, lang);
      if (n !== seq.current) return; // a newer request (place changed) is already running
      if (r.ok) { try { localStorage.setItem(lastKey(clientId, sendProfile), JSON.stringify(r)); } catch { /* private mode */ } }
      setState(r.ok ? { status: "ok", data: r } : { status: r.needs ? "needPlace" : r.error === "place_not_found" ? "badPlace" : "error", data: undefined });
    } catch {
      // server unreachable: show the last plan this device saw, clearly marked offline (never presented as live)
      const last = readLast(lastKey(clientId, sendProfile));
      if (n === seq.current) setState((s) => (s.data ? { ...s, refreshing: false, offline: true } : last ? { status: "ok", data: { ...last, changes: [] }, offline: true } : { status: "error" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, clientId, lang]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const id = setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const { status, data, refreshing, offline } = state;
  if (status === "needPlace") return <section className="today"><p className="note">{w.needPlace}</p></section>;
  if (status === "loading" || status === "idle") return <section className="today" role="status"><p className="note">{w.loading}</p></section>;
  if (status === "badPlace") return <section className="today"><p className="note warn">⚠ {w.badPlace}</p></section>;
  if (status === "error") return <section className="today"><p className="note warn">⚠ {w.unavailable}</p><button type="button" className="link" onClick={load}>{w.refresh}</button></section>;

  const actions = data.plan.actions || [];
  const shown = all ? actions : actions.slice(0, TOP);
  const when = new Date(data.fetched_at * 1000).toLocaleTimeString(BCP47[lang] || "en-IN", { hour: "2-digit", minute: "2-digit" });
  const ago = data.since ? Math.max(1, Math.round((data.fetched_at - data.since) / 60)) : null;
  const agoText = ago == null ? "" : ago >= 120 ? ` (${Math.round(ago / 60)} h)` : ` (${ago} min)`;
  return (
    <section className="today" aria-label={w.top}>
      {offline && <p className="note warn" role="status">⚠ {w.offline} {when}</p>}
      <Verdict cards={data.cards} t={t} />
      {data.changes.length > 0 && (
        <div className="changes" role="alert">
          <b>🔔 {w.since}{agoText}</b>
          <ul>{data.changes.map((c, i) => <li key={i} className={`sev${c.severity}`}>{changeText(c, lang, t)}</li>)}</ul>
        </div>
      )}
      {data.timeline?.changes?.length > 0 && (
        <div className="changes replan" role="alert">
          <b>🗓 {w.replanned}</b>
          <ul>{data.timeline.changes.map((c, i) => <li key={i}>{c.text}</li>)}</ul>
        </div>
      )}
      <h2>{w.top}</h2>
      {actions.length === 0 && <p className="note">{w.noActions}</p>}
      <ol className="tiles">
        {shown.map((a) => (
          <li key={a.rank} className={`tile p${a.priority}`}>
            <span className="ico" aria-hidden="true">{w.cat[a.category] || "•"}</span>
            <div>
              <b>{actionText(a, lang, t)}</b>
              <details><summary>{w.why}</summary><p className="note">{a.why} · {a.source}{lang !== "en" && <small> ({t.ruleEnglish})</small>}</p></details>
            </div>
          </li>
        ))}
      </ol>
      {actions.length > TOP && <button type="button" className="link" onClick={() => setAll(!all)}>{all ? "−" : `+${actions.length - TOP}`} {w.all}</button>}
      {data.plan.skipped?.length > 0 && <p className="note warn">⚠ {w.skipped}: {(data.plan.skipped_codes || data.plan.skipped).map((k) => w.checks[k] || k).join("; ")}</p>}
      {data.timeline?.tasks?.length > 0 && (
        <details className="timeline" open>
          <summary>{w.timeline}</summary>
          <ol>{data.timeline.tasks.map((x, i) => <li key={i}><b>{fmtDay(x.date, lang)}</b> {x.text}</li>)}</ol>
        </details>
      )}
      <p className="note">{w.updated} {when}{refreshing ? " …" : ""} <button type="button" className="link" onClick={load}>{w.refresh}</button></p>
      <Sources list={data.sources} lang={lang} t={t} />
      <button type="button" className="listen" onClick={() => onAsk(t.prompts[0])} disabled={busy}>{w.askPlan}</button>
      <div className="quick" role="group" aria-label={w.top}>
        {w.ask.map(([icon, key, q]) => <button key={key} type="button" onClick={() => onAsk(q)} disabled={busy}><span aria-hidden="true">{icon}</span> {w.askLabel[key]}</button>)}
      </div>
      <details className="more-cards"><summary>{t.markets} · {t.rain} · {t.soil}</summary><Cards cards={data.cards} lang={lang} t={t} /></details>
    </section>
  );
}
