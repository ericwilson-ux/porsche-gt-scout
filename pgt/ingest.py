"""Ingestion: upsert listings, track prices, conservative lifecycle, rebuild merged vehicles."""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from . import classify as C
from . import db as D
from . import evaluate, geo, otd, valuation
from . import vin as vinlib
from .sources.base import Listing, SourceResult

SOURCE_PRIORITY = {"porsche_finder": 0, "marketcheck": 1}
SOURCE_LABEL = {"porsche_finder": "Porsche Finder", "marketcheck": "MarketCheck dealer feed"}


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def vehicle_key(lst: Listing) -> str:
    v = vinlib.normalize(lst.vin)
    return v if v else f"NOVIN:{lst.source}:{lst.source_listing_id}"


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------
def _upsert_seller(con, s: dict, now: str) -> int | None:
    if not s or not s.get("name"):
        return None
    key = f"{re.sub(r'[^a-z0-9]+', ' ', s['name'].lower()).strip()}|{(s.get('zip') or '')[:5]}"
    lat, lon = s.get("lat"), s.get("lon")
    if lat is None or lon is None:
        loc = geo.zip_latlon(s.get("zip"))
        if loc:
            lat, lon = loc[0], loc[1]
            if not s.get("state"):
                s["state"] = loc[3]
    row = con.execute("SELECT id FROM sellers WHERE key=?", (key,)).fetchone()
    if row:
        con.execute("""UPDATE sellers SET name=?, kind=COALESCE(?,kind), street=COALESCE(?,street), city=COALESCE(?,city),
                       state=COALESCE(?,state), zip=COALESCE(?,zip), phone=COALESCE(?,phone), website=COALESCE(?,website),
                       lat=COALESCE(?,lat), lon=COALESCE(?,lon), last_seen=? WHERE id=?""",
                    (s["name"], s.get("kind"), s.get("street"), s.get("city"), s.get("state"), s.get("zip"),
                     s.get("phone"), s.get("website"), lat, lon, now, row["id"]))
        return row["id"]
    cur = con.execute("""INSERT INTO sellers(key,name,kind,street,city,state,zip,phone,website,lat,lon,first_seen,last_seen)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (key, s["name"], s.get("kind"), s.get("street"), s.get("city"), s.get("state"), s.get("zip"),
                       s.get("phone"), s.get("website"), lat, lon, now, now))
    return cur.lastrowid


def known_scopes(con, source: str) -> set:
    return set(json.loads(D.get_meta(con, f"scopes_seen:{source}", "[]")))


def upsert_listings(con, result: SourceResult, now: str, baseline: bool | None = None) -> dict:
    """Insert/update listings.  A vehicle first seen while its source (or that source's
    coverage unit, e.g. one dealer) is being covered for the first time is flagged as
    baseline: it was already on the market, so it is never announced as "new"."""
    stats = {"seen": 0, "new_listings": 0, "new_vehicles": 0, "new_vehicles_baseline": 0, "price_changes": 0,
             "photos_advertised": 0, "photos_captured": 0}
    source_baseline = D.get_meta(con, f"baseline_done:{result.source}") != "1"
    scopes_before = known_scopes(con, result.source)
    for lst in result.listings:
        stats["seen"] += 1
        key = vehicle_key(lst)
        is_base = baseline if baseline is not None else (source_baseline or (lst.scope not in scopes_before))
        veh = con.execute("SELECT key FROM vehicles WHERE key=?", (key,)).fetchone()
        if not veh:
            con.execute("""INSERT INTO vehicles(key, vin, variant, generation, year, first_seen, last_seen, baseline, status,
                           status_changed_at) VALUES(?,?,?,?,?,?,?,?,'active',?)""",
                        (key, vinlib.normalize(lst.vin), lst.variant, lst.generation, lst.year, now, now, 1 if is_base else 0, now))
            stats["new_vehicles"] += 1
            stats["new_vehicles_baseline"] += 1 if is_base else 0
        seller_id = _upsert_seller(con, lst.seller, now)
        d = lst.to_dict()
        d["photos"] = []                     # photos live in their own table; keep listing rows small
        data = D.j(d)
        row = con.execute("SELECT * FROM listings WHERE source=? AND source_listing_id=?",
                          (lst.source, lst.source_listing_id)).fetchone()
        if row is None:
            cur = con.execute("""INSERT INTO listings(source, source_listing_id, vehicle_key, seller_id, url, scope, status,
                                 first_seen, last_seen, last_checked, price, mileage, data)
                                 VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?)""",
                              (lst.source, lst.source_listing_id, key, seller_id, lst.url, lst.scope, now, now, now,
                               lst.price, lst.mileage, data))
            lid = cur.lastrowid
            stats["new_listings"] += 1
            D.event(con, key, "discovered" if not veh else "listed_on_additional_source",
                    {"source": lst.source, "url": lst.url, "price": lst.price, "baseline": bool(is_base)}, lid, now)
            if lst.source_previous_price and lst.price and abs(lst.source_previous_price - lst.price) >= 1:
                ts = lst.extra.get("ref_price_dt")
                prev_at = (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                           if isinstance(ts, (int, float)) and ts > 0 else lst.source_first_seen or now)
                con.execute("""INSERT INTO price_observations(listing_id, vehicle_key, observed_at, price, kind, note)
                               VALUES(?,?,?,?, 'source_reported_previous', ?)""",
                            (lid, key, prev_at, lst.source_previous_price, "earlier price reported by the feed"))
            if lst.price is not None:
                con.execute("""INSERT INTO price_observations(listing_id, vehicle_key, observed_at, price, kind)
                               VALUES(?,?,?,?,'asking')""", (lid, key, now, lst.price))
        else:
            lid = row["id"]
            if row["status"] != "active":
                D.event(con, key, "relisted", {"source": lst.source, "was": row["status"]}, lid, now)
            if lst.price is not None and (row["price"] is None or abs(row["price"] - lst.price) >= 1):
                con.execute("""INSERT INTO price_observations(listing_id, vehicle_key, observed_at, price, kind)
                               VALUES(?,?,?,?,'asking')""", (lid, key, now, lst.price))
                if row["price"] is not None:
                    stats["price_changes"] += 1
                    D.event(con, key, "price_change", {"source": lst.source, "from": row["price"], "to": lst.price,
                                                       "delta": lst.price - row["price"]}, lid, now)
            con.execute("""UPDATE listings SET vehicle_key=?, seller_id=?, url=?, scope=COALESCE(?,scope), status='active',
                           last_seen=?, last_checked=?, silent_misses=0, explicit_gone=0, last_gone_signal=NULL,
                           price=COALESCE(?,price), mileage=COALESCE(?,mileage), data=? WHERE id=?""",
                        (key, seller_id, lst.url, lst.scope, now, now, lst.price, lst.mileage, data, lid))
        con.execute("UPDATE vehicles SET last_seen=? WHERE key=?", (now, key))
        # options (replace with what the source says today)
        con.execute("DELETE FROM options WHERE listing_id=?", (lid,))
        for o in lst.options:
            con.execute("INSERT OR IGNORE INTO options(listing_id, code, name, grp, kind) VALUES(?,?,?,?,'factory_option')",
                        (lid, o.get("code"), o["name"], o.get("group")))
        for name in lst.standard_equipment:
            con.execute("INSERT OR IGNORE INTO options(listing_id, code, name, grp, kind) VALUES(?,NULL,?,NULL,'standard')",
                        (lid, name))
        # photos: keyed by the source's image id so different resolutions never double count
        for ph in lst.photos:
            con.execute("""INSERT INTO photos(listing_id, vehicle_key, source_image_id, url, category, position, aspect,
                           first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(listing_id, source_image_id) DO UPDATE SET url=excluded.url,
                           category=excluded.category, position=excluded.position, last_seen=excluded.last_seen""",
                        (lid, key, ph["id"], ph["url"], ph.get("category"), ph.get("position"), ph.get("aspect"), now, now))
        stats["photos_advertised"] += lst.photos_advertised or 0
        stats["photos_captured"] += len(lst.photos)
    D.set_meta(con, f"scopes_seen:{result.source}", json.dumps(sorted(scopes_before | {str(x) for x in result.scopes_ok})))
    if result.status == "ok" and source_baseline:
        D.set_meta(con, f"baseline_done:{result.source}", "1")
        D.set_meta(con, f"baseline_at:{result.source}", now)
    return stats


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def apply_lifecycle(con, result: SourceResult, now: str, cfg: dict) -> dict:
    """Age out listings the source no longer shows - conservatively.

    A listing only accrues a "gone" signal when the source was reachable for the
    relevant coverage unit; failed or partial fetches change nothing.
    """
    lc = cfg["lifecycle"]
    stats = {"unverified": 0, "removed": 0, "untouched_due_to_coverage": 0}
    if result.status in ("failed", "skipped"):
        return stats
    seen = {l.source_listing_id for l in result.listings}
    now_dt = _parse_ts(now)
    rows = con.execute("SELECT * FROM listings WHERE source=? AND status!='removed'", (result.source,)).fetchall()
    for row in rows:
        lid = row["source_listing_id"]
        if lid in seen:
            continue
        last_sig = _parse_ts(row["last_gone_signal"]) if row["last_gone_signal"] else None
        fresh_signal_ok = last_sig is None or (now_dt - last_sig) >= timedelta(hours=18)
        explicit, silent = row["explicit_gone"], row["silent_misses"]
        if lid in result.inconclusive:
            stats["untouched_due_to_coverage"] += 1
            continue
        if lid in result.explicit_gone:
            if fresh_signal_ok:
                explicit += 1
        elif lid in result.checked_ids:
            continue                      # conclusively checked and alive but not returned (e.g. reclassified)
        elif row["scope"] in result.scopes_ok:
            if fresh_signal_ok:
                silent += 1
        else:
            stats["untouched_due_to_coverage"] += 1
            continue
        hours_unseen = (now_dt - _parse_ts(row["last_seen"])).total_seconds() / 3600
        gone = (explicit >= lc["explicit_gone_checks_to_remove"] or silent >= lc["silent_misses_to_remove"]) \
            and hours_unseen >= lc["min_hours_before_removed"]
        new_status = "removed" if gone else "unverified"
        con.execute("""UPDATE listings SET status=?, explicit_gone=?, silent_misses=?, last_gone_signal=?, last_checked=?
                       WHERE id=?""",
                    (new_status, explicit, silent, now if fresh_signal_ok else row["last_gone_signal"], now, row["id"]))
        if new_status != row["status"]:
            stats[new_status] += 1
            D.event(con, row["vehicle_key"], f"listing_{new_status}",
                    {"source": result.source, "explicit_gone_signals": explicit, "silent_misses": silent,
                     "last_asking": row["price"]}, row["id"], now)
    return stats


# ---------------------------------------------------------------------------
# merged vehicle view
# ---------------------------------------------------------------------------
def _first(listings: list[dict], field: str):
    for l in listings:
        v = l.get(field)
        if v not in (None, "", [], {}):
            return v
    return None


def _warranty_summary(merged: dict, today: date) -> dict:
    out = {"cpo": bool(merged.get("cpo")), "items": merged.get("warranty") or []}
    year = merged.get("year")
    if not year:
        return out
    start, basis = None, "estimated from model year"
    stated = merged.get("warranty_start_stated")
    if stated:
        m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", stated)
        if m:
            try:
                start, basis = date(int(m.group(3)), int(m.group(1)), int(m.group(2))), "stated by seller"
            except ValueError:
                start = None
    if start is None:
        start = date(year, 4, 1)
    end = date(start.year + 4, start.month, min(start.day, 28))
    months = max(0, (end.year - today.year) * 12 + end.month - today.month)
    if (merged.get("mileage") or 0) >= 50000:
        months = 0
    out.update({"factory_start": start.isoformat(), "factory_start_basis": basis,
                "factory_months_remaining_est": months,
                "note": "New-car limited warranty is 4 years / 50,000 miles from first sale; confirm the in-service date."})
    return out


def merge_vehicle(con, vrow, cfg: dict, today: date) -> dict:
    rows = con.execute("""SELECT l.*, s.name AS s_name, s.kind AS s_kind, s.street AS s_street, s.city AS s_city,
                          s.state AS s_state, s.zip AS s_zip, s.phone AS s_phone, s.website AS s_website,
                          s.lat AS s_lat, s.lon AS s_lon
                          FROM listings l LEFT JOIN sellers s ON s.id=l.seller_id WHERE l.vehicle_key=?""",
                       (vrow["key"],)).fetchall()
    status_rank = {"active": 0, "unverified": 1, "removed": 2}
    rows = sorted(rows, key=lambda r: (status_rank.get(r["status"], 3), SOURCE_PRIORITY.get(r["source"], 9), r["first_seen"]))
    ls = []
    for r in rows:
        d = json.loads(r["data"])
        d["_row"] = r
        ls.append(d)
    live = [l for l in ls if l["_row"]["status"] != "removed"] or ls
    m: dict = {"key": vrow["key"], "vin": vrow["vin"], "vin_valid": vinlib.is_valid(vrow["vin"])}
    for f in ("variant", "generation", "year", "title", "condition", "exterior_color", "exterior_hex", "interior_color",
              "transmission", "transmission_raw", "mileage", "stock_number", "seller_comment", "previous_owners"):
        m[f] = _first(live, f)
    m["variant_label"] = C.VARIANT_LABEL.get(m["variant"] or "", m["variant"])
    m["cpo"] = any(l.get("cpo") for l in live)
    m["exterior_family"] = C.exterior_family(m["exterior_color"])
    m["interior_family"] = C.interior_family(m["interior_color"])

    # price: best-priority live listing that has one
    priced = next((l for l in live if l.get("price") is not None), None)
    m["price"] = priced["price"] if priced else None
    m["price_as_listed"] = priced.get("price_as_listed") if priced else None
    m["dealer_fees_listed"] = priced.get("dealer_fees_listed") if priced else None
    m["fee_items"] = priced.get("fee_items") if priced else []
    m["price_source"] = SOURCE_LABEL.get(priced["source"], priced["source"]) if priced else None

    # seller: from the best listing's seller row
    r0 = live[0]["_row"]
    m["seller"] = {"name": r0["s_name"], "kind": r0["s_kind"], "street": r0["s_street"], "city": r0["s_city"],
                   "state": r0["s_state"], "zip": r0["s_zip"], "phone": r0["s_phone"], "website": r0["s_website"]}
    for l in live:                       # fill gaps (e.g. phone) from other sources listing the same car
        s = l.get("seller") or {}
        for f in ("phone", "website", "street"):
            if not m["seller"].get(f) and s.get(f):
                m["seller"][f] = s[f]
    m["distance"] = geo.distance_from(cfg["buyer"]["home_zip"], r0["s_zip"], r0["s_lat"], r0["s_lon"],
                                      cfg["otd"]["road_factor"])
    m["in_texas"] = (r0["s_state"] or "").upper() == "TX"

    # -- equipment and evidence
    eq_src = next((l for l in ls if l.get("has_equipment_list")), None)
    texts = [l.get("seller_comment") or "" for l in ls]
    text_sig = C.scan_seller_text("\n".join(texts))
    if any(C.scan_seller_text(l.get("title"))["mentions_weissach"] for l in ls):
        text_sig["mentions_weissach"] = True
    equipment: dict = {}
    if eq_src:
        det = C.detect_equipment(eq_src.get("options") or [], eq_src.get("standard_equipment") or [], eq_src.get("highlights") or [])
        src_label = SOURCE_LABEL.get(eq_src["source"], eq_src["source"])
        lift_opt = next((o for o in eq_src["options"] if str(o.get("code") or "").upper() in C.LIFT_CODES
                         or C.has_lift_text(o.get("name"))), None)
        if det["lift"]:
            code = f" (option code {lift_opt['code']})" if lift_opt and lift_opt.get("code") else ""
            equipment["lift"] = {"status": "verified", "value": True, "source": src_label, "url": eq_src["url"],
                                 "detail": f"Front Axle Lift System{code} in the factory option list on {src_label}"}
        else:
            equipment["lift"] = {"status": "verified_absent", "value": False, "source": src_label, "url": eq_src["url"],
                                 "detail": f"Factory option list on {src_label} ({len(eq_src['options'])} options) has no front axle lift"}
        equipment["weissach"] = {"status": "verified" if det["weissach"] else "verified_absent", "value": det["weissach"],
                                 "source": src_label}
        equipment["seats"] = {"status": "verified" if det["seats"] else "unknown", "value": det["seats"], "source": src_label}
        equipment["pccb"] = {"status": "verified", "value": det["pccb"], "source": src_label}
        m["options"] = eq_src.get("options") or []
        m["standard_equipment"] = eq_src.get("standard_equipment") or []
        m["highlights"] = eq_src.get("highlights") or []
    else:
        if text_sig["mentions_lift"]:
            equipment["lift"] = {"status": "seller_stated", "value": True, "source": "seller description",
                                 "detail": "Seller description mentions a front axle lift; no factory option list to confirm"}
        else:
            equipment["lift"] = {"status": "unknown", "value": None}
        equipment["weissach"] = ({"status": "seller_stated", "value": True, "source": "listing title or description"}
                                 if text_sig["mentions_weissach"] else {"status": "unknown", "value": None})
        equipment["seats"] = {"status": "unknown", "value": None}
        equipment["pccb"] = {"status": "unknown", "value": None}
        m["options"], m["standard_equipment"], m["highlights"] = [], [], []
    m["equipment"] = equipment

    # -- history
    history: dict = {"service_history": _first(ls, "service_history"),
                     "one_owner": bool(_first(ls, "one_owner")) or None,
                     "previous_owners": m["previous_owners"]}
    if any(l.get("accident_free") is True for l in ls):
        history["accident"] = {"status": "verified_clean", "detail": "Source lists the car as accident-free"}
    elif text_sig["mentions_accident"]:
        history["accident"] = {"status": "reported", "detail": "Listing text mentions accident or damage history"}
    elif text_sig["claims_clean_history"]:
        history["accident"] = {"status": "seller_stated_clean",
                               "detail": "Seller states a clean history report in the listing; ask for the report itself"}
    else:
        bits = []
        if m["cpo"]:
            bits.append("Porsche Approved CPO (inspection passed), but no accident statement published")
        if any(l.get("carfax_clean_title") for l in ls):
            bits.append("feed reports a clean title, which is not the same as accident-free")
        history["accident"] = {"status": "unknown", "detail": "; ".join(bits) or None}
    history["track"] = ({"status": "documented", "detail": "Listing text refers to track use or track equipment"}
                        if text_sig["mentions_track_use"] else {"status": "none_found"})
    m["history"] = history

    m["warranty"] = _first(ls, "warranty") or []
    m["warranty_start_stated"] = next((l["extra"].get("warranty_start_stated") for l in ls
                                       if (l.get("extra") or {}).get("warranty_start_stated")), None)
    m["warranty_summary"] = _warranty_summary(m, today)
    docs = {}
    for l in ls:
        for k, val in (l.get("documents") or {}).items():
            if val and not docs.get(k):
                docs[k] = val
                docs.setdefault("source_url", l["url"])
    m["documents"] = docs

    # -- photos: the richest gallery among the listings, de-duplicated by source image id
    best, best_n = None, -1
    for l in ls:
        n = con.execute("SELECT COUNT(*) c FROM photos WHERE listing_id=?", (l["_row"]["id"],)).fetchone()["c"]
        bonus = 0.5 if (l.get("photos_are_real") and l["source"] == "porsche_finder") else 0
        if n + bonus > best_n:
            best, best_n = l, n + bonus
    photos = []
    if best is not None:
        last = con.execute("SELECT MAX(last_seen) m FROM photos WHERE listing_id=?", (best["_row"]["id"],)).fetchone()["m"]
        for p in con.execute("""SELECT * FROM photos WHERE listing_id=? AND last_seen=? ORDER BY position""",
                             (best["_row"]["id"], last)):
            photos.append({"id": p["source_image_id"], "url": p["url"], "category": p["category"],
                           "archived": p["archived_path"]})
    advertised = best.get("photos_advertised") if best else None
    m["photos"] = photos
    m["gallery"] = {"source": SOURCE_LABEL.get(best["source"], best["source"]) if best else None,
                    "advertised": advertised, "captured": len(photos),
                    "complete": bool(photos) and (advertised is None or len(photos) >= advertised),
                    "real_photos": best.get("photos_are_real") if best else None}

    # -- sources, conflicts, price history, timing
    m["sources"] = [{"source": l["source"], "label": SOURCE_LABEL.get(l["source"], l["source"]),
                     "id": l["source_listing_id"], "url": l["url"], "status": l["_row"]["status"],
                     "price": l.get("price"), "mileage": l.get("mileage"), "origin_site": l.get("origin_site"),
                     "first_seen": l["_row"]["first_seen"], "last_seen": l["_row"]["last_seen"],
                     "issues": l.get("issues") or []} for l in ls]
    conflicts = []
    act = [l for l in ls if l["_row"]["status"] == "active"]
    prices = [l["price"] for l in act if l.get("price")]
    if len(prices) > 1 and max(prices) - min(prices) > max(500, 0.01 * max(prices)):
        conflicts.append({"field": "price", "detail": "Sources disagree on price: " +
                          ", ".join(f"{SOURCE_LABEL.get(l['source'], l['source'])} ${l['price']:,.0f}" for l in act if l.get("price"))})
    miles = [l["mileage"] for l in act if l.get("mileage") is not None]
    if len(miles) > 1 and max(miles) - min(miles) > 500:
        conflicts.append({"field": "mileage", "detail": "Sources disagree on mileage: " +
                          ", ".join(f"{SOURCE_LABEL.get(l['source'], l['source'])} {l['mileage']:,} mi" for l in act if l.get("mileage") is not None)})
    fams = {C.exterior_family(l.get("exterior_color")) for l in act if l.get("exterior_color")}
    if len(fams) > 1:
        conflicts.append({"field": "exterior_color", "detail": "Sources disagree on exterior colour: " +
                          ", ".join(sorted({l["exterior_color"] for l in act if l.get("exterior_color")}))})
    tr = {l.get("transmission") for l in act if l.get("transmission")}
    if len(tr) > 1:
        conflicts.append({"field": "transmission", "detail": "Sources disagree on transmission"})
    for l in ls:
        for issue in l.get("issues") or []:
            conflicts.append({"field": "identity", "detail": f"{SOURCE_LABEL.get(l['source'], l['source'])}: {issue}"})
    m["conflicts"] = conflicts

    ph = con.execute("""SELECT p.observed_at, p.price, p.kind, l.source FROM price_observations p
                        JOIN listings l ON l.id=p.listing_id WHERE p.vehicle_key=? ORDER BY p.observed_at, p.id""",
                     (vrow["key"],)).fetchall()
    m["price_history"] = [{"at": p["observed_at"], "price": p["price"], "kind": p["kind"],
                           "source": SOURCE_LABEL.get(p["source"], p["source"])} for p in ph]
    primary = [p for p in m["price_history"] if priced and p["source"] == m["price_source"]]
    m["original_price"] = primary[0]["price"] if primary else None
    m["price_change_total"] = (m["price"] - primary[0]["price"]) if (primary and m["price"] is not None) else None
    last_change = None
    for a, b in zip(primary, primary[1:]):
        if abs(b["price"] - a["price"]) >= 1:
            last_change = {"at": b["at"], "from": a["price"], "to": b["price"], "delta": b["price"] - a["price"]}
    m["last_price_change"] = last_change

    src_first = [l.get("source_first_seen") for l in ls if l.get("source_first_seen")]
    earliest = min([vrow["first_seen"]] + [s[:19].rstrip("Z") + "Z" if "T" in s else s + "T00:00:00Z" for s in src_first])
    m["first_seen"] = vrow["first_seen"]
    m["listed_since"] = earliest
    m["listed_since_basis"] = "source-reported" if earliest != vrow["first_seen"] else "first observed by this tracker"
    try:
        m["days_on_market"] = max(0, (datetime.now(timezone.utc) - _parse_ts(earliest)).days)
    except ValueError:
        m["days_on_market"] = None
    m["baseline"] = bool(vrow["baseline"])

    # -- vehicle status from listing statuses
    statuses = {l["_row"]["status"] for l in ls}
    m["status"] = "active" if "active" in statuses else "unverified" if "unverified" in statuses else "removed"
    return m


def data_quality(m: dict) -> dict:
    """A plain confidence indicator for the record as a whole."""
    pts, notes = 0, []
    if m.get("vin_valid"):
        pts += 2
    else:
        notes.append("VIN missing or failed its check digit")
    if (m.get("equipment") or {}).get("lift", {}).get("status") in ("verified", "verified_absent"):
        pts += 2
    else:
        notes.append("no manufacturer equipment list")
    if m.get("price") is not None and m.get("mileage") is not None:
        pts += 1
    else:
        notes.append("price or mileage missing")
    if (m.get("gallery") or {}).get("complete"):
        pts += 1
    else:
        notes.append("gallery incomplete or missing")
    if not m.get("conflicts"):
        pts += 1
    else:
        notes.append(f"{len(m['conflicts'])} cross-source conflict(s)")
    level = "high" if pts >= 6 else "medium" if pts >= 4 else "low"
    return {"level": level, "points": pts, "max": 7, "notes": notes}


def rebuild_vehicles(con, cfg: dict, now: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    stats = {"vehicles": 0, "status_changes": 0}
    sold_keys = {r["vehicle_key"]: r for r in con.execute("SELECT * FROM sales WHERE vehicle_key IS NOT NULL")}
    merged_all = []
    for vrow in con.execute("SELECT * FROM vehicles").fetchall():
        m = merge_vehicle(con, vrow, cfg, today)
        if vrow["key"] in sold_keys:
            m["status"] = "sold"
            s = sold_keys[vrow["key"]]
            m["sale"] = {"price": s["sale_price"], "date": s["sale_date"], "venue": s["venue"], "url": s["url"], "kind": s["kind"]}
        m["eligibility"] = evaluate.eligibility(m, cfg)
        m["score"] = evaluate.preference_score(m, cfg, today)
        m["otd"] = otd.out_the_door(m.get("price"), (m.get("seller") or {}).get("state"),
                                    (m.get("distance") or {}).get("road_miles_est"), m.get("dealer_fees_listed"), cfg)
        m["quality"] = data_quality(m)
        merged_all.append((vrow, m))

    cutoff = _parse_ts(now) - timedelta(days=120)
    pool = []
    for vrow, m in merged_all:
        if m.get("price") is None or m.get("mileage") is None:
            continue
        if m["status"] in ("active", "unverified") or (vrow["last_seen"] and _parse_ts(vrow["last_seen"]) >= cutoff):
            pool.append({"key": m["key"], "variant": m["variant"], "generation": m["generation"], "year": m["year"],
                         "mileage": m["mileage"], "price": m["price"]})
    sales = [dict(r) for r in con.execute("SELECT variant, generation, year, mileage, sale_price FROM sales")]

    for vrow, m in merged_all:
        m["valuation"] = valuation.value({"key": m["key"], "variant": m["variant"], "generation": m["generation"],
                                          "year": m["year"], "mileage": m["mileage"], "price": m["price"]}, pool, sales)
        if m["status"] != vrow["status"]:
            stats["status_changes"] += 1
            D.event(con, m["key"], f"vehicle_{m['status']}", {"was": vrow["status"], "last_asking": m.get("price")}, None, now)
        if vrow["category"] and m["eligibility"]["category"] != vrow["category"]:
            D.event(con, m["key"], "category_change", {"from": vrow["category"], "to": m["eligibility"]["category"],
                                                       "summary": m["eligibility"]["summary"]}, None, now)
        con.execute("""UPDATE vehicles SET variant=?, generation=?, year=?, status=?, status_changed_at=CASE WHEN status!=? THEN ? ELSE status_changed_at END,
                       category=?, score=?, merged=? WHERE key=?""",
                    (m["variant"], m["generation"], m["year"], m["status"], m["status"], now,
                     m["eligibility"]["category"], m["score"]["total"], D.j(m), m["key"]))
        # evidence ledger (idempotent)
        ev = [("lift", m["equipment"]["lift"]), ("weissach", m["equipment"]["weissach"]),
              ("seats", m["equipment"]["seats"]), ("accident", m["history"]["accident"]), ("track", m["history"]["track"])]
        for field, e in ev:
            con.execute("""INSERT OR IGNORE INTO evidence(vehicle_key, field, status, value, source, detail, url, observed_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (m["key"], field, e.get("status") or "unknown", json.dumps(e.get("value")), e.get("source") or "",
                         e.get("detail"), e.get("url"), now))
        stats["vehicles"] += 1
    return stats


def snapshot_inventory(con, day: str) -> None:
    rows = con.execute("SELECT merged FROM vehicles WHERE status IN ('active','unverified') AND merged IS NOT NULL").fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        m = json.loads(r["merged"])
        if m.get("variant") and m.get("generation"):
            groups.setdefault((m["variant"], m["generation"]), []).append(m)
    for (variant, gen), ms in groups.items():
        prices = sorted(x["price"] for x in ms if x.get("price"))
        miles = [x["mileage"] for x in ms if x.get("mileage") is not None]
        elig = sum(1 for x in ms if x["eligibility"]["category"] in ("confirmed", "lead"))
        con.execute("""INSERT INTO inventory_snapshots(day, variant, generation, active_count, median_ask, p25_ask, p75_ask,
                       median_miles, eligible_count) VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(day, variant, generation) DO UPDATE SET active_count=excluded.active_count,
                       median_ask=excluded.median_ask, p25_ask=excluded.p25_ask, p75_ask=excluded.p75_ask,
                       median_miles=excluded.median_miles, eligible_count=excluded.eligible_count""",
                    (day, variant, gen, len(ms), median(prices) if prices else None,
                     valuation.percentile(prices, 0.25) if prices else None,
                     valuation.percentile(prices, 0.75) if prices else None,
                     median(miles) if miles else None, elig))


# ---------------------------------------------------------------------------
# completed sales (kept apart from asking prices)
# ---------------------------------------------------------------------------
def import_sales_csv(con, path: Path, now: str) -> int:
    """Import publicly documented completed sales.

    Columns: sale_date, sale_price, kind, venue, url, title, vin, year, mileage, variant, generation, notes
    Only rows with a sale price, a date and a public URL are accepted.
    """
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            try:
                price = float(str(row.get("sale_price") or "").replace(",", "").replace("$", ""))
            except ValueError:
                continue
            if not price or not row.get("sale_date") or not row.get("url"):
                continue
            vin_n = vinlib.normalize(row.get("vin"))
            year = int(row["year"]) if (row.get("year") or "").isdigit() else None
            cls = C.classify(row.get("title") or row.get("variant"), vin_n, year)
            variant = row.get("variant") or cls["variant"]
            gen = row.get("generation") or cls["generation"] or (C.generation_for(variant, year) if variant else None)
            vk = vin_n if vin_n and con.execute("SELECT 1 FROM vehicles WHERE key=?", (vin_n,)).fetchone() else None
            cur = con.execute("""INSERT OR IGNORE INTO sales(vehicle_key, vin, variant, generation, year, mileage, sale_price,
                                 sale_date, kind, venue, url, title, notes, recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (vk, vin_n, variant, gen, year,
                               int(row["mileage"]) if (row.get("mileage") or "").replace(",", "").isdigit() else None,
                               price, row["sale_date"], row.get("kind") or "auction_sold", row.get("venue"), row["url"],
                               row.get("title"), row.get("notes"), now))
            if cur.rowcount:
                n += 1
                if vk:
                    D.event(con, vk, "sold", {"sale_price": price, "venue": row.get("venue"), "url": row["url"]}, None, now)
    return n


# ---------------------------------------------------------------------------
# run orchestration
# ---------------------------------------------------------------------------
def record_source(con, run_id: int, result: SourceResult, started: str, up: dict | None, lc: dict | None) -> None:
    detail = {k: v for k, v in result.detail.items() if k != "raw_sample"}
    detail["messages"] = result.messages[:40]
    if lc:
        detail["lifecycle"] = lc
    up = up or {}
    con.execute("""INSERT INTO source_runs(run_id, source, started_at, finished_at, status, listings_seen, listings_new,
                   requests, errors, scopes_total, scopes_ok, photos_advertised, photos_captured, detail)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, result.source, started, D.now_iso(), result.status, up.get("seen", 0), up.get("new_listings", 0),
                 result.requests, result.errors, result.scopes_total, len(result.scopes_ok),
                 up.get("photos_advertised", 0), up.get("photos_captured", 0), D.j(detail)))


