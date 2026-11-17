// GT Scout: Porsche Finder capture, run in a browser tab on finder.porsche.com (see docs/SOURCES.md).
// The parser below is the code that produced the 2026-11-14 capture.

window.A = v => Array.isArray(v) ? v : [];

window.__pfMatch = function(s, start){ let depth=0, inStr=false, esc=false; for(let i=start;i<s.length;i++){ const c=s[i]; if(inStr){ if(esc) esc=false; else if(c==='\\') esc=true; else if(c==='"') inStr=false; continue; } if(c==='"') inStr=true; else if(c==='{'||c==='[') depth++; else if(c==='}'||c===']'){ depth--; if(depth===0) return i+1; } } return -1; };

window.__pfParse = function(html){
  const chunks=[...html.matchAll(/self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g)].map(m=>{try{return JSON.parse(m[1])}catch(e){return ''}}); const rsc=chunks.join('');
  let main=null, dl={}, p=-1; while((p=rsc.indexOf('{"listingId"',p+1))>=0){ const e=window.__pfMatch(rsc,p); if(e<0) continue; try{ const x=JSON.parse(rsc.slice(p,e)); if(x.sections&&x.meta) main=x; else if(x.modelCode||x.realcarStatus) dl=x; }catch(err){} }
  if(!main) return null;
  const U=v=>(v==='$undefined'||v==null)?null:(typeof v==='string'&&v.startsWith('$$')?v.slice(1):v);
  const ref=v=>{ if(typeof v==='string'&&/^\$[0-9a-f]{1,4}$/.test(v)){ const m=rsc.match(new RegExp('(?:^|\\n)'+v.slice(1)+':T([0-9a-f]+),')); if(!m) return null; const n=parseInt(m[1],16); const bytes=new TextEncoder().encode(rsc.slice(m.index+m[0].length, m.index+m[0].length+n)); return new TextDecoder().decode(bytes.slice(0,n)); } return U(v); };
  const m=main.meta, s=main.sections, info=s.info||{}, eq=s.equipment||{}, sel=m.seller||info.seller||{}, comp=sel.addressComponents||{};
  const money=t=>{ if(typeof t==='number') return t; const x=String(t||'').match(/-?[\d,]+(?:\.\d+)?/); return x?parseFloat(x[0].replace(/,/g,'')):null; };
  const fees=[]; let vp=null; for(const it of A((U(info.overviewBreakdown)||{}).items)){ const lab=(U(it.label)||'').trim(), val=money(U(it.value)); if(val==null) continue; if(/vehicle price/i.test(lab)) vp=val; else fees.push([lab,val]); }
  const imgs={}; for(const [k,b] of Object.entries((s.mediaCenter||{}).images||{})) imgs[k]=A((b||{}).images).map(i=>i.id||i).filter(Boolean);
  const mil=m.mileage||{};
  return {id:main.listingId, listed:main.listed, url:U(m.detailsUrl), vin:U(m.vin)||U(info.vin), yr:U(m.modelYear), cat:U(m.modelCategory)||U(m.title), mdl:U(m.model), cond:U(m.condition)||U(info.condition),
    pa:!!(info.porscheApproved||(s.conditionAndHistory||{}).porscheApproved), pv:money(U(m.priceValue))??money(U(info.price)), vp, fees, mi:typeof mil.value==='number'?mil.value:null, miu:mil.unitCode,
    ext:U((s.summary||{}).exteriorColor&&s.summary.exteriorColor.name)||U(m.color), hex:U((s.summary||{}).exteriorColor&&s.summary.exteriorColor.value), int:U((s.summary||{}).interior&&s.summary.interior.name)||U(m.interiorName), tr:U(m.transmission),
    own:typeof m.numberOfPreviousOwners==='number'?m.numberOfPreviousOwners:null, af:typeof m.accidentFree==='boolean'?m.accidentFree:null, stock:U(info.stockNumber), com:U(info.commissionNumber),
    sid:U(sel.sellerId), sname:U(sel.name), street:U(comp.street), city:U(comp.city), zip:U(comp.postalCode), fcity:U(sel.formattedCity), pn:U(sel.porschePartnerNumber),
    opts:A(eq.specialEquipment).flatMap(g=>A(g.items).filter(i=>i.name).map(i=>[U(i.code),i.name,g.name])),
    std:A(eq.standardEquipment).flatMap(g=>A(g.items).map(i=>i.name||i)).filter(n=>n&&n.length<200),
    hl:A(eq.importantEquipmentOptions).map(o=>o.value).filter(Boolean), comment:ref((s.sellerComment||{}).sellerComment),
    svc:(A((s.conditionAndHistory||{}).history).find(h=>/service history/i.test(h.title||''))||{}).value||null,
    warr:A((s.warrantyDetails||{}).warranties).map(w=>[(w.warrantyType||{}).value,(w.warrantyType||{}).label,U(w.duration)]),
    docs:[!!(s.importantResources||{}).monroneyLabelAvailable, !!(s.importantResources||{}).buildsheetAvailable], imgs, adv:(s.gallery||{}).totalNumberOfImages??null, real:((s.mediaCenter||{}).imagesType||(s.gallery||{}).imagesType)==='real',
    mc:U(dl.modelCode), ecc:U(dl.exteriorColorCode), inv:main.inventoryId};
};


