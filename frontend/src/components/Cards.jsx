// Cards rendered from tool output (live data), the verdict band, and the source/time line under every answer.
// Unknown card types and missing fields render nothing: the API only ever adds to this contract.
import { useState } from "react";
import { getRaw } from "../api.js";
import { BCP47 } from "../i18n.js";

const loc = (lang) => BCP47[lang] || "en-IN";
const fmtTime = (iso, lang) => {
  try { return new Date(iso).toLocaleTimeString(loc(lang), { hour: "2-digit", minute: "2-digit" }); } catch { return iso; }
};
export const fmtDay = (d, lang) => {
  try { return new Date(d).toLocaleDateString(loc(lang), { weekday: "short", day: "numeric" }); } catch { return d; }
};
const fmtShort = (d, lang) => {
  try { return new Date(d).toLocaleDateString(loc(lang), { day: "numeric", month: "short" }); } catch { return d; }
};
const rs = (n) => `₹${Math.round(n).toLocaleString("en-IN")}`;
const flagText = (f, t) => t.flags[f] || String(f).replace(/_/g, " ").toLowerCase();
const fill = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k]);

const VERDICT = {
  SELL_NOW: { cls: "sell", icon: "↓", key: "sellNow" },
  HOLD: { cls: "hold", icon: "↑", key: "hold" },
  NEUTRAL: { cls: "flat", icon: "–", key: "neutral" },
  INSUFFICIENT_DATA: { cls: "flat", icon: "?", key: "noPriceData" },
};

/** The big band at the top of an answer: the price signal the code computed, never the LLM's opinion.
 *  It is a recent-trend indicator, not a price forecast (see README, "What the mandi source really is"). */
export function Verdict({ cards, t }) {
  const price = (cards || []).find((c) => c?.type === "prices" && c.signal?.signal);
  const v = price && VERDICT[price.signal.signal];
  if (!v) return null;
  const s = price.signal;
  return (
    <div className={`verdict ${v.cls}`} role="status">
      <span className="v-icon" aria-hidden="true">{v.icon}</span>
      <span className="v-word">{t[v.key]}</span>
      {s.latest != null && <span className="v-num">{rs(s.latest)} <small>{t.perQuintal}</small></span>}
      {s.change_pct != null && s.window_days != null && (
        <span className="v-sub">{s.change_pct > 0 ? "+" : ""}{s.change_pct}% {fill(t.trendWindow, { d: s.window_days, m: s.markets_compared ?? "" })}</span>
      )}
    </div>
  );
}

