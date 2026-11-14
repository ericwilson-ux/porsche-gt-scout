'use strict';
/* GT Scout front end. Static: reads data/*.json produced by the daily run. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => n == null ? 'n/a' : (n < 0 ? '-$' : '$') + Math.abs(Math.round(n)).toLocaleString('en-US');
const num = n => n == null ? 'n/a' : Math.round(n).toLocaleString('en-US');
const pct = (x, d = 1) => (x > 0 ? '+' : '') + (x * 100).toFixed(d) + '%';
const day = s => s ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? s + 'T12:00:00' : s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : 'n/a';
const store = {
  get(k, d) { try { const v = JSON.parse(localStorage.getItem('gts:' + k)); return v ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('gts:' + k, JSON.stringify(v)); } catch { /* private mode: state lives in memory */ } },
};
const S = {
  idx: null, market: null, health: null, detail: {},
  tab: store.get('tab', 'qualifying'), sort: store.get('sort', 'score'), f: store.get('filters', {}),
  favs: new Set(store.get('favs', [])), cmp: new Set(store.get('cmp', [])), otd: store.get('otd', {}), group: null, gal: null,
};
const FAMILY_HEX = { white: '#f4f4f1', silver: '#bcbfc3', gray: '#7d8084', black: '#111', blue: '#0067b9', red: '#c8102e', yellow: '#f6c700',
  green: '#3b7a3a', orange: '#e2701c', purple: '#7a3d8f', brown: '#6b4a2f', beige: '#d8c7a6', tan: '#a8754a', other: '#999' };
const CAT_LABEL = { confirmed: 'Confirmed match', lead: 'Lead, needs verification', near_miss: 'Near miss', excluded: 'Excluded' };
const TRANS = { manual: 'Manual', pdk: 'PDK' };
const src = (p, w) => !p ? '' : p.k === 'pf' ? `${p.u}/${w}` : p.u;
const fileKey = k => k.replace(/:/g, '_');
const title = c => `${c.year ?? ''} ${c.label ?? c.variant_label ?? ''}`.trim();
const live = () => S.idx.vehicles.filter(c => c.status === 'active' || c.status === 'unverified');