// ---- discovery, capture loop and save ---------------------------------------------------------
// Run from any finder.porsche.com page:  __pfStart()  then poll  __pfStatus()  until running is false,
// then  __pfSave()  to download the capture file.  Reads only /sitemap.xml, the per-dealer sitemaps and the
// listing pages whose URL names a tracked model (all allowed by the site's robots.txt), two at a time.
window.sleep = ms => new Promise(r => setTimeout(r, ms));

window.__pfStart = function () {
  const TARGET = /^https:\/\/finder\.porsche\.com\/us\/en-US\/details\/(porsche-911-gt3|porsche-911-gt3-with-touring-package|porsche-718-cayman-gt4-rs|porsche-718-spyder-rs)-(new|preowned)-([A-Z0-9]+)$/;
  const PF = window.__pf = {started: new Date().toISOString(), dealers: [], ok: [], failed: [], targets: {}, total: 0, rec: {}, gone: [], bad: [], running: true, phase: 'sitemaps', error: null};
  (async () => {
    try {
      const idx = await fetch('/sitemap.xml', {credentials: 'omit'});
      if (idx.status !== 200) throw new Error('sitemap index HTTP ' + idx.status);
      PF.dealers = [...new Set([...(await idx.text()).matchAll(/https:\/\/finder\.porsche\.com\/us\/en-US\/sitemap-dealer\/(\d+)\/sitemap\.xml/g)].map(m => m[1]))];
      if (!PF.dealers.length) throw new Error('no dealer sitemaps in the index');
      let i = 0;
      const readSitemaps = async () => {
        while (i < PF.dealers.length) {
          const d = PF.dealers[i++]; let done = false;
          for (let attempt = 0; attempt < 3 && !done; attempt++) {
            try {
              const r = await fetch('/us/en-US/sitemap-dealer/' + d + '/sitemap.xml', {credentials: 'omit'});
              if (r.status === 200) {
                const locs = [...(await r.text()).matchAll(/<loc>([^<]+)<\/loc>/g)].map(m => m[1].trim());
                PF.total += locs.length;
                for (const u of locs) { const m = TARGET.exec(u); if (m) PF.targets[m[3]] = {u, slug: m[1], cond: m[2], d}; }
                PF.ok.push(d); done = true;
              } else await sleep(1500);
            } catch (e) { await sleep(1500); }
          }
          if (!done) PF.failed.push(d);
          await sleep(300);
        }
      };
      await Promise.all([readSitemaps(), readSitemaps()]);
      PF.phase = 'listings';
      for (let round = 0; round < 4; round++) {
        const todo = Object.entries(PF.targets).filter(([id]) => !PF.rec[id] && !PF.gone.includes(id));
        if (!todo.length) break;
        let j = 0;
        const readListings = async () => {
          while (j < todo.length) {
            const [id, t] = todo[j++];
            try {
              const r = await fetch(t.u, {credentials: 'omit'});
              if (r.status === 404 || r.status === 410) PF.gone.push(id);
              else if (r.status === 200) {
                const rec = window.__pfParse(await r.text());
                if (rec) { rec.d = t.d; rec.slug = t.slug; PF.rec[id] = rec; } else PF.bad.push(id + ':noparse');
              } else PF.bad.push(id + ':' + r.status);
            } catch (e) { PF.bad.push(id + ':err'); }
            await sleep(600);
          }
        };
        await Promise.all([readListings(), readListings()]);
        if (round < 3) await sleep(3000);
      }
      PF.phase = 'done';
    } catch (e) { PF.error = String(e); PF.phase = 'failed'; }
    PF.running = false;
  })();
  return 'started';
};

window.__pfStatus = function () {
  const PF = window.__pf; if (!PF) return {started: false};
  const ids = Object.keys(PF.targets);
  return {phase: PF.phase, running: PF.running, error: PF.error, dealers: PF.dealers.length, dealers_ok: PF.ok.length,
    dealers_failed: PF.failed.length, urls: PF.total, targets: ids.length, captured: Object.keys(PF.rec).length,
    gone: PF.gone.length, left: ids.filter(id => !PF.rec[id] && !PF.gone.includes(id)).length, last_errors: PF.bad.slice(-3)};
};

window.__pfSave = function () {
  const PF = window.__pf; const ids = Object.keys(PF.targets);
  const cap = {format: 'full', captured_at: new Date().toISOString().slice(0, 19) + 'Z', dealers_total: PF.dealers.length,
    dealers_ok: PF.ok.map(String), dealers_failed: PF.failed.map(String), gone: PF.gone.map(String),
    failed: ids.filter(id => !PF.rec[id] && !PF.gone.includes(id)), listing_urls: PF.total, targets: ids.length,
    listings: Object.values(PF.rec)};
  const name = 'gt-scout-finder-capture-' + cap.captured_at.replace(/[-:]/g, '').replace('T', '-').replace('Z', '') + '.json';
  const blob = new Blob([JSON.stringify(cap)], {type: 'application/json'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  return {file: name, bytes: blob.size, listings: cap.listings.length, failed: cap.failed.length, gone: cap.gone.length,
    dealers_ok: cap.dealers_ok.length, dealers_total: cap.dealers_total};
};