export function Sources({ list: all, lang, t }) {
  const list = (all || []).filter((s) => !/geocoding/i.test(s.name)); // the place lookup gives coordinates, not a number the farmer asked about
  const [open, setOpen] = useState(null); // index of the source whose raw answer is showing
  const [raw, setRaw] = useState({}); // url -> {body, fetched_at} | {error}
  if (!list?.length) return null;
  const toggle = (i, url) => {
    setOpen(open === i ? null : i);
    if (open !== i && !raw[url]) getRaw(url).then((r) => setRaw((m) => ({ ...m, [url]: r }))).catch(() => setRaw((m) => ({ ...m, [url]: { error: true } })));
  };
  const shown = open !== null ? raw[list[open]?.url] : null;
  return (
    <div className="live">
      <p>
        <b>{t.liveData}:</b>{" "}
        {list.map((s, i) => (
          <span key={i} className={s.stale ? "stale" : ""}>
            <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> {fmtTime(s.fetched_at, lang)}
            {s.stale ? ` (${t.olderData})` : ""}{" "}
            <button type="button" className="rawbtn" aria-expanded={open === i} onClick={() => toggle(i, s.url)}>{open === i ? t.hideRaw : t.showRaw}</button>
            {i < list.length - 1 ? " · " : ""}
          </span>
        ))}
      </p>
      {open !== null && (
        <div className="raw">
          {!shown && <p className="note">…</p>}
          {shown?.error && <p className="note">{t.rawGone}</p>}
          {shown?.body && (
            <>
              <p className="note">{t.rawFetched} {fmtTime(shown.fetched_at, lang)}{shown.clipped ? ` · ${t.rawClipped}` : ""}</p>
              <pre>{shown.body}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Forecast({ c, lang, t }) {
  const days = Array.isArray(c.days) ? c.days : [];
  if (!days.length) return null;
  const wetDays = new Set((c.flags || []).filter((f) => f.flag === "HEAVY_RAIN").map((f) => f.day));
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      <div className="days">
        {days.map((d) => (
          <div key={d.date} className={`dcell ${d.rain_mm >= 2.5 ? "wet" : ""} ${wetDays.has(d.date) ? "storm" : ""}`}>
            <b>{fmtDay(d.date, lang)}</b>
            <span className="temp">{Math.round(d.tmax)}°<i>{Math.round(d.tmin)}°</i></span>
            <span className="rain"><span aria-hidden="true">💧</span> {d.rain_mm} mm</span>
          </div>
        ))}
      </div>
      {c.flags?.length > 0 && (
        <ul className="flags">
          {c.flags.map((f, i) => <li key={i} className="flag">⚠ {flagText(f.flag, t)} · {fmtDay(f.day, lang)}</li>)}
        </ul>
      )}
      {c.soil_moisture != null && <p className="note">{t.soil}: {c.soil_moisture} m³/m³</p>}
      {Array.isArray(c.spray_windows) && c.spray_windows.length > 0 && (
        <div className="hours">
          <b>🧴 {t.sprayHours}</b>
          <ul className="chips">{c.spray_windows.map((w) => <li key={w.date}>{fmtDay(w.date, lang)} {w.start}–{w.end}</li>)}</ul>
          <span className="note">{t.sprayHoursNote}</span>
        </div>
      )}
      {Array.isArray(c.irrigation_plan) && c.irrigation_plan.length > 0 && (
        <div className="hours">
          <b>💧 {t.irrigPlan}</b>
          <div className="irr">
            {c.irrigation_plan.map((d) => (
              <div key={d.date} className={d.action === "SKIP" ? "skip" : ""}>
                <b>{fmtDay(d.date, lang)}</b><span>{d.action === "SKIP" ? t.skipIrr : t.checkSoil}</span><small>{d.rain_mm ?? "–"} mm</small>
              </div>
            ))}
          </div>
          <span className="note">{t.irrigPlanNote}</span>
        </div>
      )}
    </section>
  );
}

function PriceChart({ pts, lang }) {
  const W = 300, H = 110, L = 44, R = 8, T = 10, B = 22;
  const vals = pts.map((p) => p.modal);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i) => L + (pts.length > 1 ? (i / (pts.length - 1)) * (W - L - R) : (W - L - R) / 2);
  const y = (v) => T + (1 - (v - lo) / span) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="price trend">
      <line x1={L} x2={W - R} y1={y(hi)} y2={y(hi)} className="grid" />
      <line x1={L} x2={W - R} y1={y(lo)} y2={y(lo)} className="grid" />
      <text x={L - 4} y={y(hi) + 4} textAnchor="end">{rs(hi)}</text>
      <text x={L - 4} y={y(lo) + 4} textAnchor="end">{rs(lo)}</text>
      <polyline fill="none" className="line" points={pts.map((p, i) => `${x(i)},${y(p.modal)}`).join(" ")} />
      {pts.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.modal)} r="4" className="dot">
          <title>{`${fmtShort(p.date, lang)}: ${rs(p.modal)}${p.n ? ` (${p.n})` : ""}`}</title>
        </circle>
      ))}
      <text x={x(0)} y={H - 6} textAnchor="start">{fmtShort(pts[0].date, lang)}</text>
      <text x={x(pts.length - 1)} y={H - 6} textAnchor="end">{fmtShort(pts[pts.length - 1].date, lang)}</text>
    </svg>
  );
}