async function getJSON(path) { const r = await fetch(path, { cache: 'no-cache' }); if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`); return r.json(); }

/* ---------------- out-the-door (mirrors pgt/otd.py; assumptions editable) ---------------- */
function A() { return { ...S.idx.otd, ...S.otd }; }
function otdFor(c) {
  if (c.price == null) return null;
  const a = A();
  const published = c.fees_basis === 'published by dealer';
  const fees = published ? c.fees : (c.state === 'TX' ? a.default_doc_fee_tx : a.default_doc_fee_other);
  const tax = (c.price + fees) * a.sales_tax_rate;
  const ttl = a.title_fee + a.registration_fee + a.county_fee + a.state_misc_fees;
  let ship = null, mode = 'unknown';
  if (c.dist != null) {
    if (c.dist <= a.drive_radius_miles) { ship = 0; mode = 'drive'; }
    else {
      const raw = a.ship_base + Math.min(c.dist, 500) * a.ship_rate_short + Math.max(0, Math.min(c.dist, 1500) - 500) * a.ship_rate_mid + Math.max(0, c.dist - 1500) * a.ship_rate_long;
      ship = Math.round(Math.max(a.ship_minimum, raw) / 10) * 10; mode = 'enclosed transport';
    }
  }
  return { price: c.price, fees, feesBasis: published ? 'published by dealer' : 'assumed', tax, ttl, ship, mode, total: c.price + fees + tax + ttl + (ship ?? 0) };
}

/* ---------------- shell ---------------- */
function setFresh() {
  const el = $('#fresh'), i = S.idx;
  const times = Object.values(i.last_success || {}).sort();
  const last = times.length ? new Date(times[times.length - 1]) : null;
  const hours = last ? (Date.now() - last) / 36e5 : Infinity;
  const st = i.last_run ? i.last_run.status : 'failed';
  el.className = 'fresh ' + (hours > 36 ? 'old' : st);
  el.innerHTML = last ? `<span>Sources last refreshed</span> ${hours < 1 ? 'under an hour ago' : hours < 48 ? Math.round(hours) + 'h ago' : Math.round(hours / 24) + ' days ago'}` : 'No successful refresh yet';
  const stale = $('#stale');
  if (hours > 36) { stale.hidden = false; stale.textContent = `Showing stored inventory: the last successful source refresh was ${last ? last.toLocaleString() : 'never'}. Prices and availability may have changed. See Sources for details.`; }
  else if (st !== 'ok') { stale.hidden = false; stale.textContent = `The latest run was ${st}: some sources did not refresh completely. See Sources for what is missing.`; }
  else stale.hidden = true;
}
function setNav(name) { $$('.nav a').forEach(a => a.toggleAttribute('aria-current', false)); const a = $(`.nav a[data-nav="${name}"]`); if (a) a.setAttribute('aria-current', 'page'); $('#cmpCount').textContent = S.cmp.size || ''; }
function toggleSet(set, key, name) { set.has(key) ? set.delete(key) : set.add(key); store.set(name, [...set]); $('#cmpCount').textContent = S.cmp.size || ''; }

/* ---------------- inventory ---------------- */
const TABS = [['qualifying', 'Qualifying', c => c.cat === 'confirmed' || c.cat === 'lead'], ['confirmed', 'Confirmed', c => c.cat === 'confirmed'],
  ['lead', 'Leads to verify', c => c.cat === 'lead'], ['near_miss', 'Near misses', c => c.cat === 'near_miss'], ['all', 'Everything tracked', () => true]];
function passes(c) {
  const f = S.f;
  if (f.variants?.length && !f.variants.includes(c.variant)) return false;
  if (f.gens?.length && !f.gens.includes(c.gen)) return false;
  if (f.pmin && (c.price ?? 0) < f.pmin) return false;
  if (f.pmax && (c.price ?? Infinity) > f.pmax) return false;
  if (f.ymin && (c.year ?? 0) < f.ymin) return false;
  if (f.ymax && (c.year ?? 9999) > f.ymax) return false;
  if (f.mmax && (c.miles ?? Infinity) > f.mmax) return false;
  if (f.ext?.length && !f.ext.includes(c.extf)) return false;
  if (f.int?.length && !f.int.includes(c.intf)) return false;
  if (f.trans && c.trans !== f.trans) return false;
  if (f.seats && c.seats !== f.seats) return false;
  if (f.cpo && !c.cpo) return false;
  if (f.lift && c.lift !== 'verified') return false;
  if (f.noW && c.weissach === true) return false;
  if (f.pccb === 'yes' && c.pccb !== true) return false;
  if (f.pccb === 'no' && c.pccb !== false) return false;
  if (f.tx && !c.tx) return false;
  if (f.dmax && (c.dist ?? Infinity) > f.dmax) return false;
  if (f.smin && c.score < f.smin) return false;
  if (f.newOnly && !c.new) return false;
  if (f.drops && !c.drop) return false;
  if (f.favs && !S.favs.has(c.key)) return false;
  if (f.state && c.state !== f.state) return false;
  if (f.q) { const hay = `${title(c)} ${c.vin} ${c.ext} ${c.int} ${c.seller} ${c.city} ${c.state}`.toLowerCase(); if (!f.q.toLowerCase().split(/\s+/).every(w => hay.includes(w))) return false; }
  return true;
}
const SORTS = {
  score: ['Best preference match', (a, b) => b.score - a.score || (a.price ?? 9e9) - (b.price ?? 9e9)],
  deal: ['Best deal vs comparables', (a, b) => (a.deal.delta ?? 9) - (b.deal.delta ?? 9) || b.score - a.score],
  near: ['Closest to Austin', (a, b) => (a.dist ?? 9e9) - (b.dist ?? 9e9)],
  price: ['Price, low to high', (a, b) => (a.price ?? 9e9) - (b.price ?? 9e9)],
  priced: ['Price, high to low', (a, b) => (b.price ?? 0) - (a.price ?? 0)],
  otd: ['Out-the-door, low to high', (a, b) => (otdFor(a)?.total ?? 9e9) - (otdFor(b)?.total ?? 9e9)],
  miles: ['Mileage, low to high', (a, b) => (a.miles ?? 9e9) - (b.miles ?? 9e9)],
  newest: ['Newest listing', (a, b) => (b.since || b.first).localeCompare(a.since || a.first)],
};
function liftChip(c) { return c.lift === 'verified' ? '<span class="chip ok">Front lift</span>' : c.lift === 'seller_stated' ? '<span class="chip ok">Front lift (seller)</span>' : c.lift === 'verified_absent' ? '<span class="chip bad">No front lift</span>' : '<span class="chip q">Front lift unverified</span>'; }
function dealText(d) { return d.rating ? `${d.rating[0].toUpperCase() + d.rating.slice(1)} deal ${pct(d.delta)}` : 'Deal: too few comps'; }
function distText(c) { if (c.dist == null) return ''; return c.tx ? `<span class="near">Texas</span> ${num(c.dist)} mi` : c.dist <= 600 ? `<span class="near">Drivable</span> ${num(c.dist)} mi` : `${num(c.dist)} mi away`; }
function cardHTML(c, archive) {
  const o = otdFor(c), t = c.thumbs || [];
  const flags = [c.new ? '<span class="flag new">New</span>' : '', c.drop && c.lpc ? `<span class="flag drop">${money(c.lpc.delta)}</span>` : '',
    c.status === 'unverified' ? '<span class="flag">Availability unconfirmed</span>' : '', c.status === 'sold' ? '<span class="flag">Sold</span>' : '', c.status === 'removed' ? '<span class="flag">Delisted</span>' : ''].join('');
  const pc = c.nphotos ? `<span class="pcount${c.gcomplete ? '' : ' warn'}">${c.gcomplete ? c.nphotos + ' photos' : `${c.nphotos} of ${c.nadv} photos`}</span>` : '';
  const chips = [liftChip(c), c.cpo ? '<span class="chip ok">CPO</span>' : '', c.unver.includes('accident') ? '<span class="chip q">Accident history</span>' : '',
    c.seats ? `<span class="chip">${c.seats === 'bucket' ? 'Bucket seats' : 'Sport seats'}</span>` : '', c.weissach === true ? '<span class="chip">Weissach</span>' : '', c.pccb === true ? '<span class="chip">PCCB</span>' : '',
    c.conflicts ? `<span class="chip q">${c.conflicts} source conflict${c.conflicts > 1 ? 's' : ''}</span>` : ''].join('');
  const priceLine = archive
    ? `<span class="price">${c.sale ? money(c.sale.price) : money(c.price)}</span><span class="muted">${c.sale ? 'sold ' + day(c.sale.date) : 'last asking, not a sale price'}</span>`
    : `<span class="price">${money(c.price)}</span>${c.dprice ? `<span class="was">${c.dprice < 0 ? 'down' : 'up'} ${money(Math.abs(c.dprice))} from ${money(c.price0)}</span>` : ''}`;
  return `<article class="card ${c.status === 'unverified' ? 'unverified-status' : ''}" data-key="${esc(c.key)}">
  <div class="shot">${t.length ? `<img src="${esc(src(t[0], 640))}" alt="${esc(title(c))} in ${esc(c.ext || 'unknown colour')}" loading="lazy" data-i="0">` : '<div class="noimg">No photos published</div>'}
    <div class="ticks" aria-hidden="true">${t.map((_, i) => `<b class="${i ? '' : 'on'}"></b>`).join('')}</div>
    <div class="flags">${flags}</div>${archive ? '' : `<div class="score" title="Preference match"><b>${c.score}</b><span>match</span></div>`}${pc}</div>
  <div class="body">
    <div class="price-row">${priceLine}</div>
    <a class="title" href="#/v/${encodeURIComponent(c.key)}">${esc(title(c))}${c.gen && c.gen !== '982' ? ' <span class="muted">' + esc(c.gen) + '</span>' : ''}</a>
    <div class="facts">${num(c.miles)} mi, ${TRANS[c.trans] || 'transmission n/a'}, ${esc(c.ext || 'colour n/a')} over ${esc(c.intf || 'n/a')}</div>
    <div class="where"><span>${esc(c.seller || 'Seller n/a')}, ${esc([c.city, c.state].filter(Boolean).join(', '))}</span><span>${distText(c)}</span></div>
    <div class="chips">${chips}</div>
    <div class="foot"><span>${archive ? `Tracked ${day(c.first)} to ${day(c.last_seen)}` : `<span class="deal ${c.deal.rating || ''}">${dealText(c.deal)}</span><br><span class="muted">Out the door about ${money(o?.total)}</span>`}</span>
      <span class="acts"><button class="act" data-fav aria-pressed="${S.favs.has(c.key)}" title="Save to favourites">${S.favs.has(c.key) ? 'Saved' : 'Save'}</button><button class="act" data-cmp aria-pressed="${S.cmp.has(c.key)}" title="Add to comparison">Compare</button></span></div>
  </div></article>`;
}
function filterPanel() {
  const f = S.f, v = S.idx.variants, all = live();
  const fams = k => [...new Set(all.map(c => c[k]).filter(Boolean))].sort();
  const states = [...new Set(all.map(c => c.state).filter(Boolean))].sort();
  const ck = (name, label) => `<label><input type="checkbox" data-f="${name}" ${f[name] ? 'checked' : ''}>${label}</label>`;
  const multi = (name, val, label) => `<label><input type="checkbox" data-m="${name}" value="${val}" ${f[name]?.includes(val) ? 'checked' : ''}>${label}</label>`;
  const n = (name, ph) => `<input type="number" inputmode="numeric" data-n="${name}" placeholder="${ph}" value="${f[name] ?? ''}" aria-label="${ph}">`;
  const sw = (name, fam) => `<button type="button" class="sw" data-sw="${name}" value="${fam}" aria-pressed="${!!f[name]?.includes(fam)}"><i style="background:${FAMILY_HEX[fam] || '#999'}"></i>${fam}</button>`;
  const sel = (name, opts) => `<select data-s="${name}" aria-label="${name}">${opts.map(([val, l]) => `<option value="${val}" ${String(f[name] ?? '') === val ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  return `<form class="filters" id="filters" onsubmit="return false">
  <fieldset><legend>Search</legend><input type="search" data-q placeholder="VIN, colour, dealer, city" value="${esc(f.q || '')}" aria-label="Search"></fieldset>
  <fieldset><legend>Model</legend>${Object.entries(v).map(([k, o]) => multi('variants', k, esc(o.label))).join('')}
    ${multi('gens', '992.1', '992.1 generation')}${multi('gens', '992.2', '992.2 generation')}</fieldset>
  <fieldset><legend>Price</legend><div class="pair">${n('pmin', 'Min $')}${n('pmax', 'Max $')}</div></fieldset>
  <fieldset><legend>Year and mileage</legend><div class="pair">${n('ymin', 'From year')}${n('ymax', 'To year')}</div><div class="pair" style="margin-top:8px">${n('mmax', 'Max miles')}${n('smin', 'Min match')}</div></fieldset>
  <fieldset><legend>Exterior</legend><div class="swatches">${fams('extf').map(x => sw('ext', x)).join('')}</div></fieldset>
  <fieldset><legend>Interior</legend><div class="swatches">${fams('intf').map(x => sw('int', x)).join('')}</div></fieldset>
  <fieldset><legend>Specification</legend>
    ${sel('trans', [['', 'Any transmission'], ['manual', 'Manual'], ['pdk', 'PDK']])}
    <div style="height:8px;clear:both"></div>${sel('seats', [['', 'Any seats'], ['adjustable', 'Adjustable sport seats'], ['bucket', 'Bucket seats']])}
    <div style="height:8px;clear:both"></div>${sel('pccb', [['', 'Any brakes'], ['yes', 'PCCB ceramic'], ['no', 'Steel brakes']])}
    ${ck('lift', 'Front lift verified by factory data')}${ck('noW', 'Without Weissach Package')}${ck('cpo', 'Porsche Approved CPO')}</fieldset>
  <fieldset><legend>Location</legend>${ck('tx', 'In Texas')}<div class="pair">${n('dmax', 'Max miles away')}${sel('state', [['', 'Any state'], ...states.map(s => [s, s])])}</div></fieldset>
  <fieldset><legend>Activity</legend>${ck('newOnly', 'New this week')}${ck('drops', 'Price reduced in last 14 days')}${ck('favs', 'My favourites')}</fieldset>
  <fieldset><button type="button" class="linkbtn" id="clearF">Clear all filters</button></fieldset></form>`;
}
function viewInventory() {
  setNav('inventory');
  const all = live(), tabDef = TABS.find(t => t[0] === S.tab) || TABS[0];
  const rows = all.filter(tabDef[2]).filter(passes).sort(SORTS[S.sort]?.[1] || SORTS.score[1]);
  const cnt = fn => all.filter(fn).length;
  const drive = all.filter(c => (c.cat === 'confirmed' || c.cat === 'lead') && c.dist != null && c.dist <= 600).length;
  $('#app').innerHTML = `<section class="head"><h1>${cnt(TABS[1][2])} confirmed, ${cnt(TABS[2][2])} to verify</h1>
    <p>Used 992 GT3, GT3 Touring, 718 Cayman GT4 RS and 718 Spyder RS for sale across the US, checked against your hard requirements. ${drive} of the qualifying cars are within driving distance of ${esc(S.idx.home)}.</p></section>
    <div class="tabs" role="tablist">${TABS.map(([k, l, fn]) => `<button class="tab" role="tab" aria-selected="${k === S.tab}" data-tab="${k}"><span class="n">${cnt(fn)}</span>${l}</button>`).join('')}</div>
    <div class="layout">${filterPanel()}<section><div class="bar"><button class="btn alt filter-toggle" id="fToggle">Filters</button>
      <span class="grow">${rows.length} ${rows.length === 1 ? 'car' : 'cars'}${rows.length !== cnt(tabDef[2]) ? ` of ${cnt(tabDef[2])} (filtered)` : ''}</span>
      <button class="linkbtn" id="otdBtn">Out-the-door assumptions</button>
      <label class="sr" for="sort">Sort</label><select id="sort">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}" ${k === S.sort ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      ${rows.length ? `<div class="grid" id="grid">${rows.map(c => cardHTML(c)).join('')}</div>` : `<div class="empty"><h2>No cars match</h2><p>${all.length ? 'Nothing in this tab passes the current filters. Clear a filter or open Everything tracked to see cars that miss a hard requirement, with the reason.' : 'No inventory has been collected yet. The first daily run fills this page; check Sources for run status.'}</p></div>`}</section></div>`;
}
function onInventoryInput(e) {
  const t = e.target, f = S.f;
  if (t.dataset.f) f[t.dataset.f] = t.checked;
  else if (t.dataset.m) { const a = new Set(f[t.dataset.m] || []); t.checked ? a.add(t.value) : a.delete(t.value); f[t.dataset.m] = [...a]; }
  else if (t.dataset.n) f[t.dataset.n] = t.value ? Number(t.value) : null;
  else if (t.dataset.s) f[t.dataset.s] = t.value || null;
  else if ('q' in t.dataset) f.q = t.value;
  else if (t.id === 'sort') { S.sort = t.value; store.set('sort', S.sort); }
  else return;
  store.set('filters', f);
  const focus = t.dataset.n || ('q' in t.dataset ? 'q' : null), pos = t.selectionStart;
  viewInventory();
  if (focus) { const el = focus === 'q' ? $('[data-q]') : $(`[data-n="${focus}"]`); if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch { /* number inputs */ } } }
}

