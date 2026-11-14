// Runs the browser capture script's parser against a fixture page under node (no network).
import fs from 'node:fs';
globalThis.window = globalThis;
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
const rec = window.__pfParse(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify({id: rec.id, vin: rec.vin, tr: rec.tr, pv: rec.pv, vp: rec.vp, mi: rec.mi, opts: rec.opts.length,
  imgs: Object.values(rec.imgs).reduce((a, b) => a + b.length, 0), adv: rec.adv, pa: rec.pa, fns: ['__pfStart', '__pfStatus', '__pfSave'].map(f => typeof window[f])}));
