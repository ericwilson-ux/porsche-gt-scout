"""Daily digest: at most one email per local day, only for newly qualifying vehicles,
each vehicle announced once no matter how many sites list it, safe to retry.

Idempotency model
  * alert_log.vehicle_key is a primary key: claiming a vehicle for a digest can happen once.
  * digests.digest_date is a primary key: one digest (one email) per America/Chicago date.
  * A digest is created `pending`; sending marks it `sent`.  Re-running any step is a no-op.
"""
from __future__ import annotations

import html
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db as D

QUALIFYING_FROM = ("excluded", "near_miss")


def local_date(cfg: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(cfg["buyer"]["timezone"])).date().isoformat()


def _candidates(con, cfg: dict) -> list[dict]:
    cats = set(cfg["alerts"]["categories"])
    out = []
    rows = con.execute("""SELECT v.* FROM vehicles v LEFT JOIN alert_log a ON a.vehicle_key=v.key
                          WHERE a.vehicle_key IS NULL AND v.status='active' AND v.merged IS NOT NULL""").fetchall()
    for r in rows:
        if r["category"] not in cats:
            continue
        reason = None
        if not r["baseline"]:
            reason = "New listing"
        else:
            ev = con.execute("""SELECT detail FROM lifecycle_events WHERE vehicle_key=? AND event='category_change'
                                ORDER BY id DESC LIMIT 1""", (r["key"],)).fetchone()
            if ev:
                d = json.loads(ev["detail"])
                # a source's first import re-grades cars that were already for sale: backfill, not news
                if d.get("from") in QUALIFYING_FROM and d.get("to") in cats and not d.get("backfill"):
                    reason = "Now qualifies (was " + d["from"].replace("_", " ") + ")"
        if reason:
            m = json.loads(r["merged"])
            if m["equipment"]["lift"]["status"] == "decode_absent":
                continue                    # build data shows no front lift: stays visible on the site, not emailed
            m["_alert_reason"] = reason
            out.append(m)
    order = {"confirmed": 0, "lead": 1}
    out.sort(key=lambda m: (order.get(m["eligibility"]["category"], 9), -m["score"]["total"]))
    return out


def prepare_digest(con, cfg: dict, state: Path, now: datetime | None = None) -> dict | None:
    """Claim newly qualifying vehicles into a pending digest.  Returns the digest or None."""
    now = now or datetime.now(timezone.utc)
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = local_date(cfg, now)
    cands = _candidates(con, cfg)
    pending = con.execute("SELECT * FROM digests WHERE status IN ('pending','failed') ORDER BY digest_date LIMIT 1").fetchone()
    todays = con.execute("SELECT * FROM digests WHERE digest_date=?", (today,)).fetchone()
    if cands:
        if pending:
            ddate, keys = pending["digest_date"], json.loads(pending["vehicle_keys"])
        elif todays and todays["status"] == "sent":
            cands, ddate, keys = [], None, []          # today's email already went out; these wait for tomorrow
        else:
            ddate, keys = today, []
            con.execute("""INSERT INTO digests(digest_date, status, created_at, vehicle_keys) VALUES(?, 'pending', ?, '[]')""",
                        (ddate, now_s))
        for m in cands:
            cur = con.execute("""INSERT OR IGNORE INTO alert_log(vehicle_key, digest_date, category, claimed_at)
                                 VALUES(?,?,?,?)""", (m["key"], ddate, m["eligibility"]["category"], now_s))
            if cur.rowcount:
                keys.append(m["key"])
                D.event(con, m["key"], "alert_queued", {"digest": ddate, "reason": m["_alert_reason"]}, None, now_s)
        if ddate:
            con.execute("UPDATE digests SET vehicle_keys=? WHERE digest_date=?", (json.dumps(keys), ddate))
    con.commit()
    row = con.execute("SELECT * FROM digests WHERE status IN ('pending','failed') ORDER BY digest_date LIMIT 1").fetchone()
    write_pending_pointer(con, state)
    if not row:
        return None
    digest = build_digest(con, cfg, row)
    ddir = state / "digests"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / f"{row['digest_date']}.json").write_text(json.dumps(digest, indent=1, ensure_ascii=False), encoding="utf-8")
    return digest