/* ---------------- gallery ---------------- */
function Gallery(photos, meta) { return { photos, meta, i: 0, open: false }; }
function galleryHTML(g, big) {
  const p = g.photos[g.i], n = g.photos.length, cats = [...new Set(g.photos.map(x => x.c).filter(Boolean))];
  if (!n) return `<div class="gal"><div class="gal-main"><div class="noimg">The seller published no photos for this car.</div></div></div>`;
  const jump = cats.length > 1 ? cats.map(c => `<button data-jump="${g.photos.findIndex(x => x.c === c)}" aria-pressed="${p.c === c}">${esc(c[0].toUpperCase() + c.slice(1))} ${g.photos.filter(x => x.c === c).length}</button>`).join('') : '';
  const warn = g.meta && !g.meta.complete && g.meta.advertised ? `<span class="gal-warn">Incomplete gallery: ${g.meta.captured} of ${g.meta.advertised} captured</span>` : '';
  return `<div class="gal" data-gal><div class="gal-main"><img src="${esc(src(p, 1280))}" alt="Photo ${g.i + 1} of ${n}${p.c && p.c !== 'uncategorized' ? ', ' + esc(p.c) : ''}" data-main ${p.a ? `data-a="${esc(p.a)}"` : ''}>
    <button class="gal-nav prev" data-step="-1" aria-label="Previous photo">&#8249;</button><button class="gal-nav next" data-step="1" aria-label="Next photo">&#8250;</button></div>
    <div class="gal-bar"><span><span class="counter">${g.i + 1} / ${n}</span> ${warn}</span><span class="btnrow">${jump}<button data-full>${big ? 'Close' : 'Full screen'}</button></span></div>
    <div class="thumbs">${g.photos.map((x, k) => `<button data-go="${k}" aria-current="${k === g.i}" aria-label="Photo ${k + 1}"><img src="${esc(src(x, 320))}" alt="" loading="lazy"></button>`).join('')}</div></div>`;
}
function paintGallery() {
  const g = S.gal; if (!g) return;
  const mount = $('#galMount'); if (mount) mount.innerHTML = galleryHTML(g, false);
  const lb = $('#lightbox'); lb.hidden = !g.open; lb.innerHTML = g.open ? galleryHTML(g, true) : '';
  const cur = $((g.open ? '#lightbox' : '#galMount') + ' .thumbs [aria-current="true"]'); if (cur) cur.scrollIntoView({ block: 'nearest', inline: 'center' });
  const nx = g.photos[(g.i + 1) % g.photos.length]; if (nx) new Image().src = src(nx, 1280);
}
function galStep(d) { const g = S.gal; if (!g || !g.photos.length) return; g.i = (g.i + d + g.photos.length) % g.photos.length; paintGallery(); }
function galFull(on) { const g = S.gal; if (!g) return; g.open = on; paintGallery(); const lb = $('#lightbox'); if (on) lb.requestFullscreen?.().catch(() => {}); else if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {}); }