def process_results(con, cfg: dict, results: list[SourceResult], now: str | None = None,
                    sales_csv: Path | None = None) -> dict:
    """Apply a set of source results as one run.  Used by the CLI and by tests."""
    now = now or D.now_iso()
    baseline = not any(D.get_meta(con, f"baseline_done:{r.source}") == "1" for r in results)
    cur = con.execute("INSERT INTO runs(started_at, status, baseline) VALUES(?, 'running', ?)", (now, 1 if baseline else 0))
    run_id = cur.lastrowid
    summary = {"baseline": baseline, "sources": {}}
    any_ok = False
    for res in results:
        up = lc = None
        if res.status in ("ok", "partial"):
            up = upsert_listings(con, res, now)
            lc = apply_lifecycle(con, res, now, cfg)
            any_ok = True
            D.set_meta(con, f"last_success:{res.source}", now)
        record_source(con, run_id, res, now, up, lc)
        summary["sources"][res.source] = {"status": res.status, **(up or {}), **({"lifecycle": lc} if lc else {})}
    if sales_csv:
        summary["sales_imported"] = import_sales_csv(con, sales_csv, now)
    summary["rebuild"] = rebuild_vehicles(con, cfg, now)
    snapshot_inventory(con, now[:10])
    statuses = [r.status for r in results if r.status != "skipped"]
    status = "failed" if not any_ok else "ok" if all(s == "ok" for s in statuses) else "partial"
    con.execute("UPDATE runs SET finished_at=?, status=?, summary=? WHERE id=?", (D.now_iso(), status, D.j(summary), run_id))
    con.commit()
    summary["status"], summary["run_id"] = status, run_id
    return summary