function Prices({ c, lang, t }) {
  const pts = (Array.isArray(c.series) ? c.series : []).filter((p) => Number.isFinite(p?.modal));
  const markets = Array.isArray(c.markets) ? c.markets : [];
  if (!pts.length && !markets.length) return null;
  const last = pts[pts.length - 1];
  const pos = c.position;
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      {last && <p className="big-price">{rs(last.modal)} <small>{t.perQuintal} · {fmtShort(last.date, lang)}</small></p>}
      {pts.length > 1 && <PriceChart pts={pts} lang={lang} />}
      {markets.length > 0 && (
        <table>
          <caption>{t.markets}</caption>
          <tbody>
            {markets.map((m, i) => (
              <tr key={i}><th scope="row">{m.market}</th><td>{m.district}</td><td className="num">{Number.isFinite(m.modal) ? rs(m.modal) : "–"}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {pos && (
        <p className="note">
          {t.stateMedian} {rs(pos.state_median)} · {pos.markets} {t.marketsWord} · {t.bestWord} {rs(pos.best_price)} ({pos.best_market})
          {pos.local_median != null ? ` · ${t.yourDistrict} ${rs(pos.local_median)} (${pos.pct_vs_state_median}% ${t.vsState})` : ""}
        </p>
      )}
    </section>
  );
}

function RainHistory({ c, t }) {
  const pts = Array.isArray(c.daily) ? c.daily : [];
  const hi = Math.max(1, ...pts.map((p) => p.rain_mm ?? 0));
  const today = new Date().toISOString().slice(0, 10);
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      <div className="stats">
        <div><b>{c.past_7d_mm} mm</b><span>{t.past7}</span></div>
        <div><b>{c.past_30d_mm} mm</b><span>{t.past30}</span></div>
        <div><b>{c.forecast_7d_mm} mm</b><span>{t.next7}</span></div>
      </div>
      {pts.length > 0 && (
        <div className="rainbars" role="img" aria-label={`${t.rain}: ${t.past30}, ${t.next7}`}>
          {pts.map((p) => <i key={p.date} className={p.date >= today ? "next" : ""} style={{ height: `${Math.max(2, ((p.rain_mm ?? 0) / hi) * 100)}%` }} title={`${p.date}: ${p.rain_mm ?? "–"} mm`} />)}
        </div>
      )}
      {c.alerts?.length > 0 && <ul className="flags">{c.alerts.map((a) => <li key={a} className="flag">⚠ {t.alerts[a] || String(a).replace(/_/g, " ").toLowerCase()}</li>)}</ul>}
    </section>
  );
}

function Diagnosis({ c, t }) {
  // fixed wording comes from the language tables; the symptoms and causes are written by the vision model in the farmer's language
  const list = (title, items) => items?.length > 0 && <><h4>{title}</h4><ul className="plain">{items.map((s, i) => <li key={i}>{s}</li>)}</ul></>;
  return (
    <section className="card" aria-label={t.diagTitle}>
      <h3>{t.diagTitle}</h3>
      {c.severity && <span className={`sev ${c.severity}`}>{t.severity}: {t.sevWord[c.severity] || c.severity}</span>}
      {list(t.symptoms, c.symptoms)}
      {list(t.causes, c.possible_causes)}
      {list(t.nextSteps, t.diagRecs)}
      <p className="note">{t.diagDisclaimer}</p>
    </section>
  );
}

function CropFit({ c, t }) {
  const rows = Array.isArray(c.crops) ? c.crops : [];
  if (!rows.length) return null;
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      <div className="scroll-x">
        <table className="wide">
          <thead><tr><th>{t.colCrop}</th><th>{t.colFit}</th><th>{t.colSow}</th><th>{t.colHarvest}</th><th>{t.colWater}</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.crop}><th scope="row">{r.crop}</th><td>{r.fit}</td><td>{r.sow_on}</td><td>{r.harvest_from} – {r.harvest_to}</td><td>{t.water[r.rain_label] || String(r.rain_label || "").replace(/_/g, " ").toLowerCase()}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {c.note && <p className="note">{c.note}</p>}
    </section>
  );
}

function Soil({ c, t }) {
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      <div className="stats">
        <div><b>{c.ph}</b><span>{t.ph}</span></div>
        <div><b>{c.clay_pct}%</b><span>{t.clay}</span></div>
        <div><b>{c.sand_pct}%</b><span>{t.sand}</span></div>
        <div><b>{c.organic_carbon_g_kg} g/kg</b><span>{t.organicCarbon}</span></div>
      </div>
      <p className="note">{c.texture_class}. {c.note}</p>
    </section>
  );
}

/** Today's farm plan: the ranked actions the code computed. Order is urgency, not profit; each row shows the rule and source behind it. */
function FarmPlan({ c }) {
  const actions = Array.isArray(c.actions) ? c.actions : [];
  return (
    <section className="card plan" aria-label={c.title}>
      <h3>{c.title}</h3>
      {actions.length === 0 && <p className="note">No action was triggered by the data that could be read.</p>}
      <ol className="plan-list">
        {actions.map((a) => (
          <li key={a.rank} className={`plan-item p${a.priority}`}>
            <b>{a.title}</b>
            <span className="note">{a.why} · {a.source}</span>
          </li>
        ))}
      </ol>
      {Array.isArray(c.skipped) && c.skipped.length > 0 && <p className="note warn">Not checked: {c.skipped.join("; ")}</p>}
    </section>
  );
}

