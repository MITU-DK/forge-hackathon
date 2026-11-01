// The answer text with every number that appears in the live data / documents made tappable.
// Tapping shows the data line it came from, the source and when it was fetched: the guardrail already checks numbers against these same lines.
import { useMemo, useState } from "react";
import { BCP47 } from "../i18n.js";

const DASH = "[-\u2010\u2011\u2012\u2013]"; // models often write dates with a non-breaking hyphen
const TOKEN = new RegExp(`\\d{1,2}:\\d{2}|\\d{4}${DASH}\\d{2}${DASH}\\d{2}|\\d[\\d,]*(?:\\.\\d+)?`, "g");
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const plain = (s) => s.replace(/(\d),(?=\d{3})/g, "$1").replace(/[\u2010-\u2013]/g, "-");

function matchesFor(token, evidence) {
  const clean = plain(token);
  if (clean.replace(/\D/g, "").length < 2) return []; // single digits match everything
  const re = new RegExp(`(?<![\\d.])${escape(clean)}(?!\\d|\\.\\d)`);
  return evidence.filter((e) => re.test(plain(e.text))).slice(0, 3);
}

export default function Proof({ text, evidence, lang, t }) {
  const [open, setOpen] = useState(null);
  const parts = useMemo(() => {
    const out = [];
    let last = 0;
    for (const m of text.matchAll(TOKEN)) {
      out.push({ text: text.slice(last, m.index) });
      out.push({ text: m[0], hits: matchesFor(m[0], evidence || []), at: m.index });
      last = m.index + m[0].length;
    }
    out.push({ text: text.slice(last) });
    return out;
  }, [text, evidence]);
  const fmt = (iso) => { try { return new Date(iso).toLocaleString(BCP47[lang] || "en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return iso; } };
  const chosen = parts.find((p) => p.at === open);

  return (
    <>
      <p className="answer">
        {parts.map((p, i) => (p.hits?.length
          ? <button key={i} type="button" className={`num-proof ${open === p.at ? "on" : ""}`} aria-expanded={open === p.at} onClick={() => setOpen(open === p.at ? null : p.at)}>{p.text}</button>
          : <span key={i}>{p.text}</span>))}
      </p>
      {chosen && (
        <div className="proof" role="region" aria-label={t.proofTitle}>
          <b>{t.proofTitle}: {chosen.text}</b>
          {chosen.hits.map((h, i) => (
            <div key={i} className="proof-row">
              <code>{h.text}</code>
              {h.sources.map((s, j) => (
                <span key={j} className={`note ${s.stale ? "warn" : ""}`}>
                  {h.kind === "document" ? t.documents : t.liveData}: {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}
                  {s.fetched_at ? ` · ${fmt(s.fetched_at)}` : ""}{s.stale ? ` (${t.olderData})` : ""}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