def write_pending_pointer(con, state: Path) -> None:
    ddir = state / "digests"
    ddir.mkdir(parents=True, exist_ok=True)
    pend = [r["digest_date"] for r in con.execute("SELECT digest_date FROM digests WHERE status IN ('pending','failed') ORDER BY digest_date")]
    (ddir / "pending.json").write_text(json.dumps({"pending": pend, "updated_at": D.now_iso()}), encoding="utf-8")


def _photo(m: dict, width: int = 640) -> str | None:
    if not m.get("photos"):
        return None
    u = m["photos"][0]["url"]
    return u.rsplit("/", 1)[0] + f"/{width}" if "images.finder.porsche.com" in u else u


def build_digest(con, cfg: dict, row) -> dict:
    keys = json.loads(row["vehicle_keys"])
    site = cfg["alerts"]["site_url"]
    cars = []
    for k in keys:
        v = con.execute("SELECT merged FROM vehicles WHERE key=?", (k,)).fetchone()
        if not v or not v["merged"]:
            continue
        m = json.loads(v["merged"])
        ev = con.execute("""SELECT detail FROM lifecycle_events WHERE vehicle_key=? AND event='alert_queued'
                            ORDER BY id DESC LIMIT 1""", (k,)).fetchone()
        s = m.get("seller") or {}
        cars.append({
            "key": k, "vin": m.get("vin"), "title": f"{m.get('year') or ''} {m.get('variant_label') or ''}".strip(),
            "reason": json.loads(ev["detail"]).get("reason") if ev else "New listing",
            "category": m["eligibility"]["category"], "eligibility_summary": m["eligibility"]["summary"],
            "checks": [{"label": c["label"], "status": c["status"], "provenance": c["provenance"]} for c in m["eligibility"]["checks"]],
            "price": m.get("price"), "mileage": m.get("mileage"), "transmission": m.get("transmission"),
            "exterior": m.get("exterior_color"), "interior": m.get("interior_color"), "cpo": m.get("cpo"),
            "seats": (m["equipment"].get("seats") or {}).get("value"),
            "weissach": (m["equipment"].get("weissach") or {}).get("value"),
            "pccb": (m["equipment"].get("pccb") or {}).get("value"),
            "seller": s.get("name"), "location": ", ".join(x for x in (s.get("city"), s.get("state")) if x),
            "distance_miles": (m.get("distance") or {}).get("road_miles_est"),
            "score": m["score"]["total"], "strengths": m["score"]["strengths"][:4], "misses": m["score"]["misses"][:4],
            "otd_total": (m.get("otd") or {}).get("total"),
            "deal": {k2: m["valuation"].get(k2) for k2 in ("rating", "delta_pct", "n", "confidence", "fair_low", "fair_high")},
            "photo": _photo(m), "photo_count": (m.get("gallery") or {}).get("captured"),
            "detail_url": f"{site}/#/v/{k}" if site else None,
            "source_urls": [x["url"] for x in m.get("sources") or [] if x.get("url")],
        })
    n = len(cars)
    conf = sum(1 for c in cars if c["category"] == "confirmed")
    subject = (f"[PGT-{row['digest_date']}] {n} new qualifying Porsche GT listing{'s' if n != 1 else ''}"
               + (f" ({conf} confirmed)" if conf else " (needs verification)"))
    digest = {"digest_date": row["digest_date"], "status": row["status"], "subject": subject,
              "recipient": cfg["alerts"]["recipient"], "cars": cars, "site_url": site}
    digest["html"] = render_html(digest)
    digest["text"] = render_text(digest)
    return digest


def _money(x) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "n/a"