/** Best markets from the farm: price, straight-line distance, price gap to the nearest listed market. No transport cost is shown because none is known. */
function BestMarket({ c, t }) {
  const rows = Array.isArray(c.markets) ? c.markets : [];
  if (!rows.length) return null;
  const top = Math.max(...rows.map((r) => r.modal));
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      {c.best_nearby && <p className="note"><b>{t.bestNearby}</b> {c.best_nearby.market} · {rs(c.best_nearby.modal)} · {c.best_nearby.located_by === "district" ? "~" : ""}{c.best_nearby.km} km</p>}
      {c.lot && (
        <p className="note lot"><b>{fill(t.lotLine, { q: c.lot.quantity ?? c.lot.quintals, p: rs(c.lot.price), total: rs(c.lot.total), m: c.lot.market })}</b>
          {c.lot.gap_total > 0 && <> {fill(t.lotGap, { g: rs(c.lot.gap_per_quintal), n: c.lot.nearest, gt: rs(c.lot.gap_total) })}</>}</p>
      )}
      <ul className="bm">
        {rows.map((r) => (
          <li key={r.market}>
            <span className="bm-name"><b>{r.market}</b><small>{r.district}</small></span>
            <span className="bm-bar" aria-hidden="true"><i style={{ width: `${Math.max(8, (r.modal / top) * 100)}%` }} /></span>
            <span className="bm-num"><b>{rs(r.modal)}</b><small>{r.located_by === "district" ? "~" : ""}{r.km} km{r.vs_nearest ? ` · ${r.vs_nearest > 0 ? "+" : "−"}${rs(Math.abs(r.vs_nearest))}` : ""}</small></span>
          </li>
        ))}
      </ul>
      <p className="note">{t.bestMarketNote}</p>
    </section>
  );
}

const DAY_MS = 86400000;
const ms = (iso) => new Date(`${iso}T00:00:00Z`).getTime();

/** Sowing and harvest calendar for the farmer's crop: growing time in light green, the harvest range in dark green, a line for today. */
function CropCalendar({ c, lang, t }) {
  const spans = Array.isArray(c.spans) ? c.spans : [];
  const rows = [...(c.sown ? [{ kind: "sown", label: t.yourSowing, sow_on: c.sown.sowing, harvest_from: c.sown.harvest_from, harvest_to: c.sown.harvest_to }] : []), ...spans];
  if (!rows.length) return null;
  const lo = Math.min(ms(c.today), ...rows.map((r) => ms(r.sow_on))) - 20 * DAY_MS;
  const hi = Math.max(ms(c.today), ...rows.map((r) => ms(r.harvest_to))) + 20 * DAY_MS;
  const pos = (d) => `${((d - lo) / (hi - lo)) * 100}%`;
  const width = (a, b) => `${((b - a) / (hi - lo)) * 100}%`;
  const months = [];
  for (let d = new Date(lo); d.getTime() < hi; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
    if (d.getUTCDate() === 1 || !months.length) months.push(d.getTime());
  }
  const step = months.length > 14 ? 2 : 1;
  const mname = (d) => new Date(d).toLocaleDateString(loc(lang), { month: "short", timeZone: "UTC" });
  const sub = (r) => `${fmtShort(r.sow_on, lang)} → ${fmtShort(r.harvest_from, lang)} – ${fmtShort(r.harvest_to, lang)}`;
  return (
    <section className="card" aria-label={c.title}>
      <h3>{c.title}</h3>
      <div className="cal">
        <div className="cal-axis">{months.filter((_, i) => i % step === 0).map((m) => <span key={m} style={{ left: pos(m) }}>{mname(m)}</span>)}</div>
        {rows.map((r, i) => (
          <div key={i} className="cal-row">
            <div className="cal-label"><b>{r.kind === "sown" ? r.label : r.kind === "now" ? t.sowNow : t.sowWindow}</b> <small>{sub(r)}</small></div>
            <div className="cal-track">
              <i className="grow" style={{ left: pos(ms(r.sow_on)), width: width(ms(r.sow_on), ms(r.harvest_from)) }} />
              <i className={`harvest ${r.kind === "sown" ? "own" : ""}`} style={{ left: pos(ms(r.harvest_from)), width: width(ms(r.harvest_from), ms(r.harvest_to)) }} />
              <u className="today-line" style={{ left: pos(ms(c.today)) }} title={t.todayWord} />
            </div>
          </div>
        ))}
      </div>
      <p className="legend"><i className="grow" /> {t.growing} <i className="harvest" /> {t.harvestWord} <u /> {t.todayWord}</p>
      {c.sown && <p className="note">{t.stageText[c.sown.status] || c.sown.status} ({c.sown.days_since_sowing} {t.daysSince})</p>}
      <p className="note">{c.note}</p>
    </section>
  );
}

const CARD_TYPES = { crop_calendar: CropCalendar, farm_plan: FarmPlan, best_market: BestMarket, forecast: Forecast, prices: Prices, rainfall_history: RainHistory, diagnosis: Diagnosis, crop_fit: CropFit, soil: Soil };

export function Cards({ cards, lang, t }) {
  if (!Array.isArray(cards) || !cards.length) return null;
  return (
    <div className="cards">
      {cards.map((c, i) => {
        const Card = CARD_TYPES[c?.type];
        return Card ? <Card key={i} c={c} lang={lang} t={t} /> : null;
      })}
    </div>
  );
}
