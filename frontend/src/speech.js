// Read-aloud with the browser's own voices (no network). Voices load asynchronously, so callers pass the list in.
import { BCP47 } from "./i18n.js";

export const ttsSupported = () => typeof window !== "undefined" && "speechSynthesis" in window;

export function listVoices() {
  return ttsSupported() ? speechSynthesis.getVoices() : [];
}

/** Best installed voice for a language code: exact region match first (hi-IN), then any voice for the language. */
export function pickVoice(voices, lang) {
  const tag = (BCP47[lang] || lang).toLowerCase();
  const base = lang.toLowerCase();
  return voices.find((v) => v.lang.toLowerCase().replace("_", "-") === tag)
    || voices.find((v) => v.lang.toLowerCase().replace("_", "-").startsWith(base))
    || null;
}

// What to say, per language, for the symbols and units that appear in answers. Browser voices read raw symbols aloud
// ("comma", "apostrophe", "percent sign"), so the text is turned into words first.
const SAY = {
  en: { rupees: "rupees", percent: "percent", mm: "millimetres", degC: "degrees Celsius", kmh: "kilometres per hour", km: "kilometres", per: "per", plus: "plus", minus: "minus", cubic: "cubic metres per cubic metre", and: "and" },
  hi: { rupees: "रुपये", percent: "प्रतिशत", mm: "मिलीमीटर", degC: "डिग्री सेल्सियस", kmh: "किलोमीटर प्रति घंटा", km: "किलोमीटर", per: "प्रति", plus: "प्लस", minus: "माइनस", cubic: "घन मीटर प्रति घन मीटर", and: "और" },
  mr: { rupees: "रुपये", percent: "टक्के", mm: "मिलिमीटर", degC: "अंश सेल्सिअस", kmh: "किलोमीटर प्रति तास", km: "किलोमीटर", per: "प्रति", plus: "प्लस", minus: "वजा", cubic: "घन मीटर प्रति घन मीटर", and: "आणि" },
};
const DASHES = "[-\u2010\u2011\u2012\u2013\u2212]";
const CITATION = /\s*\([^()]*(?:agmarknet|open[\s\u2010\u2011-]?meteo|data\.gov\.in|isric|soilgrids|ecocrop|fao|एगमार्कनेट|एग्मार्कनेट|ओपन[\s-]?मीटियो)[^()]*\)/giu;

/** The language the TEXT is written in (not the screen language): Devanagari letters win, else English. */
export function textLanguage(text, uiLang, answerLang) {
  const plain = String(text).replace(CITATION, "");
  const dev = (plain.match(/[\u0900-\u097f]/g) || []).length;
  const latin = (plain.match(/[A-Za-z]/g) || []).length;
  if (dev <= latin) return "en";
  if (answerLang === "hi" || answerLang === "mr") return answerLang;
  return uiLang === "mr" ? "mr" : "hi";
}

/** Plain, speakable text: no symbols, citations or raw dates that a voice would spell out. */
export function speakable(text, lang) {
  const w = SAY[lang] || SAY.en;
  const tag = BCP47[lang] || "en-IN";
  let t = String(text).replace(CITATION, "");
  t = t.replace(new RegExp(`(\\d{4})${DASHES}(\\d{2})${DASHES}(\\d{2})`, "g"), (_, y, m, d) =>
    new Date(Date.UTC(+y, +m - 1, +d)).toLocaleDateString(tag, { day: "numeric", month: "long", timeZone: "UTC" }));
  t = t.replace(/(?:Rs\.?|₹)\s*(\d[\d,]*(?:\.\d+)?)/g, `$1 ${w.rupees}`).replace(/\bRs\b\.?/g, w.rupees)
    .replace(/\s*%/g, ` ${w.percent}`)
    .replace(/°\s*C\b/g, ` ${w.degC}`)
    .replace(/\bkm\s*\/\s*h\b/gi, ` ${w.kmh}`)
    .replace(/m³\s*\/\s*m³/g, ` ${w.cubic}`)
    .replace(/\bmm\b/g, ` ${w.mm}`).replace(/\bkm\b/gi, w.km)
    .replace(new RegExp(`${w.rupees}\\s*/`, "g"), `${w.rupees} ${w.per} `)
    .replace(/\s[-\u2013\u2014]\s/g, ", ")
    .replace(/(?<=\s|^)\+(?=\d)/g, `${w.plus} `).replace(/(?<=\s|^)[\u2212-](?=\d)/g, `${w.minus} `)
    .replace(/&/g, ` ${w.and} `)
    .replace(/(?<=[\p{L}\d])\s*\/\s*(?=[\p{L}])/gu, ` ${w.per} `)
    .replace(/[()\[\]{}]/g, ", ").replace(/[·|—–;:]/g, ", ").replace(/\//g, " ")
    .replace(/(?<![\p{L}])['’‘]|['’‘](?![\p{L}])/gu, "").replace(/["“”«»`*#_~^<>=]/g, "")
    .replace(/\s*,(\s*,)+/g, ",").replace(/\s+([,.!?।])/g, "$1").replace(/[ \t]+/g, " ").replace(/^[\s,]+/, "");
  return t.trim();
}

// Chrome stops long utterances after ~15 s, so speak sentence by sentence; pieces with no letter or digit (a bare "1.") are skipped.
const sentences = (text) => (text.match(/[^.!?।\n]+[.!?।]?/g) || [text]).map((s) => s.trim()).filter((s) => /[\p{L}\d]/u.test(s));

/** Reads `text` in the language it is WRITTEN in (an English answer is read by an English voice even when the screen is Hindi). */
export function speak(text, uiLang, { voices, rate = 0.9, onEnd, answerLang } = {}) {
  if (!ttsSupported()) return false;
  const lang = textLanguage(text, uiLang, answerLang);
  const voice = pickVoice(voices || listVoices(), lang);
  if (!voice) return false;
  speechSynthesis.cancel();
  const parts = sentences(speakable(text, lang));
  if (!parts.length) return false;
  parts.forEach((s, i) => {
    const u = new SpeechSynthesisUtterance(s);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = rate;
    if (i === parts.length - 1) { u.onend = () => onEnd?.(); u.onerror = () => onEnd?.(); }
    speechSynthesis.speak(u);
  });
  return true;
}

export function stopSpeaking() {
  if (ttsSupported()) speechSynthesis.cancel();
}