def render_text(d: dict) -> str:
    lines = [d["subject"], ""]
    for c in d["cars"]:
        lines += [f"{c['title']} - {_money(c['price'])} - {c['mileage']:,} mi" if c["mileage"] is not None else f"{c['title']} - {_money(c['price'])}",
                  f"  {c['reason']} | {c['category'].upper()}: {c['eligibility_summary']}",
                  f"  {c['exterior'] or '?'} / {c['interior'] or '?'} | {(c['transmission'] or '?').upper()} | {'CPO' if c['cpo'] else 'not CPO'}",
                  f"  {c['seller'] or '?'} - {c['location']} (~{c['distance_miles']} mi from Austin)" if c["distance_miles"] is not None else f"  {c['seller'] or '?'} - {c['location']}",
                  f"  Preference match {c['score']}/100 | Est. out-the-door {_money(c['otd_total'])}",
                  *(f"  + {s}" for s in c["strengths"]), *(f"  - {s}" for s in c["misses"]),
                  f"  {c['detail_url'] or (c['source_urls'][0] if c['source_urls'] else '')}", ""]
    return "\n".join(lines)


def render_html(d: dict) -> str:
    e = html.escape
    parts = [f"""<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:#0d0d0e;color:#f2f0ea;padding:24px">
<div style="max-width:640px;margin:0 auto">
<div style="font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#c8ff2e">GT Scout &middot; {e(d['digest_date'])}</div>
<h1 style="font-size:24px;margin:6px 0 20px;color:#f2f0ea">{len(d['cars'])} new qualifying listing{'s' if len(d['cars']) != 1 else ''}</h1>"""]
    for c in d["cars"]:
        badge = "#c8ff2e" if c["category"] == "confirmed" else "#ffb020"
        badge_txt = "CONFIRMED MATCH" if c["category"] == "confirmed" else "LEAD - VERIFY"
        deal = c["deal"]
        deal_txt = (f"{deal['rating'].title()} deal ({deal['delta_pct']:+.1%} vs {deal['n']} asking comps, {deal['confidence']} confidence)"
                    if deal.get("rating") else "Deal quality: not enough comparables yet")
        img = (f'<img src="{e(c["photo"])}" alt="" width="640" style="width:100%;display:block;border:0">' if c["photo"] else "")
        link = c["detail_url"] or (c["source_urls"][0] if c["source_urls"] else "#")
        checks = "".join(
            f'<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 8px;font-size:11px;border:1px solid '
            f'{"#3a3a3d" if k["status"] == "pass" else "#ffb020"};color:{"#b9b6ad" if k["status"] == "pass" else "#ffb020"}">'
            f'{"&#10003;" if k["status"] == "pass" else "?"} {e(k["label"])}'
            f'{" &middot; " + e(k["provenance"]) if k["provenance"] and k["status"] == "pass" else ""}</span>' for k in c["checks"])
        strengths = "".join(f'<div style="color:#b9b6ad;font-size:13px">+ {e(s)}</div>' for s in c["strengths"])
        misses = "".join(f'<div style="color:#ffb020;font-size:13px">&minus; {e(s)}</div>' for s in c["misses"])
        miles = f"{c['mileage']:,} mi" if c["mileage"] is not None else "mileage n/a"
        dist = f" &middot; ~{c['distance_miles']:,} mi from Austin" if c["distance_miles"] is not None else ""
        parts.append(f"""<div style="background:#161617;margin:0 0 20px;border:1px solid #2a2a2c">
{img}
<div style="padding:16px 18px">
<div style="font-size:11px;letter-spacing:.14em;color:{badge};font-weight:700">{badge_txt} &middot; {e(c['reason']).upper()}</div>
<div style="font-size:20px;font-weight:800;margin:6px 0 2px">{e(c['title'])}</div>
<div style="font-size:15px;color:#f2f0ea"><b>{_money(c['price'])}</b> &middot; {miles} &middot; {e((c['transmission'] or '?').upper())} &middot; {'CPO' if c['cpo'] else 'not CPO'}</div>
<div style="font-size:13px;color:#b9b6ad;margin:4px 0 10px">{e(c['exterior'] or '?')} over {e(c['interior'] or '?')}<br>{e(c['seller'] or '?')} &middot; {e(c['location'])}{dist}</div>
<div style="margin:0 0 8px">{checks}</div>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 10px"><tr>
<td style="padding-right:22px"><div style="font-size:11px;color:#8b887f;text-transform:uppercase;letter-spacing:.1em">Preference match</div><div style="font-size:22px;font-weight:800;color:#c8ff2e">{c['score']}<span style="font-size:12px;color:#8b887f">/100</span></div></td>
<td style="padding-right:22px"><div style="font-size:11px;color:#8b887f;text-transform:uppercase;letter-spacing:.1em">Est. out-the-door</div><div style="font-size:22px;font-weight:800">{_money(c['otd_total'])}</div></td>
</tr></table>
<div style="font-size:13px;color:#b9b6ad;margin-bottom:8px">{e(deal_txt)}</div>
{strengths}{misses}
<div style="margin-top:14px"><a href="{e(link)}" style="background:#c8ff2e;color:#0d0d0e;text-decoration:none;font-weight:800;font-size:13px;padding:10px 16px;display:inline-block;letter-spacing:.06em">VIEW FULL DETAIL &amp; {c['photo_count'] or 0} PHOTOS</a></div>
</div></div>""")
    parts.append(f"""<div style="font-size:11px;color:#8b887f">Sent only on days with newly qualifying cars. Asking prices are not sale prices.
Out-the-door figures are estimates. {('<a style="color:#8b887f" href="' + e(d['site_url']) + '">Open GT Scout</a>') if d['site_url'] else ''}</div></div></div>""")
    return "".join(parts)


