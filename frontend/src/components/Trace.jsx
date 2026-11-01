// The live "how the agents decided" panel: the single best thing to have on screen in a demo.
// Steps appear as the backend streams them; failed or rejected steps are red AND carry a cross, so colour is not the only cue.
export default function Trace({ steps, t, busy }) {
  if (!steps.length) return <p className="muted">{busy ? t.thinking : t.traceEmpty}</p>;
  const tools = steps.filter((s) => s.agent === "Tool" && s.ok !== false).length;
  const failed = steps.filter((s) => s.ok === false).length;
  const rewrites = steps.filter((s) => s.agent === "Guardrail" && /REJECT|rewrite/i.test(`${s.action} ${s.detail}`)).length;
  const total = steps.reduce((n, s) => n + (s.ms || 0), 0);
  return (
    <>
    {!busy && (
      <p className="trace-sum" aria-label="summary">
        <span>⚙ {steps.length} {t.stepsWord}</span> <span>🔌 {tools} {t.toolsWord}</span>
        <span className={rewrites ? "hit" : ""}>🛡 {rewrites ? `${rewrites} ${t.rewrites}` : t.guardOk}</span>
        {failed > 0 && <span className="hit">✕ {failed}</span>} <span>⏱ {(total / 1000).toFixed(1)} s</span>
      </p>
    )}
    <ol className="trace">
      {steps.map((s, i) => {
        const guard = s.agent === "Guardrail" && /REJECT|fallback/i.test(`${s.action}`);
        return (
          <li key={i} className={`${s.ok === false ? "bad" : ""} ${guard ? "guard" : ""}`}>
            <span className="mark" aria-hidden="true">{s.ok === false ? "✕" : "✓"}</span>
            <div className="body">
              <div className="head">
                <b>{s.agent}</b> <span>{s.action}</span>
                {s.ms > 0 && <em>{s.ms} ms</em>}
              </div>
              {s.detail && <div className="detail">{s.detail}</div>}
            </div>
          </li>
        );
      })}
      {busy && <li className="pending"><span className="mark" aria-hidden="true">…</span><div className="body">{t.thinking}</div></li>}
    </ol>
    </>
  );
}
