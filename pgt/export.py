"""Export the database to static JSON consumed by the website."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from . import classify as C
from . import db as D
from . import valuation

FINDER_BROWSER_NOTE = (
    "Read through the owner's own browser, not from the cloud: every US dealer sitemap and each matching listing page "
    "(VIN, transmission, factory option codes, CPO, fees). finder.porsche.com answers GitHub's servers with a bot-check "
    "page that is not bypassed, so the daily cloud run cannot refresh these listings; they are as of the capture time "
    "shown here until the next browser capture.")

COVERAGE_NOTES = [
    {"source": "Porsche Finder", "state": "not covered from the cloud",
     "note": "The adapter (dealer sitemaps + listing pages: VIN, factory option codes, CPO, full gallery) works on real "
             "pages, but finder.porsche.com answers GitHub's servers with a bot-check page (HTTP 429), and that check "
             "is not bypassed. Until a collector runs from a permitted network, factory option data is missing and "
             "front-lift status stays unverified."},
    {"source": "MarketCheck dealer feed", "state": "covered",
     "note": "Licensed API for nationwide used dealer inventory (Porsche dealers and independents), swept daily in "
             "three model-year buckets. It carries VIN, price, mileage, colours, CPO flag and photo links, but no "
             "factory option list, so front lift and accident history need the seller's documentation."},
    {"source": "MarketCheck VIN decode", "state": "covered for qualifying cars",
     "note": "Paid per-VIN lookup, run once for each car that passes every other hard requirement, within a monthly "
             "call budget. For most of these cars the decode is assembled from the dealer's listed options, not an OEM "
             "build record, so a front-lift option it finds counts as seller-documented and a decode without one proves "
             "nothing unless it lists ten or more options."},
    {"source": "Cars.com", "state": "not covered",
     "note": "Cars.com disallows automated access in robots.txt and offers no public inventory API. Dealer stock that "
             "appears there is reached through the MarketCheck feed instead."},
    {"source": "Autotrader / CarGurus", "state": "not covered directly",
     "note": "No automated access permitted to the sites themselves. An official CarGurus connector exists for Claude "
             "and can be used for manual cross-checks."},
    {"source": "Bring a Trailer / Cars & Bids / PCarMarket", "state": "manual",
     "note": "Completed-sale prices are entered in config/manual_sales.csv with a public URL per sale; the sites are "
             "not crawled."},
]

METHODOLOGY = {
    "valuation": valuation.__doc__.strip(),
    "asking_vs_sold": "Every price on this site is an advertised asking price unless it appears in the Sales table, "
                      "which only holds publicly documented completed transactions with a source link. The last asking "
                      "price of a delisted car is never presented as a sale price.",
    "score": "Preference match is a weighted 0-100 fit to stated preferences. Hard requirements decide the category "
             "(confirmed / lead / near miss / excluded) and never move the score. Deal quality is computed separately.",
}


def _photo_ref(p: dict) -> dict:
    u = p["url"]
    if C.is_finder_image(u):
        return {"k": "pf", "u": C.canonical_photo(u)[1].rsplit("/", 1)[0], "c": p.get("category"), "a": p.get("archived")}
    return {"k": "url", "u": u, "c": p.get("category"), "a": p.get("archived")}


def card(m: dict, now: datetime) -> dict:
    eq, el, val, o = m.get("equipment") or {}, m["eligibility"], m.get("valuation") or {}, m.get("otd") or {}
    s = m.get("seller") or {}
    first = datetime.strptime(m["first_seen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    lpc = m.get("last_price_change")
    recent_drop = bool(lpc and lpc["delta"] < 0 and
                       (now - datetime.strptime(lpc["at"][:19] + "Z", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)) <= timedelta(days=14))
    return {
        "key": m["key"], "vin": m.get("vin"), "variant": m.get("variant"), "label": m.get("variant_label"),
        "gen": m.get("generation"), "year": m.get("year"), "price": m.get("price"),
        "psrc": ("page" if m.get("price_source") == "dealer's listing page" else "last" if (m.get("price_source") or "").endswith("last advertised") else None),
        "por": bool(m.get("price_on_request")), "pnote": m.get("price_note"), "snote": m.get("status_note"), "pend": bool(m.get("sale_pending_note")), "soon": bool(m.get("coming_soon_note")), "pok": m.get("page_verified_on"),
        "price0": m.get("original_price"), "dprice": m.get("price_change_total"), "lpc": lpc, "drop": recent_drop,
        "miles": m.get("mileage"), "ext": m.get("exterior_color"), "extf": m.get("exterior_family"),
        "hex": m.get("exterior_hex"), "int": m.get("interior_color"), "intf": m.get("interior_family"),
        "trans": m.get("transmission"), "transq": bool(m.get("transmission_note")) and not m.get("transmission"), "cpo": bool(m.get("cpo")), "cpos": m.get("cpo_status"), "cond": m.get("condition"),
        "seats": (eq.get("seats") or {}).get("value"), "lift": (eq.get("lift") or {}).get("status"),
        "weissach": (eq.get("weissach") or {}).get("value"), "weissach_s": (eq.get("weissach") or {}).get("status"),
        "pccb": (eq.get("pccb") or {}).get("value"),
        "seller": s.get("name"), "skind": s.get("kind"), "city": s.get("city"), "state": s.get("state"),
        "tx": bool(m.get("in_texas")), "locq": bool(m.get("location_note")), "dist": (m.get("distance") or {}).get("road_miles_est"),
        "cat": el["category"], "status": m["status"], "why": el["summary"], "unver": el["unverified"],
        "score": m["score"]["total"],
        "deal": {"rating": val.get("rating"), "delta": val.get("delta_pct"), "n": val.get("n"),
                 "conf": val.get("confidence"), "lo": val.get("fair_low"), "mid": val.get("fair_mid"), "hi": val.get("fair_high")},
        "otd": o.get("total"), "fees": o.get("dealer_fees"), "fees_basis": o.get("dealer_fees_basis"),
        "ship": o.get("shipping"), "ship_mode": o.get("shipping_mode"),
        "first": m["first_seen"], "since": m.get("listed_since"), "dom": m.get("days_on_market"),
        "new": (not m.get("baseline")) and (now - first) <= timedelta(days=7), "baseline": bool(m.get("baseline")),
        "thumbs": [_photo_ref(p) for p in (m.get("photos") or [])[:6]],
        "nphotos": (m.get("gallery") or {}).get("captured", 0), "nadv": (m.get("gallery") or {}).get("advertised"),
        "gcomplete": (m.get("gallery") or {}).get("complete"), "gnote": (m.get("gallery") or {}).get("note"),
        "sources": [x["label"] for x in m.get("sources") or []], "quality": (m.get("quality") or {}).get("level"),
        "conflicts": len(m.get("conflicts") or []), "sale": m.get("sale"),
        "last_seen": max((x["last_seen"] for x in m.get("sources") or []), default=None),
    }


def _group_stats(points: list[dict]) -> dict:
    prices = [p["price"] for p in points]
    miles = [p["miles"] for p in points]
    out = {"n": len(points)}
    if not points:
        return out
    out.update({"median_ask": median(prices), "p25": valuation.percentile(prices, .25), "p75": valuation.percentile(prices, .75),
                "min": min(prices), "max": max(prices), "median_miles": median(miles)})
    slope = valuation.theil_sen_slope([(p["miles"], p["price"]) for p in points]) if len(points) >= valuation.MIN_SLOPE_COMPS else None
    out["slope_per_mile"] = round(min(0.0, slope), 3) if slope is not None else None
    s = out["slope_per_mile"] or 0.0
    ref = median(miles)
    effects = []
    for attr, label in (("cpo", "CPO vs not CPO"), ("manual", "Manual vs PDK"), ("weissach", "Weissach vs none"), ("pccb", "PCCB vs steel")):
        a = [p["price"] + s * (ref - p["miles"]) for p in points if p.get(attr) is True]
        b = [p["price"] + s * (ref - p["miles"]) for p in points if p.get(attr) is False]
        if len(a) >= 4 and len(b) >= 4:
            effects.append({"attr": attr, "label": label, "n_with": len(a), "n_without": len(b),
                            "median_diff": round(median(a) - median(b)),
                            "note": "difference of mileage-adjusted median asking prices; not a controlled estimate"})
        else:
            effects.append({"attr": attr, "label": label, "n_with": len(a), "n_without": len(b), "median_diff": None,
                            "note": "too few cars on one side (need 4+ each)"})
    out["effects"] = effects
    return out


def export(con, cfg: dict, out: Path, state: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    vdir = out / "v"
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir()
    now = datetime.now(timezone.utc)
    cards, points = [], []
    for row in con.execute("SELECT * FROM vehicles WHERE merged IS NOT NULL").fetchall():
        m = json.loads(row["merged"])
        c = card(m, now)
        cards.append(c)
        m["photos"] = [_photo_ref(p) for p in m.get("photos") or []]
        if m.get("seller"):
            m["seller"] = {k: v for k, v in m["seller"].items() if k != "email"}     # contact emails stay in the private database
        m["events"] = [{"at": e["at"], "event": e["event"], "detail": json.loads(e["detail"]) if e["detail"] and e["detail"][:1] in "{[" else e["detail"]}
                       for e in con.execute("SELECT * FROM lifecycle_events WHERE vehicle_key=? ORDER BY at, id", (row["key"],))]
        m["evidence"] = [dict(e) for e in con.execute(
            "SELECT field, status, value, source, detail, url, observed_at FROM evidence WHERE vehicle_key=? ORDER BY field, observed_at", (row["key"],))]
        (vdir / f"{row['key'].replace(':', '_')}.json").write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        if m.get("price") is not None and m.get("mileage") is not None and m.get("variant"):
            points.append({"key": m["key"], "variant": m["variant"], "gen": m["generation"], "year": m["year"],
                           "miles": m["mileage"], "price": m["price"], "status": m["status"], "cat": c["cat"],
                           "cpo": bool(m.get("cpo")), "manual": (m.get("transmission") == "manual") if m.get("transmission") else None,
                           "weissach": c["weissach"], "pccb": c["pccb"], "extf": c["extf"], "dom": c["dom"],
                           "label": f"{m.get('year')} {m.get('variant_label')}", "state": c["state"]})

    groups = {}
    for p in points:
        if p["status"] in ("active", "unverified"):
            groups.setdefault(f"{p['variant']}|{p['gen']}", []).append(p)
    group_stats = {k: _group_stats(v) for k, v in groups.items()}
    sales = [dict(r) for r in con.execute("SELECT * FROM sales ORDER BY sale_date DESC")]
    snaps = [dict(r) for r in con.execute("SELECT * FROM inventory_snapshots ORDER BY day")]
    changes = []
    for e in con.execute("SELECT * FROM lifecycle_events WHERE event='price_change' ORDER BY at DESC LIMIT 300"):
        d = json.loads(e["detail"])
        changes.append({"key": e["vehicle_key"], "at": e["at"], **d})
    market = {"generated_at": D.now_iso(), "points": points, "groups": group_stats, "sales": sales, "snapshots": snaps,
              "price_changes": changes, "methodology": METHODOLOGY}
    (out / "market.json").write_text(json.dumps(market, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    runs = []
    for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30"):
        srcs = [dict(s) | {"detail": json.loads(s["detail"] or "{}")} for s in
                con.execute("SELECT * FROM source_runs WHERE run_id=?", (r["id"],))]
        runs.append({"id": r["id"], "started_at": r["started_at"], "finished_at": r["finished_at"], "status": r["status"],
                     "baseline": bool(r["baseline"]), "sources": srcs})
    last_success = {r["key"].split(":", 1)[1]: r["value"] for r in con.execute("SELECT * FROM meta WHERE key LIKE 'last_success:%'")}
    digests = [{"date": d["digest_date"], "status": d["status"], "created_at": d["created_at"], "sent_at": d["sent_at"],
                "channel": d["channel"], "attempts": d["attempts"], "error": d["last_error"],
                "vehicles": len(json.loads(d["vehicle_keys"]))} for d in con.execute("SELECT * FROM digests ORDER BY digest_date DESC LIMIT 60")]
    listed = [c for c in cards if c["status"] != "removed"]
    adv = sum(c["nadv"] or 0 for c in listed)
    cap = sum(c["nphotos"] or 0 for c in listed)
    incomplete = [{"key": c["key"], "label": f"{c['year']} {c['label']}", "captured": c["nphotos"], "advertised": c["nadv"],
                   "note": c["gnote"], "cat": c["cat"]} for c in listed if not c["gcomplete"]]
    dec = {r["status"]: r["c"] for r in con.execute("SELECT status, COUNT(*) c FROM vin_decodes GROUP BY status")}
    coverage = [dict(c) for c in COVERAGE_NOTES]
    fin = con.execute("""SELECT finished_at, listings_seen, detail FROM source_runs WHERE source='porsche_finder'
                         AND status IN ('ok','partial') ORDER BY id DESC LIMIT 1""").fetchone()
    if fin and "browser" in (fin["detail"] or ""):
        coverage[0].update({"state": "covered by browser capture", "note": FINDER_BROWSER_NOTE,
                            "captured_at": json.loads(fin["detail"]).get("captured_at"), "listings": fin["listings_seen"]})
    pages = {r["result"]: r["c"] for r in con.execute("SELECT result, COUNT(*) c FROM page_checks GROUP BY result")}
    page_avail = {r["avail"] or "could not tell": r["c"] for r in con.execute("SELECT avail, COUNT(*) c FROM page_checks GROUP BY avail")}
    page_last = json.loads(D.get_meta(con, "last_pagecheck_summary") or "null")
    health = {"generated_at": D.now_iso(), "runs": runs, "decodes": dec, "page_checks": pages, "page_avail": page_avail, "page_last": page_last,
              "no_price": sum(1 for c in listed if c["price"] is None), "price_from_page": sum(1 for c in listed if c["psrc"] == "page"),
              "gearbox_unknown": sum(1 for c in listed if not c["trans"]),
              "decode_last": json.loads(D.get_meta(con, "last_decode_summary", "null")),
              "api_calls": {r["key"].split(":", 1)[1]: int(r["value"]) for r in con.execute("SELECT * FROM meta WHERE key LIKE 'mc_calls:%'")},
              "last_success": last_success, "coverage": coverage,
              "digests": digests, "gallery": {"advertised": adv, "captured": cap, "cars": len(listed), "complete_cars": sum(1 for c in listed if c["gcomplete"]),
                          "preview_only": sum(1 for c in listed if c["gnote"] and "preview" in c["gnote"]),
                          "no_photos": sum(1 for c in listed if not c["nphotos"]),
                          "finder_partial": sum(1 for c in listed if c["gnote"] and "original listing" in c["gnote"]
                                                and "preview" not in c["gnote"]),
                          "incomplete": [g for g in incomplete if g["cat"] in ("lead", "confirmed", "near_miss")][:200]},
              "baseline": {r["key"].split(":", 1)[1]: r["value"] for r in con.execute("SELECT * FROM meta WHERE key LIKE 'baseline_at:%'")}}
    (out / "health.json").write_text(json.dumps(health, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    counts = {}
    for c in cards:
        if c["status"] in ("active", "unverified"):
            counts[c["cat"]] = counts.get(c["cat"], 0) + 1
    counts["archive"] = sum(1 for c in cards if c["status"] in ("removed", "sold"))
    index = {"generated_at": D.now_iso(), "last_success": last_success,
             "last_run": {"status": runs[0]["status"], "at": runs[0]["started_at"]} if runs else None,
             "home": cfg["buyer"]["home_label"], "variants": cfg["variants"], "otd": cfg["otd"],
             "weights": cfg["weights"], "counts": counts, "vehicles": cards}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"vehicles": len(cards), "points": len(points), "sales": len(sales)}