/* ---------------- detail ---------------- */
const ICON = { pass: '✓', unverified: '?', fail: '✕' };
function stepChart(hist) {
  const pts = hist.filter(p => p.price != null).map(p => ({ t: +new Date(p.at), y: p.price, p }));
  if (pts.length < 2) return `<p class="note">${pts.length ? `One price observation so far: ${money(pts[0].y)} on ${day(pts[0].p.at)} (${esc(pts[0].p.source)}). Changes will chart here as they are observed.` : 'No price observations.'}</p>`;
  const W = 720, H = 200, L = 70, R = 16, T = 14, B = 26, t0 = pts[0].t, t1 = Math.max(Date.now(), pts[pts.length - 1].t), ys = pts.map(p => p.y);
  const lo = Math.min(...ys), hi = Math.max(...ys), pad = Math.max(1000, (hi - lo) * 0.2), y0 = lo - pad, y1 = hi + pad;
  const X = t => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0), Y = y => T + (H - T - B) * (1 - (y - y0) / (y1 - y0));
  let d = `M${X(pts[0].t)},${Y(pts[0].y)}`; pts.slice(1).forEach(p => { d += `H${X(p.t)}V${Y(p.y)}`; }); d += `H${X(t1)}`;
  const ticks = [y0 + (y1 - y0) * 0.15, (y0 + y1) / 2, y1 - (y1 - y0) * 0.15];
  return `<div class="viz"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Asking price history"><g class="grid">${ticks.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>
  <g class="axis">${ticks.map(v => `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${money(v)}</text>`).join('')}<text x="${L}" y="${H - 6}">${day(pts[0].p.at)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">today</text></g>
  <path d="${d}" fill="none" stroke="var(--series-1)" stroke-width="2"/>
  ${pts.map(p => `<circle cx="${X(p.t)}" cy="${Y(p.y)}" r="5" fill="var(--series-1)" stroke="var(--white)" stroke-width="2" data-tip="<b>${money(p.y)}</b>${day(p.p.at)}<br>${esc(p.p.source)}${p.p.kind !== 'asking' ? '<br>' + esc(p.p.kind.replace(/_/g, ' ')) : ''}"/>`).join('')}
  <text class="lbl" x="${X(pts[pts.length - 1].t) + 8}" y="${Y(pts[pts.length - 1].y) - 8}">${money(pts[pts.length - 1].y)}</text></svg></div>`;
}
function otdPanel(c) {
  const o = otdFor(c); if (!o) return '<p class="note">No advertised price, so no estimate.</p>';
  return `<dl class="rows"><div><dt>Advertised price</dt><dd>${money(o.price)}</dd></div>
  <div><dt>Dealer fees <span class="muted">(${o.feesBasis})</span></dt><dd>${money(o.fees)}</dd></div>
  <div><dt>Texas motor vehicle tax ${(A().sales_tax_rate * 100).toFixed(2)}%</dt><dd>${money(o.tax)}</dd></div>
  <div><dt>Title and registration, Travis County</dt><dd>${money(o.ttl)}</dd></div>
  <div><dt>${o.mode === 'drive' ? 'Shipping <span class="muted">(close enough to drive)</span>' : o.ship == null ? 'Shipping <span class="muted">(location unknown)</span>' : `Enclosed transport, about ${num(c.dist)} mi <span class="muted">(estimate)</span>`}</dt><dd>${o.ship == null ? 'n/a' : money(o.ship)}</dd></div>
  <div class="total"><dt>Estimated out the door</dt><dd>${money(o.total)}</dd></div></dl>
  <p class="note">Estimate for registering in Austin. Tax is owed to Texas even on an out-of-state purchase, with credit for tax paid elsewhere. <button class="linkbtn" id="otdBtn">Change assumptions</button></p>`;
}
async function viewDetail(key) {
  const c = S.idx.vehicles.find(v => v.key === key);
  setNav(c && (c.status === 'removed' || c.status === 'sold') ? 'archive' : 'inventory');
  if (!c) { $('#app').innerHTML = `<div class="page"><h1>Car not found</h1><p>This vehicle is not in the database. <a href="#/">Back to inventory</a></p></div>`; return; }
  $('#app').innerHTML = '<p class="loading">Loading vehicle</p>';
  let m; try { m = S.detail[key] ||= await getJSON(`data/v/${fileKey(key)}.json`); } catch (e) { $('#app').innerHTML = `<div class="page"><h1>Could not load this car</h1><p>${esc(e.message)}</p></div>`; return; }
  S.gal = Gallery(m.photos || [], m.gallery);
  const el = m.eligibility, eq = m.equipment || {}, s = m.seller || {}, val = m.valuation || {}, h = m.history || {}, w = m.warranty_summary || {};
  const hits = new Set(['474', '2UH']);
  const evTxt = e => e.status.replace(/_/g, ' ');
  const spec = [['VIN', `${esc(m.vin || 'n/a')}${m.vin ? (m.vin_valid ? ' <span class="muted">check digit ok</span>' : ' <span class="chip bad">check digit fails</span>') : ''}`],
    ['Model', `${esc(title(c))}, ${esc(m.generation || '')}`], ['Mileage', num(m.mileage) + ' mi'], ['Transmission', esc(m.transmission_raw || TRANS[m.transmission] || 'n/a')],
    ['Exterior', `${m.exterior_hex ? `<i style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${esc(m.exterior_hex)};border:1px solid #0003"></i> ` : ''}${esc(m.exterior_color || 'n/a')}`], ['Interior', esc(m.interior_color || 'n/a')],
    ['Seats', eq.seats?.value ? (eq.seats.value === 'bucket' ? 'Full bucket seats' : 'Adjustable sport seats') + ` <span class="muted">${evTxt(eq.seats)}</span>` : 'Not documented'],
    ['Brakes', eq.pccb?.value == null ? 'Not documented' : (eq.pccb.value ? 'PCCB ceramic' : 'Steel') + ` <span class="muted">${evTxt(eq.pccb)}</span>`],
    ['Front axle lift', `${eq.lift?.value ? 'Yes' : eq.lift?.value === false ? 'No' : 'Unknown'} <span class="muted">${evTxt(eq.lift || { status: 'unknown' })}</span>`],
    ['Weissach Package', `${eq.weissach?.value ? 'Yes' : eq.weissach?.value === false ? 'No' : 'Unknown'} <span class="muted">${evTxt(eq.weissach || { status: 'unknown' })}</span>`],
    ['Certification', m.cpo ? 'Porsche Approved Certified Pre-Owned' : 'Not CPO'],
    ['Warranty', (m.warranty || []).map(x => `${esc(x.label || x.type)} ${esc(x.duration || '')}`).join('<br>') || (w.factory_months_remaining_est != null ? `About ${w.factory_months_remaining_est} months of factory warranty left <span class="muted">(${esc(w.factory_start_basis)})</span>` : 'n/a')],
    ['Owners and service', [h.previous_owners != null ? `${h.previous_owners} previous owner${h.previous_owners === 1 ? '' : 's'}` : null, h.service_history ? 'Service history: ' + esc(h.service_history) : null].filter(Boolean).join('<br>') || 'Not published'],
    ['Stock number', esc(m.stock_number || 'n/a')], ['On the market', `${m.days_on_market ?? 'n/a'} days <span class="muted">since ${day(m.listed_since)}, ${esc(m.listed_since_basis || '')}</span>`]];
  const docs = m.documents || {};
  $('#app').innerHTML = `<div class="detail"><div class="crumb"><a class="back" href="${c.status === 'removed' || c.status === 'sold' ? '#/archive' : '#/'}">&#8249; ${c.status === 'removed' || c.status === 'sold' ? 'Archive' : 'Inventory'}</a>
    <span class="muted">${esc(m.sources.map(x => x.label).join(' + '))}, data quality ${esc(m.quality?.level || 'n/a')}${m.quality?.notes?.length ? ': ' + esc(m.quality.notes.join(', ')) : ''}</span></div>
  <div class="dgrid"><div><div id="galMount"></div>
    ${(m.conflicts || []).length ? `<section class="sect"><h2>Sources disagree</h2>${m.conflicts.map(x => `<div class="conflict">${esc(x.detail)}</div>`).join('')}</section>` : ''}
    <section class="sect"><h2>Specification</h2><dl class="rows cols">${spec.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl></section>
    <section class="sect"><h2>Factory options <small>${m.options.length ? `${m.options.length} options from ${esc(eq.lift?.source || 'the source')} manufacturer data` : 'no option list published'}</small></h2>
      ${m.options.length ? `<ul class="optlist">${m.options.map(o => `<li class="${hits.has(String(o.code)) || /weissach|lift/i.test(o.name) ? 'hit' : ''}"><code>${esc(o.code || '')}</code><span>${esc(o.name)}</span></li>`).join('')}</ul>` : '<p class="note">No source exposes this car\'s factory option list, so front lift, seats, brakes and Weissach status cannot be verified here. Ask the seller for the window sticker or build sheet.</p>'}
      ${docs.window_sticker_available || docs.build_sheet_available ? `<p class="note">${[docs.window_sticker_available ? 'Window sticker' : null, docs.build_sheet_available ? 'build sheet' : null].filter(Boolean).join(' and ')} available on the <a href="${esc(docs.source_url || m.sources[0].url)}" target="_blank" rel="noopener">Porsche Finder listing</a>.</p>` : ''}
      ${m.standard_equipment?.length ? `<details class="tbl"><summary>Standard equipment (${m.standard_equipment.length})</summary><ul class="optlist">${m.standard_equipment.map(x => `<li><span>${esc(x)}</span></li>`).join('')}</ul></details>` : ''}</section>
    <section class="sect"><h2>Asking price history <small>advertised prices, not sale prices</small></h2>${stepChart(m.price_history || [])}
      ${m.sale ? `<p><b>Sold for ${money(m.sale.price)}</b> on ${day(m.sale.date)} at ${esc(m.sale.venue || 'n/a')}. <a href="${esc(m.sale.url)}" target="_blank" rel="noopener">Sale record</a></p>` : c.status === 'removed' ? `<p class="note">Delisted. Last asking price ${money(m.price)}. No sale price is recorded because none has been publicly documented.</p>` : ''}
      <details class="tbl"><summary>All price observations (${(m.price_history || []).length})</summary><div class="tablewrap"><table><thead><tr><th>Observed</th><th class="r">Price</th><th>Kind</th><th>Source</th></tr></thead><tbody>${(m.price_history || []).map(p => `<tr><td>${day(p.at)}</td><td class="r">${money(p.price)}</td><td>${esc(p.kind.replace(/_/g, ' '))}</td><td>${esc(p.source)}</td></tr>`).join('')}</tbody></table></div></details></section>
    ${m.seller_comment ? `<section class="sect"><h2>Seller's description</h2><div class="comment">${esc(m.seller_comment)}</div></section>` : ''}
    <section class="sect"><h2>Where it is listed</h2><div class="tablewrap"><table><thead><tr><th>Source</th><th>Status</th><th class="r">Price</th><th class="r">Miles</th><th>First seen</th><th>Last seen</th><th></th></tr></thead><tbody>
      ${m.sources.map(x => `<tr><td>${esc(x.label)}${x.origin_site ? ` <span class="muted">via ${esc(x.origin_site)}</span>` : ''}</td><td>${esc(x.status)}</td><td class="r">${money(x.price)}</td><td class="r">${num(x.mileage)}</td><td>${day(x.first_seen)}</td><td>${day(x.last_seen)}</td><td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">Original listing</a>` : ''}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="sect"><h2>Record history</h2><ul class="timeline">${(m.events || []).slice().reverse().map(e => `<li><span class="muted">${new Date(e.at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}</span><span><b>${esc(e.event.replace(/_/g, ' '))}</b> ${e.detail && typeof e.detail === 'object' ? esc(Object.entries(e.detail).filter(([, v]) => v != null && typeof v !== 'object').map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'number' && Math.abs(v) > 999 ? money(v) : v}`).join(', ')) : esc(e.detail || '')}</span></li>`).join('')}</ul></section>
  </div>
  <aside class="side"><div><h1>${esc(title(c))}</h1><div class="muted">${num(m.mileage)} mi, ${TRANS[m.transmission] || 'n/a'}, ${esc(m.exterior_color || '')}</div></div>
    <div><div class="bigprice">${money(m.price)}</div>${m.dealer_fees_listed ? `<div class="muted">Listed at ${money(m.price_as_listed)} including ${money(m.dealer_fees_listed)} dealer fees</div>` : ''}
      ${m.price_change_total ? `<div class="was">${m.price_change_total < 0 ? 'Down' : 'Up'} ${money(Math.abs(m.price_change_total))} from the first observed ${money(m.original_price)}</div>` : ''}</div>
    <div class="verdict ${el.category}">${CAT_LABEL[el.category]}<small>${esc(el.summary)}</small></div>
    <div class="btnrow"><button class="btn" data-fav data-key="${esc(key)}" aria-pressed="${S.favs.has(key)}">${S.favs.has(key) ? 'Saved to favourites' : 'Save to favourites'}</button><button class="btn alt" data-cmp data-key="${esc(key)}" aria-pressed="${S.cmp.has(key)}">${S.cmp.has(key) ? 'In comparison' : 'Add to comparison'}</button></div>
    <div class="panel"><h2>Hard requirements</h2><ul class="checks">${el.checks.map(k => `<li class="${k.status}"><span class="ic" aria-label="${k.status}">${ICON[k.status]}</span><div><b>${esc(k.label)}</b><span>${esc(k.detail || '')}</span>${k.provenance ? `<em>Evidence: ${esc(k.provenance)}</em>` : ''}</div></li>`).join('')}</ul></div>
    <div class="panel"><h2>Preference match ${m.score.total}<small>of 100</small></h2><div class="bars">${m.score.items.map(i => `<div class="barrow"><span>${esc(i.label)}</span><b>${i.points.toFixed(1)} / ${i.max}</b><div class="track"><i style="width:${i.fraction * 100}%"></i></div><span class="note">${esc(i.note)}</span></div>`).join('')}</div></div>
    <div class="panel"><h2>Deal quality <small>separate from match</small></h2>${val.rating ? `<dl class="rows"><div><dt>Fair asking range at this mileage</dt><dd>${money(val.fair_low)} to ${money(val.fair_high)}</dd></div><div><dt>This car vs comparable median</dt><dd>${pct(val.delta_pct)} (${esc(val.rating)})</dd></div><div><dt>Comparables used</dt><dd>${val.n} asking prices, ${esc(val.year_window)}</dd></div><div><dt>Confidence</dt><dd>${esc(val.confidence)}</dd></div></dl>` : ''}<p class="note">${esc(val.note || '')}${val.sales ? ` ${val.sales.n} completed sale${val.sales.n > 1 ? 's' : ''} on record for this model, median ${money(val.sales.median)}.` : ''} <a href="#/market">Market view</a></p></div>
    <div class="panel"><h2>Out the door in Austin</h2>${otdPanel(c)}</div>
    <div class="panel"><h2>Seller</h2><dl class="rows"><div><dt>${esc(s.kind === 'porsche_dealer' ? 'Porsche dealer' : s.kind || 'Seller')}</dt><dd>${esc(s.name || 'n/a')}</dd></div><div><dt>Address</dt><dd>${esc([s.street, s.city, s.state, s.zip].filter(Boolean).join(', ') || 'n/a')}</dd></div>
      <div><dt>Phone</dt><dd>${s.phone ? `<a href="tel:${esc(s.phone.replace(/[^\d+]/g, ''))}">${esc(s.phone)}</a>` : 'Not published'}</dd></div><div><dt>Distance</dt><dd>${c.dist != null ? `about ${num(c.dist)} road miles` : 'n/a'}</dd></div></dl></div>
  </aside></div></div>`;
  paintGallery();
  window.scrollTo(0, 0);
}

/* ---------------- compare ---------------- */
function viewCompare() {
  setNav('compare');
  const cars = [...S.cmp].map(k => S.idx.vehicles.find(v => v.key === k)).filter(Boolean);
  if (!cars.length) { $('#app').innerHTML = `<div class="page"><h1>Compare</h1><p>Nothing selected yet. Use the Compare button on any car to line up as many as four side by side.</p><a class="btn" href="#/">Browse inventory</a></div>`; return; }
  const best = (vals, lowest = true) => { const xs = vals.filter(v => v != null); if (xs.length < 2) return null; return lowest ? Math.min(...xs) : Math.max(...xs); };
  const row = (label, fn, opt = {}) => { const vals = cars.map(fn); const b = opt.rank ? best(cars.map(opt.rank), opt.lowest !== false) : null;
    return `<tr><th scope="row">${label}</th>${cars.map((c, i) => `<td class="${opt.rank && b != null && opt.rank(c) === b ? 'best' : ''}">${vals[i]}</td>`).join('')}</tr>`; };
  $('#app').innerHTML = `<div class="page"><h1>Compare</h1><p>The strongest value in each row is marked. Deal quality and preference match are separate judgements.</p>
  <div class="tablewrap"><table class="cmp"><tbody>
  ${row('', c => `<a href="#/v/${encodeURIComponent(c.key)}">${c.thumbs?.[0] ? `<img src="${esc(src(c.thumbs[0], 640))}" alt="${esc(title(c))}">` : ''}<b>${esc(title(c))}</b></a><br><button class="linkbtn" data-cmp data-key="${esc(c.key)}">Remove</button>`)}
  ${row('Status', c => `${CAT_LABEL[c.cat]}<br><span class="muted">${esc(c.why)}</span>`)}
  ${row('Asking price', c => `<span class="num">${money(c.price)}</span>`, { rank: c => c.price })}
  ${row('Out the door', c => money(otdFor(c)?.total), { rank: c => otdFor(c)?.total })}
  ${row('Preference match', c => `<span class="num">${c.score}</span> / 100`, { rank: c => c.score, lowest: false })}
  ${row('Deal quality', c => esc(dealText(c.deal)) + (c.deal.n != null ? `<br><span class="muted">${c.deal.n} comps, ${esc(c.deal.conf || '')}</span>` : ''), { rank: c => c.deal.delta })}
  ${row('Mileage', c => num(c.miles) + ' mi', { rank: c => c.miles })}
  ${row('Year / generation', c => `${c.year} / ${esc(c.gen || '')}`)}
  ${row('Transmission', c => TRANS[c.trans] || 'n/a')}
  ${row('Exterior', c => esc(c.ext || 'n/a'))}${row('Interior', c => esc(c.int || 'n/a'))}
  ${row('Front lift', c => liftChip(c))}
  ${row('Seats', c => c.seats ? (c.seats === 'bucket' ? 'Bucket' : 'Adjustable sport') : 'Not documented')}
  ${row('Weissach', c => c.weissach === true ? 'Yes' : c.weissach === false ? 'No' : 'Unknown')}
  ${row('Brakes', c => c.pccb === true ? 'PCCB' : c.pccb === false ? 'Steel' : 'Unknown')}
  ${row('CPO', c => c.cpo ? 'Porsche Approved' : 'No')}
  ${row('Location', c => `${esc(c.seller || '')}<br>${esc([c.city, c.state].filter(Boolean).join(', '))}`)}
  ${row('Distance', c => c.dist != null ? num(c.dist) + ' mi' : 'n/a', { rank: c => c.dist })}
  ${row('Days on market', c => c.dom ?? 'n/a')}
  ${row('Photos', c => `${c.nphotos}${c.gcomplete ? '' : ` of ${c.nadv}`}`)}
  </tbody></table></div></div>`;
}

/* ---------------- market ---------------- */
function niceTicks(lo, hi, n = 5) { const span = hi - lo || 1, step0 = Math.pow(10, Math.floor(Math.log10(span / n))), step = [1, 2, 2.5, 5, 10].map(m => m * step0).find(s => span / s <= n) || step0 * 10; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v); return out; }
function scatter(points, sales, slope) {
  const act = points.filter(p => p.status === 'active' || p.status === 'unverified'), gone = points.filter(p => p.status === 'removed' || p.status === 'sold');
  const sl = sales.filter(s => s.mileage != null);
  const all = [...points.map(p => [p.miles, p.price]), ...sl.map(s => [s.mileage, s.sale_price])];
  if (!all.length) return '<p class="note">No priced listings for this model yet.</p>';
  const W = 900, H = 420, L = 78, R = 20, T = 14, B = 44;
  const xs = all.map(a => a[0]), ys = all.map(a => a[1]);
  const x1 = Math.max(2000, Math.max(...xs) * 1.08), ylo = Math.min(...ys), yhi = Math.max(...ys), yp = Math.max(4000, (yhi - ylo) * 0.12), y0 = ylo - yp, y1 = yhi + yp;
  const X = v => L + (W - L - R) * v / x1, Y = v => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  const xt = niceTicks(0, x1, 6), yt = niceTicks(y0, y1, 6);
  let trend = '';
  if (slope != null && slope < 0 && act.length) { const mx = [...act.map(p => p.miles)].sort((a, b) => a - b)[Math.floor(act.length / 2)], my = [...act.map(p => p.price)].sort((a, b) => a - b)[Math.floor(act.length / 2)];
    const a = Math.min(...xs), b = Math.max(...xs); trend = `<line x1="${X(a)}" y1="${Y(my + slope * (a - mx))}" x2="${X(b)}" y2="${Y(my + slope * (b - mx))}" stroke="var(--ink-3)" stroke-width="1.5" stroke-dasharray="5 4"/><text class="lbl" x="${W - R}" y="${T + 10}" text-anchor="end">dashed line: mileage trend, ${money(slope * 1000)} per 1,000 mi</text>`; }
  const tipFor = p => `<b>${esc(p.label)}</b>${money(p.price)} asking, ${num(p.miles)} mi<br>${esc(p.state || '')}${p.cpo ? ', CPO' : ''}${p.manual === true ? ', manual' : p.manual === false ? ', PDK' : ''}<br>${esc(CAT_LABEL[p.cat] || '')}`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Asking price against mileage">
  <g class="grid">${yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>
  <g class="axis"><line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${yt.map(v => `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${money(v)}</text>`).join('')}${xt.map(v => `<text x="${X(v)}" y="${H - B + 18}" text-anchor="middle">${num(v)}</text>`).join('')}<text x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">Odometer miles</text></g>${trend}
  ${gone.map(p => `<circle cx="${X(p.miles)}" cy="${Y(p.price)}" r="5" fill="var(--white)" stroke="var(--ink-3)" stroke-width="1.5" data-tip="${tipFor(p)}<br>Delisted: last asking price, not a sale" data-go="${esc(p.key)}"/>`).join('')}
  ${act.map(p => `<circle cx="${X(p.miles)}" cy="${Y(p.price)}" r="${p.cat === 'confirmed' || p.cat === 'lead' ? 6.5 : 5}" fill="var(--series-1)" fill-opacity="${p.cat === 'confirmed' || p.cat === 'lead' ? 1 : 0.45}" stroke="var(--white)" stroke-width="2" data-tip="${tipFor(p)}" data-go="${esc(p.key)}" style="cursor:pointer"/>`).join('')}
  ${sl.map(s => { const x = X(s.mileage), y = Y(s.sale_price); return `<path d="M${x},${y - 7}L${x + 7},${y}L${x},${y + 7}L${x - 7},${y}Z" fill="var(--series-2)" stroke="var(--white)" stroke-width="2" data-tip="<b>${esc(s.title || 'Completed sale')}</b>Sold ${money(s.sale_price)} on ${day(s.sale_date)}<br>${num(s.mileage)} mi, ${esc(s.venue || '')}"/>`; }).join('')}</svg>
  <div class="legend"><span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.5" fill="var(--series-1)"/></svg>For sale, qualifying</span><span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="var(--series-1)" fill-opacity=".45"/></svg>For sale, misses a requirement</span>
  <span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--ink-3)" stroke-width="1.5"/></svg>Delisted, last asking</span><span><svg viewBox="0 0 14 14"><path d="M7,1L13,7L7,13L1,7Z" fill="var(--series-2)"/></svg>Completed sale (realised price)</span></div>`;
}
function yearStrips(points) {
  const act = points.filter(p => p.status === 'active' || p.status === 'unverified'); if (!act.length) return '';
  const years = [...new Set(act.map(p => p.year))].sort(), W = 900, L = 78, R = 20, rowH = 40, H = years.length * rowH + 34;
  const lo = Math.min(...act.map(p => p.price)), hi = Math.max(...act.map(p => p.price)), pad = Math.max(4000, (hi - lo) * 0.08), x0 = lo - pad, x1 = hi + pad;
  const X = v => L + (W - L - R) * (v - x0) / (x1 - x0), ticks = niceTicks(x0, x1, 6);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Asking prices by model year"><g class="grid">${ticks.map(v => `<line x1="${X(v)}" x2="${X(v)}" y1="4" y2="${H - 28}"/>`).join('')}</g>
  <g class="axis">${ticks.map(v => `<text x="${X(v)}" y="${H - 10}" text-anchor="middle">${money(v)}</text>`).join('')}</g>
  ${years.map((y, i) => { const ps = act.filter(p => p.year === y).sort((a, b) => a.price - b.price), cy = 4 + i * rowH + rowH / 2, med = ps[Math.floor(ps.length / 2)].price;
    return `<text class="lbl" x="${L - 10}" y="${cy + 4}" text-anchor="end" style="font-weight:700;fill:var(--ink)">${y}</text><text class="lbl" x="6" y="${cy + 4}">n=${ps.length}</text><line x1="${L}" x2="${W - R}" y1="${cy}" y2="${cy}" stroke="var(--rule)"/>
    <line x1="${X(med)}" x2="${X(med)}" y1="${cy - 13}" y2="${cy + 13}" stroke="var(--ink)" stroke-width="2"/>${ps.map(p => `<circle cx="${X(p.price)}" cy="${cy}" r="5" fill="var(--series-1)" fill-opacity=".8" stroke="var(--white)" stroke-width="2" data-tip="<b>${esc(p.label)}</b>${money(p.price)}, ${num(p.miles)} mi" data-go="${esc(p.key)}" style="cursor:pointer"/>`).join('')}`; }).join('')}</svg>
  <p class="note">Each dot is one car for sale; the black tick is that year's median asking price.</p>`;
}
function trendLine(snaps, field, label, fmt) {
  const pts = snaps.filter(s => s[field] != null);
  if (pts.length < 2) return `<p class="note">${pts.length ? `Tracking started ${day(pts[0].day)}: ${fmt(pts[0][field])} ${label}. A trend line appears once several daily observations exist. History before the first run is not available from any permitted source.` : 'No observations yet.'}</p>`;
  const W = 900, H = 220, L = 78, R = 60, T = 14, B = 28, t = s => +new Date(s.day), t0 = t(pts[0]), t1 = t(pts[pts.length - 1]), ys = pts.map(s => s[field]);
  const lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.2 || Math.max(1, hi * 0.05), y0 = Math.max(0, lo - pad), y1 = hi + pad;
  const X = v => L + (W - L - R) * (v - t0) / Math.max(1, t1 - t0), Y = v => T + (H - T - B) * (1 - (v - y0) / (y1 - y0)), yt = niceTicks(y0, y1, 4);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} over time"><g class="grid">${yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>
  <g class="axis">${yt.map(v => `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${fmt(v)}</text>`).join('')}<text x="${L}" y="${H - 6}">${day(pts[0].day)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${day(pts[pts.length - 1].day)}</text></g>
  <path d="M${pts.map(s => `${X(t(s))},${Y(s[field])}`).join('L')}" fill="none" stroke="var(--series-1)" stroke-width="2"/>
  ${pts.map(s => `<circle cx="${X(t(s))}" cy="${Y(s[field])}" r="${pts.length > 40 ? 0 : 4}" fill="var(--series-1)" stroke="var(--white)" stroke-width="2"/><rect x="${X(t(s)) - 8}" y="${T}" width="16" height="${H - T - B}" fill="transparent" data-tip="<b>${day(s.day)}</b>${fmt(s[field])} ${esc(label)}"/>`).join('')}
  <text class="lbl" x="${X(t1) + 8}" y="${Y(pts[pts.length - 1][field]) + 4}">${fmt(pts[pts.length - 1][field])}</text></svg>`;
}
async function viewMarket() {
  setNav('market');
  $('#app').innerHTML = '<p class="loading">Loading market data</p>';
  const mk = S.market ||= await getJSON('data/market.json');
  const keys = [...new Set([...mk.points.map(p => `${p.variant}|${p.gen}`), ...mk.sales.filter(s => s.variant).map(s => `${s.variant}|${s.generation}`)])].sort();
  if (!keys.length) { $('#app').innerHTML = '<div class="page"><h1>Market</h1><p>No priced listings have been collected yet.</p></div>'; return; }
  if (!keys.includes(S.group)) S.group = keys[0];
  const [variant, gen] = S.group.split('|'), label = k => { const [v, g] = k.split('|'); return `${S.idx.variants[v]?.label || v}${g && g !== '982' ? ' ' + g : ''}`; };
  const pts = mk.points.filter(p => p.variant === variant && p.gen === gen), sales = mk.sales.filter(s => s.variant === variant && s.generation === gen);
  const g = mk.groups[S.group] || { n: 0 }, snaps = mk.snapshots.filter(s => s.variant === variant && s.generation === gen);
  const act = pts.filter(p => p.status === 'active' || p.status === 'unverified'), dropped = mk.price_changes.filter(c => pts.some(p => p.key === c.key));
  const cuts = dropped.filter(c => c.delta < 0), medCut = cuts.length ? cuts.map(c => c.delta).sort((a, b) => a - b)[Math.floor(cuts.length / 2)] : null;
  const dom = act.map(p => p.dom).filter(v => v != null).sort((a, b) => a - b);
  const thin = g.n < 8;
  $('#app').innerHTML = `<div class="page"><h1>Market</h1><p>Asking prices come from current and recently delisted listings. Completed sales are realised prices from public auction records and are always shown separately. Sample sizes are stated wherever a number could be mistaken for more than it is.</p>
  <div class="seg" role="group" aria-label="Model">${keys.map(k => `<button aria-pressed="${k === S.group}" data-group="${k}">${esc(label(k))}</button>`).join('')}</div>
  <div class="stats"><div class="stat"><b>${g.n}</b><span>for sale with a price</span></div><div class="stat"><b>${g.n ? money(g.median_ask) : 'n/a'}</b><span>median asking price</span></div>
    <div class="stat"><b>${g.n >= 4 ? money(g.p25) + ' to ' + money(g.p75) : 'n/a'}</b><span>middle half of asking prices</span></div><div class="stat"><b>${g.n ? num(g.median_miles) : 'n/a'}</b><span>median miles</span></div>
    <div class="stat"><b>${dom.length ? dom[Math.floor(dom.length / 2)] : 'n/a'}</b><span>median days on market</span></div><div class="stat"><b>${sales.length}</b><span>completed sales on record</span></div></div>
  ${thin ? `<p class="note" style="margin-bottom:18px"><b>Thin data:</b> only ${g.n} car${g.n === 1 ? ' of this model is' : 's of this model are'} tracked so far. No mileage trend or fair-value range is computed below 8 and 5 comparables respectively.</p>` : ''}
  <div class="viz"><h3>Asking price by mileage</h3><p>Every tracked ${esc(label(S.group))}. Select a dot to open the car.</p>${scatter(pts, sales, g.slope_per_mile)}</div>
  <details class="tbl"><summary>View as table (${pts.length} listings)</summary><div class="tablewrap"><table><thead><tr><th>Car</th><th class="r">Asking</th><th class="r">Miles</th><th>State</th><th>Status</th><th>Category</th></tr></thead><tbody>${pts.slice().sort((a, b) => a.price - b.price).map(p => `<tr><td><a href="#/v/${encodeURIComponent(p.key)}">${esc(p.label)}</a></td><td class="r">${money(p.price)}</td><td class="r">${num(p.miles)}</td><td>${esc(p.state || '')}</td><td>${esc(p.status)}</td><td>${esc(CAT_LABEL[p.cat] || '')}</td></tr>`).join('')}</tbody></table></div></details>
  <div class="viz"><h3>Asking prices by model year</h3>${yearStrips(pts)}</div>
  <div class="viz" style="margin-top:20px"><h3>Cars for sale over time</h3><p>Daily count of tracked ${esc(label(S.group))} listings.</p>${trendLine(snaps, 'active_count', 'for sale', v => num(v))}</div>
  <div class="viz" style="margin-top:20px"><h3>Median asking price over time</h3>${trendLine(snaps, 'median_ask', 'median asking', money)}</div>
  <section class="sect"><h2>What moves the asking price <small>mileage-adjusted medians, not controlled estimates</small></h2><div class="tablewrap"><table><thead><tr><th>Attribute</th><th class="r">Cars with</th><th class="r">Cars without</th><th class="r">Median difference</th><th>Note</th></tr></thead><tbody>
    <tr><td>Each additional 1,000 miles</td><td class="r" colspan="2">${g.n} car${g.n === 1 ? '' : 's'}</td><td class="r">${g.slope_per_mile != null ? money(g.slope_per_mile * 1000) : 'n/a'}</td><td>${g.slope_per_mile != null ? 'Theil-Sen slope across current listings' : 'needs 8 or more listings'}</td></tr>
    ${(g.effects || []).filter(e => !((variant === 'gt3' || variant === 'gt3_touring') && e.attr === 'weissach') && !((variant === 'gt4rs' || variant === 'spyder_rs') && e.attr === 'manual')).map(e => `<tr><td>${esc(e.label)}</td><td class="r">${e.n_with}</td><td class="r">${e.n_without}</td><td class="r">${e.median_diff == null ? 'n/a' : (e.median_diff > 0 ? '+' : '') + money(e.median_diff)}</td><td>${esc(e.note)}</td></tr>`).join('')}</tbody></table></div></section>
  <section class="sect"><h2>Observed price changes <small>${dropped.length} on record${medCut != null ? `, median reduction ${money(Math.abs(medCut))}` : ''}</small></h2>${dropped.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Car</th><th class="r">From</th><th class="r">To</th><th class="r">Change</th></tr></thead><tbody>${dropped.slice(0, 40).map(c => { const p = pts.find(x => x.key === c.key); return `<tr><td>${day(c.at)}</td><td><a href="#/v/${encodeURIComponent(c.key)}">${esc(p?.label || c.key)}</a></td><td class="r">${money(c.from)}</td><td class="r">${money(c.to)}</td><td class="r">${c.delta < 0 ? '-' : '+'}${money(Math.abs(c.delta))}</td></tr>`; }).join('')}</tbody></table></div>` : '<p class="note">No price changes observed yet. Each daily run records any change in an advertised price.</p>'}</section>
  <section class="sect"><h2>Completed sales <small>realised prices with a public record</small></h2>${sales.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Car</th><th class="r">Sold for</th><th class="r">Miles</th><th>Venue</th><th></th></tr></thead><tbody>${sales.map(s => `<tr><td>${day(s.sale_date)}</td><td>${esc(s.title || '')}</td><td class="r"><b>${money(s.sale_price)}</b></td><td class="r">${num(s.mileage)}</td><td>${esc(s.venue || '')}</td><td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Record</a>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="note">No completed sales are recorded for this model. Auction sites are not crawled; add a documented sale to config/manual_sales.csv with its public URL and it appears here and in valuations.</p>'}</section>
  <section class="sect"><h2>How fair value is estimated</h2><div class="method">${esc(mk.methodology.valuation)}\n\n${esc(mk.methodology.asking_vs_sold)}</div></section></div>`;
}

/* ---------------- archive / sources ---------------- */
function viewArchive() {
  setNav('archive');
  const rows = S.idx.vehicles.filter(c => c.status === 'removed' || c.status === 'sold').sort((a, b) => (b.last_seen || '').localeCompare(a.last_seen || ''));
  $('#app').innerHTML = `<div class="page"><h1>Archive</h1><p>Cars that are no longer listed. Records, photos and price history are kept. A delisted car shows its last asking price; a sale price appears only when a public record documents it.</p>
  ${rows.length ? `<div class="grid" id="grid">${rows.map(c => cardHTML(c, true)).join('')}</div>` : '<div class="empty"><h2>Nothing archived yet</h2><p>A listing moves here after the source confirms on two separate days that it is gone. A source outage never archives anything.</p></div>'}</div>`;
}
async function viewSources() {
  setNav('sources');
  $('#app').innerHTML = '<p class="loading">Loading source health</p>';
  const h = S.health ||= await getJSON('data/health.json');
  const cls = s => /^not/.test(s) ? 'not' : /manual/.test(s) ? 'manual' : '';
  $('#app').innerHTML = `<div class="page"><h1>Sources</h1><p>What was collected, when, and what is missing. A failed run leaves stored inventory untouched and is shown here instead of being hidden.</p>
  <section class="sect"><h2>Coverage</h2><div class="tablewrap"><table><thead><tr><th>Source</th><th>Coverage</th><th>Last successful refresh</th><th>Notes</th></tr></thead><tbody>${h.coverage.map(c => { const k = c.source === 'Porsche Finder' ? 'porsche_finder' : /MarketCheck/.test(c.source) ? 'marketcheck' : null; return `<tr><td><b>${esc(c.source)}</b></td><td><span class="status ${cls(c.state)}">${esc(c.state)}</span></td><td>${k ? (h.last_success[k] ? new Date(h.last_success[k]).toLocaleString() : 'never') : 'n/a'}</td><td>${esc(c.note)}</td></tr>`; }).join('')}</tbody></table></div></section>
  <section class="sect"><h2>Photo capture <small>${num(h.gallery.captured)} of ${num(h.gallery.advertised)} advertised photos captured for listed cars</small></h2>${h.gallery.incomplete.length ? `<div class="tablewrap"><table><thead><tr><th>Car</th><th class="r">Captured</th><th class="r">Advertised</th></tr></thead><tbody>${h.gallery.incomplete.map(g => `<tr><td><a href="#/v/${encodeURIComponent(g.key)}">${esc(g.label)}</a></td><td class="r">${g.captured}</td><td class="r">${g.advertised ?? 'n/a'}</td></tr>`).join('')}</tbody></table></div>` : '<p class="note">Every listed car has its complete advertised gallery.</p>'}</section>
  <section class="sect"><h2>Recent runs</h2><div class="tablewrap"><table><thead><tr><th>Started</th><th>Run</th><th>Source</th><th>Status</th><th class="r">Listings</th><th class="r">New</th><th class="r">Requests</th><th class="r">Errors</th><th>Coverage</th><th>Messages</th></tr></thead><tbody>
  ${h.runs.map(r => r.sources.map((s, i) => `<tr>${i ? '' : `<td rowspan="${r.sources.length}">${new Date(r.started_at).toLocaleString()}</td><td rowspan="${r.sources.length}"><span class="status ${r.status}">${r.status}</span>${r.baseline ? '<br><span class="muted">initial backfill</span>' : ''}</td>`}<td>${esc(s.source)}</td><td><span class="status ${s.status}">${s.status}</span></td><td class="r">${s.listings_seen}</td><td class="r">${s.listings_new}</td><td class="r">${s.requests}</td><td class="r">${s.errors}</td><td>${s.scopes_ok} of ${s.scopes_total}</td><td>${esc((s.detail.messages || []).slice(0, 3).join('; '))}${(s.detail.messages || []).length > 3 ? ` (+${s.detail.messages.length - 3} more)` : ''}</td></tr>`).join('') || `<tr><td>${new Date(r.started_at).toLocaleString()}</td><td><span class="status ${r.status}">${r.status}</span></td><td colspan="8">no sources ran</td></tr>`).join('')}</tbody></table></div></section>
  <section class="sect"><h2>Email digests <small>at most one per day, only when new qualifying cars appear</small></h2>${h.digests.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Status</th><th class="r">Cars</th><th>Sent</th><th>Channel</th><th>Error</th></tr></thead><tbody>${h.digests.map(d => `<tr><td>${esc(d.date)}</td><td><span class="status ${d.status}">${d.status}</span></td><td class="r">${d.vehicles}</td><td>${d.sent_at ? new Date(d.sent_at).toLocaleString() : ''}</td><td>${esc(d.channel || '')}</td><td>${esc(d.error || '')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="note">No digest has been needed yet. Cars present at the first run are treated as existing inventory, not as new.</p>'}</section></div>`;
}

/* ---------------- assumptions dialog ---------------- */
function openOTD() {
  const a = A(), d = $('#otdDialog');
  const fld = (k, l, step) => `<label>${l}<input type="number" step="${step}" name="${k}" value="${a[k]}"></label>`;
  d.innerHTML = `<form method="dialog"><h2>Out-the-door assumptions</h2><p class="note">Applied to every car. Dealer fees published in a listing always override the defaults.</p>
  ${fld('sales_tax_rate', 'Texas motor vehicle tax rate', '0.0001')}${fld('default_doc_fee_tx', 'Dealer fees, Texas dealer ($)', '1')}${fld('default_doc_fee_other', 'Dealer fees, out of state ($)', '1')}
  ${fld('title_fee', 'Title fee ($)', '0.01')}${fld('registration_fee', 'Registration ($)', '0.01')}${fld('county_fee', 'County fee ($)', '0.01')}${fld('state_misc_fees', 'Other state fees ($)', '0.01')}
  ${fld('drive_radius_miles', 'Drive it home within (miles)', '10')}${fld('ship_base', 'Transport base ($)', '10')}${fld('ship_rate_short', 'Transport $/mile, first 500', '0.05')}${fld('ship_rate_mid', 'Transport $/mile, 500 to 1,500', '0.05')}${fld('ship_rate_long', 'Transport $/mile, beyond 1,500', '0.05')}
  <div class="btnrow"><button class="btn" value="save">Apply</button><button class="btn alt" value="reset">Reset to defaults</button><button class="btn alt" value="cancel">Cancel</button></div></form>`;
  d.showModal();
  d.onclose = () => { if (d.returnValue === 'save') { S.otd = Object.fromEntries([...new FormData($('form', d))].map(([k, v]) => [k, Number(v)]).filter(([, v]) => Number.isFinite(v))); } else if (d.returnValue === 'reset') S.otd = {}; else return; store.set('otd', S.otd); route(); };
}

/* ---------------- routing and events ---------------- */
function route() {
  const h = location.hash.replace(/^#\/?/, ''); S.gal = null; $('#lightbox').hidden = true; $('#tip').hidden = true;
  if (h.startsWith('v/')) return viewDetail(decodeURIComponent(h.slice(2)));
  if (h === 'compare') return viewCompare();
  if (h === 'market') return viewMarket().catch(showError);
  if (h === 'archive') return viewArchive();
  if (h === 'sources') return viewSources().catch(showError);
  return viewInventory();
}
function showError(e) { $('#app').innerHTML = `<div class="page"><h1>Could not load data</h1><p>${esc(e.message)}. The site shows stored data only; check that the daily run has published.</p></div>`; }
document.addEventListener('click', e => {
  const t = e.target.closest('button, [data-go], [data-main]'); if (!t) return;
  if (t.dataset.tab) { S.tab = t.dataset.tab; store.set('tab', S.tab); return viewInventory(); }
  if (t.id === 'clearF') { S.f = {}; store.set('filters', S.f); return viewInventory(); }
  if (t.id === 'fToggle') return $('#filters').classList.toggle('open');
  if (t.id === 'otdBtn') return openOTD();
  if (t.dataset.sw) { const a = new Set(S.f[t.dataset.sw] || []); a.has(t.value) ? a.delete(t.value) : a.add(t.value); S.f[t.dataset.sw] = [...a]; store.set('filters', S.f); return viewInventory(); }
  if ('fav' in t.dataset || 'cmp' in t.dataset) {
    const key = t.dataset.key || t.closest('[data-key]')?.dataset.key; if (!key) return;
    if ('cmp' in t.dataset && !S.cmp.has(key) && S.cmp.size >= 4) { t.textContent = 'Limit is 4'; return; }
    'fav' in t.dataset ? toggleSet(S.favs, key, 'favs') : toggleSet(S.cmp, key, 'cmp');
    return route();
  }
  if (t.dataset.group) { S.group = t.dataset.group; return viewMarket(); }
  if (t.dataset.step) return galStep(Number(t.dataset.step));
  if (t.dataset.go != null && t.closest('.thumbs')) { S.gal.i = Number(t.dataset.go); return paintGallery(); }
  if (t.dataset.jump != null) { S.gal.i = Number(t.dataset.jump); return paintGallery(); }
  if ('full' in t.dataset) return galFull(!S.gal.open);
  if ('main' in t.dataset && S.gal && !S.gal.open) return galFull(true);
  if (t.dataset.go && t.closest('svg')) location.hash = '#/v/' + encodeURIComponent(t.dataset.go);
});
document.addEventListener('input', e => { if (e.target.closest('#filters') && e.target.type !== 'checkbox' && e.target.tagName !== 'SELECT') onInventoryInput(e); });
document.addEventListener('change', e => { if ((e.target.closest('#filters') && (e.target.type === 'checkbox' || e.target.tagName === 'SELECT')) || e.target.id === 'sort') onInventoryInput(e); });
document.addEventListener('keydown', e => {
  if (!S.gal || e.target.matches('input, select, textarea')) return;
  if (e.key === 'ArrowRight') galStep(1); else if (e.key === 'ArrowLeft') galStep(-1);
  else if (e.key === 'Escape' && S.gal.open) galFull(false); else if (e.key.toLowerCase() === 'f') galFull(!S.gal.open);
});
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && S.gal?.open) { S.gal.open = false; paintGallery(); } });
let sx = null;
document.addEventListener('pointerdown', e => { if (e.target.closest('.gal-main')) sx = e.clientX; });
document.addEventListener('pointerup', e => { if (sx != null && e.target.closest('.gal-main, .lightbox')) { const dx = e.clientX - sx; if (Math.abs(dx) > 40) galStep(dx < 0 ? 1 : -1); } sx = null; });
document.addEventListener('mousemove', e => {
  const tip = $('#tip'), m = e.target.closest?.('[data-tip]');
  if (m) { tip.hidden = false; tip.innerHTML = m.dataset.tip; const w = tip.offsetWidth, x = Math.min(window.innerWidth - w - 8, e.clientX + 14); tip.style.left = Math.max(8, x) + 'px'; tip.style.top = Math.max(8, e.clientY - tip.offsetHeight - 12) + 'px'; }
  else if (!tip.hidden) tip.hidden = true;
  const card = e.target.closest?.('.card'); if (!card) return;
  const shot = $('.shot', card), img = $('img', shot); if (!img) return;
  const r = shot.getBoundingClientRect(); if (e.clientY > r.bottom || e.clientY < r.top) return;
  const c = S.idx.vehicles.find(v => v.key === card.dataset.key), t = c?.thumbs || []; if (t.length < 2) return;
  const i = Math.max(0, Math.min(t.length - 1, Math.floor((e.clientX - r.left) / r.width * t.length)));
  if (String(i) !== img.dataset.i) { img.dataset.i = i; img.src = src(t[i], 640); $$('.ticks b', shot).forEach((b, k) => b.classList.toggle('on', k === i)); }
});
document.addEventListener('error', e => {
  const img = e.target; if (img.tagName !== 'IMG') return;
  if (img.dataset.a && !img.dataset.tried) { img.dataset.tried = 1; img.src = 'data/' + img.dataset.a; return; }
  const box = img.closest('.shot, .gal-main'); if (box && !$('.noimg', box)) { img.style.visibility = 'hidden'; box.insertAdjacentHTML('beforeend', '<div class="noimg">This photo is no longer served by the listing site.</div>'); }
}, true);
window.addEventListener('hashchange', route);
(async function boot() {
  try { S.idx = await getJSON('data/index.json'); setFresh(); route(); }
  catch (e) { $('#fresh').textContent = 'No data'; showError(e); }
})();
