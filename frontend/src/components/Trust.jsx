// "Why trust this": what the agent does with every answer and live counters from the running server (no hardcoded results).
import { useEffect, useState } from "react";
import { getStats } from "../api.js";

export default function Trust({ health, t }) {
  const [stats, setStats] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (open) getStats().then(setStats).catch(() => setStats(null)); }, [open]);
  const row = (label, value) => <div><b>{value}</b><span>{label}</span></div>;
  return (
    <details className="trust" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>🛡 {t.trustTitle}</summary>
      <ul className="plain">{t.trustPoints.map((p) => <li key={p}>{p}</li>)}</ul>
      <p className="note">{t.trustSources}</p>
      <p className="note">
        <a href="https://open-meteo.com" target="_blank" rel="noreferrer">Open-Meteo</a> · <a href="https://data.gov.in" target="_blank" rel="noreferrer">data.gov.in Agmarknet</a> · <a href="https://soilgrids.org" target="_blank" rel="noreferrer">ISRIC SoilGrids</a> · FAO EcoCrop · PIB · ICAR
      </p>
      {stats && (
        <div className="stats" aria-label={t.trustLive}>
          {row(t.sAnswers, stats.total_answers)}
          {row(t.sRewrites, stats.guardrail_rejections)}
          {row(t.sFallbacks, stats.guardrail_fallbacks)}
          {row(t.sToolFail, stats.tool_failures)}
          {row(t.sLatency, `${(stats.avg_latency_ms / 1000).toFixed(1)} s`)}
        </div>
      )}
      {health && <p className="note">{t.trustKnow}: {health.knowledge_chunks} · {t.trustAi}: {health.llm_configured ? "✓" : "✕"}{health.fallback_configured ? " + backup ✓" : ""}</p>}
      <p className="note">{t.trustEval}</p>
    </details>
  );
}