# ---------------------------------------------------------------------------
# delivery bookkeeping
# ---------------------------------------------------------------------------
def mark_sent(con, state: Path, digest_date: str, channel: str, message_id: str | None = None, when: str | None = None) -> bool:
    """Mark a digest sent.  Returns False if it was already sent (no-op)."""
    row = con.execute("SELECT status FROM digests WHERE digest_date=?", (digest_date,)).fetchone()
    if not row or row["status"] == "sent":
        return False
    when = when or D.now_iso()
    con.execute("""UPDATE digests SET status='sent', sent_at=?, channel=?, message_id=?, attempts=attempts+1, last_error=NULL
                   WHERE digest_date=?""", (when, channel, message_id, digest_date))
    for k in json.loads(con.execute("SELECT vehicle_keys FROM digests WHERE digest_date=?", (digest_date,)).fetchone()["vehicle_keys"]):
        D.event(con, k, "alert_sent", {"digest": digest_date, "channel": channel}, None, when)
    con.commit()
    write_pending_pointer(con, state)
    return True


def mark_failed(con, digest_date: str, error: str) -> None:
    con.execute("UPDATE digests SET status='failed', attempts=attempts+1, last_error=? WHERE digest_date=? AND status!='sent'",
                (error[:500], digest_date))
    con.commit()


def import_receipts(con, state: Path) -> int:
    """Pick up receipts written by an external sender (state/digests/<date>.sent.json)."""
    n = 0
    for f in sorted((state / "digests").glob("*.sent.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if mark_sent(con, state, f.name[:10], r.get("channel") or "external", r.get("message_id"), r.get("sent_at")):
            n += 1
    return n


def send_smtp(digest: dict) -> str:
    """Send through SMTP using SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM."""
    host, user, pwd = os.environ["SMTP_HOST"], os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg = EmailMessage()
    msg["Subject"] = digest["subject"]
    msg["From"] = os.environ.get("SMTP_FROM", user)
    msg["To"] = digest["recipient"]
    msg["Message-ID"] = f"<pgt-{digest['digest_date']}@gt-scout>"
    msg.set_content(digest["text"])
    msg.add_alternative(digest["html"], subtype="html")
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", "465")), timeout=30) as s:
        s.login(user, pwd)
        s.send_message(msg)
    return msg["Message-ID"]
