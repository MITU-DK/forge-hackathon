// Place + crop in as few taps as possible: type a town (live suggestions) and tap a crop.
import { useEffect, useRef, useState } from "react";
import { locate, placeSuggestions } from "../api.js";
import { cropIcon, cropLabel } from "../i18n.js";

export default function Profile({ profile, onChange, fields, lang, t, notify }) {
  const cropField = fields.find((f) => f.key === "crop");
  const allCrops = cropField?.options || [];
  // Common crops first so a farmer sees their crop without scrolling; the full list is one tap away.
  const COMMON = ["Onion", "Tomato", "Potato", "Wheat", "Rice", "Soybean", "Cotton", "Maize"];
  const [showAll, setShowAll] = useState(false);
  const common = COMMON.filter((c) => allCrops.includes(c));
  const crops = showAll || !common.length ? allCrops : [...common, ...(profile.crop && !common.includes(profile.crop) ? [profile.crop] : [])];
  const complete = Boolean(profile.place && profile.crop);
  const [open, setOpen] = useState(!complete);
  const [text, setText] = useState(profile.place || "");
  const [sugg, setSugg] = useState([]);
  const [searched, setSearched] = useState(false);
  const [locating, setLocating] = useState(false);
  const picked = useRef(profile.place || "");
  const extra = fields.filter((f) => f.key !== "place" && f.key !== "crop"); // e.g. the optional sowing date

  // Debounced live place search; typing always works even if the suggestion API is unreachable.
  useEffect(() => {
    if (text === picked.current) { setSugg([]); setSearched(false); return undefined; }
    const id = setTimeout(async () => {
      const r = await placeSuggestions(text);
      setSugg(r);
      setSearched(text.trim().length >= 2);
    }, 300);
    return () => clearTimeout(id);
  }, [text]);

  function choosePlace(place) {
    picked.current = place;
    setText(place);
    setSugg([]);
    onChange({ ...profile, place });
  }

  function typePlace(v) {
    setText(v);
    onChange({ ...profile, place: v });
  }

  // "Use my location": coordinates go to /api/locate only, which hands back a place name; nothing else keeps them.
  function useLocation() {
    if (!navigator.geolocation) { notify?.(t.errLoc); return; }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const out = await locate(pos.coords.latitude, pos.coords.longitude).catch(() => ({}));
        setLocating(false);
        if (out.place) choosePlace(out.place); else notify?.(out.error || t.errLoc);
      },
      () => { setLocating(false); notify?.(t.errLoc); }, // denied, unavailable, or an http page
      { timeout: 15000, maximumAge: 600000 },
    );
  }

  if (!open && complete) {
    return (
      <div className="profile-chip">
        <span><span aria-hidden="true">📍</span> {profile.place}</span>
        <span><span aria-hidden="true">{cropIcon(profile.crop)}</span> {cropLabel(lang, profile.crop)}</span>
        <button type="button" className="link" onClick={() => setOpen(true)}>{t.change}</button>
      </div>
    );
  }

  return (
    <section className="profile" aria-label={`${t.place}, ${t.crop}`}>
      {!complete && <p className="hint">{t.setProfile}</p>}
      <label htmlFor="place-input">{t.place}</label>
      <div className="place-box">
        <input id="place-input" type="text" autoComplete="off" value={text} placeholder={t.placeHint} onChange={(e) => typePlace(e.target.value)} />
        {sugg.length > 0 && (
          <ul className="sugg" role="listbox">
            {sugg.map((s) => (
              <li key={s.id ?? s.place + s.detail} role="option" aria-selected="false">
                <button type="button" onClick={() => choosePlace(s.place)}><b>{s.name}</b> <span>{s.detail}</span></button>
              </li>
            ))}
          </ul>
        )}
        {searched && sugg.length === 0 && <p className="note">{t.placeNotFound}</p>}
      </div>
      <button type="button" className="link loc" onClick={useLocation} disabled={locating}>📍 {locating ? t.locating : t.useLoc}</button>

      <label id="crop-label">{t.crop}</label>
      <div className="crops" role="group" aria-labelledby="crop-label">
        {crops.map((c) => (
          <button key={c} type="button" className={`crop ${profile.crop === c ? "on" : ""}`} aria-pressed={profile.crop === c} onClick={() => onChange({ ...profile, crop: c })}>
            <span className="ci" aria-hidden="true">{cropIcon(c)}</span>
            <span>{cropLabel(lang, c)}</span>
          </button>
        ))}
      </div>
      {common.length > 0 && allCrops.length > common.length && (
        <button type="button" className="link more" onClick={() => setShowAll((v) => !v)}>{showAll ? t.fewerCrops : t.moreCrops}</button>
      )}

      {extra.map((f) => (
        <div key={f.key} className="extra">
          <label htmlFor={`f-${f.key}`}>{f.label}</label>
          <input id={`f-${f.key}`} type="text" value={profile[f.key] || ""} placeholder={f.placeholder} onChange={(e) => onChange({ ...profile, [f.key]: e.target.value })} />
        </div>
      ))}

      {complete && <button type="button" className="primary" onClick={() => setOpen(false)}>{t.done}</button>}
    </section>
  );
}
