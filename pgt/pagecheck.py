"""Read a car's gearbox from the dealer's own listing page.

The dealer feed reports "Automatic" as a default, so for cars that are not on Porsche Finder the gearbox is
unknown.  The dealer's page for the car usually states it.  One polite request per listing (robots.txt
respected, honest user agent, no retries on refusal); the answer is stored and reused.

Only specific wording counts.  "Manual", "6-speed manual", "PDK", "Doppelkupplung", "dual-clutch" and
"7-speed" are evidence; a bare "Automatic" is the same default the feed carries and proves nothing.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import datetime, timedelta, timezone

from . import db as D

SPECIFIC = ("manual", "pdk")

_LD_RE = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META_RE = re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og:title|og:description|description|twitter:title)[\"'][^>]*>", re.I)
_CONTENT_RE = re.compile(r"content=[\"']([^\"']*)[\"']", re.I)
_LABEL_RE = re.compile(r"\btransmission(?:\s+(?:type|description))?\s*:?\s*\n?\s*([^\n]{2,70})", re.I)
_NOT_GEARBOX = re.compile(r"owner'?s?\s+manuals?|manuals?\s*(?:,|and|&)\s*(?:books?|keys?|records?)|service manual|"
                          r"manual\s+(?:shift\s+)?mode|manual(?:ly)?\s+adjust\w*|manual\s+(?:seats?|climate|air)", re.I)
_MANUAL = re.compile(r"\bmanual\b|\b6[\s-]*speed\s*m/?t\b|\bstick[\s-]?shift\b|\b6MT\b", re.I)
_PDK = re.compile(r"\bPDK\b|doppelkupplung|dual[\s-]*clutch|\b7[\s-]*speed\b|\bDCT\b", re.I)


def kind_of(value: str | None) -> str | None:
    """'manual' | 'pdk' | 'conflict' for specific wording, else None (a bare "Automatic" is None)."""
    t = _NOT_GEARBOX.sub(" ", value or "")
    man, pdk = bool(_MANUAL.search(t)), bool(_PDK.search(t))
    if man and pdk:
        return "conflict"
    return "manual" if man else "pdk" if pdk else None


def _walk(node, vin: str, out: list[str]) -> None:
    if isinstance(node, dict):
        other_vin = node.get("vehicleIdentificationNumber") or node.get("vin")
        mine = not other_vin or str(other_vin).upper() == vin
        tx = node.get("vehicleTransmission")
        if tx and mine:
            out.append(tx.get("name") if isinstance(tx, dict) else str(tx))
        if mine and isinstance(node.get("name"), str) and node.get("@type") in ("Vehicle", "Car", "Product"):
            out.append(node["name"])
        for v in node.values():
            _walk(v, vin, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, vin, out)


def read_page(page: str, vin: str) -> dict:
    """{'result': manual|pdk|conflict|inconclusive|vin_missing, 'evidence': str|None} from a listing page."""
    vin = (vin or "").upper()
    if not vin or vin not in page.upper():
        return {"result": "vin_missing", "evidence": None}      # not this car's page (redirect, sold, bot wall)
    stated: list[str] = []
    for block in _LD_RE.findall(page):
        try:
            _walk(json.loads(htmllib.unescape(block).strip()), vin, stated)
        except (ValueError, TypeError):
            continue
    head = [htmllib.unescape(_TAG_RE.sub(" ", t)) for t in _TITLE_RE.findall(page)[:1]]
    for tag in _META_RE.findall(page):
        m = _CONTENT_RE.search(tag)
        if m:
            head.append(htmllib.unescape(m.group(1)))
    text = htmllib.unescape(_TAG_RE.sub("\n", _SCRIPT_RE.sub("\n", page)))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    labelled = [m.group(1).strip() for m in _LABEL_RE.finditer(text)]
    found: dict[str, str] = {}
    for value in stated + labelled + head:
        k = kind_of(value)
        if k in SPECIFIC and k not in found:
            found[k] = re.sub(r"\s+", " ", value).strip()[:90]
        elif k == "conflict" and "conflict" not in found:
            found["conflict"] = re.sub(r"\s+", " ", value).strip()[:90]
    kinds = [k for k in SPECIFIC if k in found]
    if len(kinds) == 1 and "conflict" not in found:
        return {"result": kinds[0], "evidence": found[kinds[0]]}
    if kinds or "conflict" in found:
        return {"result": "conflict", "evidence": " / ".join(found.values())[:180]}
    return {"result": "inconclusive", "evidence": (labelled[0][:90] if labelled else None)}


def candidates(con, cfg: dict, now: str, limit: int) -> list[dict]:
    """Live feed listings of GT3s whose gearbox is still unknown, most useful first."""
    pc = cfg.get("page_checks") or {}
    retry = (datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=int(pc.get("retry_after_days", 14)))
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = con.execute("""
        SELECT l.source, l.source_listing_id, l.url, v.key, v.vin, v.generation, v.category, v.merged
        FROM listings l JOIN vehicles v ON v.key = l.vehicle_key
        WHERE l.status = 'active' AND l.source != 'porsche_finder' AND l.url LIKE 'http%'
          AND v.status = 'active' AND v.variant IN ('gt3', 'gt3_touring') AND v.vin IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM page_checks p WHERE p.listing_key = l.source || ':' || l.source_listing_id
                          AND (p.result IN ('manual', 'pdk') OR p.checked_at > ?))""", (retry,)).fetchall()
    out = []
    for r in rows:
        m = json.loads(r["merged"]) if r["merged"] else {}
        if m.get("transmission") and not (m.get("transmission_note") or "").startswith("Per the dealer feed only"):
            continue                                    # already settled by a trusted source
        # manual is a hard requirement on the 991.2, so those first; then cars still in contention
        rank = (0 if r["generation"] == "991.2" else 1, {"confirmed": 0, "lead": 0, "near_miss": 1}.get(r["category"], 2))
        out.append({"listing_key": f"{r['source']}:{r['source_listing_id']}", "url": r["url"], "vin": r["vin"],
                    "key": r["key"], "rank": rank})
    out.sort(key=lambda c: (c["rank"], c["listing_key"]))
    return out[:limit]


def run(con, cfg: dict, fetcher, now: str | None = None, limit: int | None = None) -> dict:
    now = now or D.now_iso()
    pc = cfg.get("page_checks") or {}
    if not pc.get("enabled", True):
        return {"enabled": False}
    todo = candidates(con, cfg, now, int(limit if limit is not None else pc.get("max_per_run", 60)))
    tally: dict[str, int] = {}
    for c in todo:
        resp = fetcher.get(c["url"])
        if resp.status == -1:
            res = {"result": "robots", "evidence": None}
        elif resp.status != 200 or not resp.text:
            res = {"result": "blocked" if resp.status in (401, 403, 406, 429, 503) else "error", "evidence": resp.error}
        else:
            res = read_page(resp.text, c["vin"])
        tally[res["result"]] = tally.get(res["result"], 0) + 1
        con.execute("""INSERT INTO page_checks(listing_key, vin, url, checked_at, http_status, result, evidence)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(listing_key) DO UPDATE SET vin=excluded.vin, url=excluded.url,
                         checked_at=excluded.checked_at, http_status=excluded.http_status, result=excluded.result,
                         evidence=excluded.evidence""",
                    (c["listing_key"], c["vin"], c["url"], now, resp.status, res["result"],
                     (res["evidence"] or None) and str(res["evidence"])[:200]))
    con.commit()
    return {"enabled": True, "checked": len(todo), "results": tally,
            "settled": tally.get("manual", 0) + tally.get("pdk", 0)}


def stated_for(con, listing_keys: list[str]) -> dict | None:
    """The stored page reading for any of a vehicle's listings, if it settled the gearbox."""
    if not listing_keys:
        return None
    marks = ",".join("?" * len(listing_keys))
    rows = con.execute(f"SELECT result, evidence, url, checked_at FROM page_checks WHERE listing_key IN ({marks}) "
                       "AND result IN ('manual', 'pdk') ORDER BY checked_at DESC", listing_keys).fetchall()
    kinds = {r["result"] for r in rows}
    return dict(rows[0]) if len(kinds) == 1 else None
