// Regenerates backend/app/domain/farm/plan_words.json (the sentences the Telegram bot sends) from the UI tables, so the app and the bot say the same thing.
// Run after editing frontend/src/plan.js or i18n.js:  node frontend/scripts/dump-plan-words.mjs
import { planWords } from "../src/plan.js";
import { labels } from "../src/i18n.js";
import fs from "node:fs";
const out = {};
for (const l of ["en", "hi", "mr"]) {
  const w = planWords(l), t = labels(l);
  const strings = Object.fromEntries(Object.entries(w).filter(([, v]) => typeof v === "string"));
  out[l] = { ...strings, c: w.c, dir: w.dir, sig: w.sig, checks: w.checks, flags: t.flags, alerts: t.alerts, crops: t.crops || {} };
}
fs.writeFileSync(new URL("../../backend/app/domain/farm/plan_words.json", import.meta.url), JSON.stringify(out, null, 1));
console.log(Object.keys(out.hi).length, "keys");
