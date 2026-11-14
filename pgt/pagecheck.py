"""Read the dealer's own listing page for what the feed leaves out or gets wrong.

For every listing that is not on Porsche Finder, the dealer's page is read on a schedule (robots.txt
respected, honest user agent, one request, no retries on refusal).  From it:

  * gearbox    the feed reports "Automatic" as a default; only specific wording on the page counts
               ("6-speed manual", "PDK", "Doppelkupplung", "dual-clutch", "7-speed")
  * price      when the feed has none: structured data for this VIN, a labelled price, or the only price on the page
  * equipment  front axle lift, ceramic brakes, Weissach, bucket / 18-way seats when the page names them
  * still for sale?  a page that says sold, or that has gone (404/410/redirect), is evidence the car is no
               longer listed even while the feed keeps returning it

Everything read here is a dealer's statement, never verification, and is labelled that way downstream.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import db as D

SPECIFIC = ("manual", "pdk")

_LD_RE = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_META_RE = re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og:title|og:description|description|twitter:title)[\"'][^>]*>", re.I)
_META_PRICE_RE = re.compile(r"<meta[^>]+(?:property|name|itemprop)=[\"'](?:product:price:amount|og:price:amount|price)[\"'][^>]*>", re.I)
_CONTENT_RE = re.compile(r"content=[\"']([^\"']*)[\"']", re.I)
_LABEL_RE = re.compile(r"\btransmission(?:\s+(?:type|description))?\s*:?\s*\n?\s*([^\n]{2,70})", re.I)
_NOT_GEARBOX = re.compile(r"owner'?s?\s+manuals?|manuals?\s*(?:,|and|&)\s*(?:books?|keys?|records?)|service manual|"
                          r"manual\s+(?:shift\s+)?mode|manual(?:ly)?\s+adjust\w*|manual\s+(?:seats?|climate|air)", re.I)
_MANUAL = re.compile(r"\bmanual\b|\b6[\s-]*(?:speed|spd)\s*m/?t\b|\bstick[\s-]?shift\b|\b6MT\b", re.I)
_PDK = re.compile(r"\bPDK\b|doppelkupplung|(?:dual|double)[\s-]*clutch|\b7[\s-]*(?:speed|spd)\b|\bDCT\b", re.I)
# "PDK manual", "auto-shift manual", "automated manual" all describe the PDK, not a manual gearbox
_AUTOMATED = re.compile(r"(?:pdk|auto(?:mated|[\s-]?shift)?|(?:dual|double)[\s-]*clutch)[\s-]+manual", re.I)

# price
PRICE_MIN, PRICE_MAX = 120_000, 1_500_000      # no tracked model is advertised below this
_AMOUNT = r"\$\s?(\d{2,3}(?:,\d{3})+|\d{5,7})(?:\.\d{2})?(?!\s*(?:/|per|a)\s*mo)"
_PRICE_LABEL_RE = re.compile(r"([^\n$]{0,45}\b(?:price|asking)\b[^\n$]{0,25})\s*:?\s*\n?\s*" + _AMOUNT, re.I)
_ANY_PRICE_RE = re.compile(r"([^\n$]{0,30})" + _AMOUNT, re.I)
_NOT_ASKING = re.compile(r"msrp|original|sticker|\bwas\b|sav(?:e|ings)|discount|rebate|down\b|payment|monthly|financ|estimat|"
                         r"trade|deposit|options?\b|new\s+price|window", re.I)
_ON_REQUEST = re.compile(r"call (?:us )?for (?:the )?pric|contact (?:us )?for (?:the )?pric|price (?:up)?on request|"
                         r"inquire for pric|request (?:a )?price|\bP\.?O\.?A\b|call for details", re.I)

# still for sale?
_SOLD_HEAD = re.compile(r"\bsold\b(?!\s+(?:in|by|at|to|new|here|with|as|and\s+serviced)\b)", re.I)
_SOLD_BODY = re.compile(r"this\s+(?:vehicle|car|listing|porsche|unit)\s+(?:has\s+(?:already\s+)?been|is|was)\s+(?:sold|no\s+longer\s+available)|"
                        r"the\s+(?:vehicle|car)\s+has\s+(?:already\s+)?been\s+sold|"
                        r"no\s+longer\s+(?:available|in\s+(?:our\s+)?(?:inventory|stock))|"
                        r"\bstatus\s*:?\s*\n?\s*sold\b|\bprice\s*:?\s*\n?\s*sold\b|\bjust\s+sold\b|\bsold\s*!", re.I)
_CONDITIONAL = re.compile(r"\b(?:before|until|once|when|if|after|time|whether)\b[^.\n]{0,25}$", re.I)   # "before this car is sold"
# where a listing page stops being about this car
_OTHER_CARS = re.compile(r"similar\s+(?:vehicles|inventory|cars|listings)|you\s+may\s+also\s+like|recommended\s+(?:vehicles|for\s+you)|"
                         r"view\s+all\s+inventory|other\s+vehicles|more\s+(?:vehicles|inventory)|recently\s+(?:viewed|sold)|related\s+(?:vehicles|inventory)", re.I)
_PENDING = re.compile(r"sale\s+pending|pending\s+sale|\bdeposit\s+(?:taken|received)\b|\bunder\s+contract\b", re.I)
_LD_GONE = re.compile(r"SoldOut|OutOfStock|Discontinued", re.I)

# equipment the page names
_PCCB = re.compile(r"\bPCCB\b|ceramic\s+composite\s+brake|carbon[\s-]*ceramic\s+brake", re.I)
_BUCKET = re.compile(r"full\s+bucket\s+seat|carbon(?:\s+fib(?:er|re))?\s+(?:full\s+)?bucket|lightweight\s+bucket|\bbucket\s+seats?\b", re.I)
_ADJ = re.compile(r"\b18[\s-]*way\b|adaptive\s+sport\s+seats", re.I)
_LIFT = re.compile(r"front[\s-]*axle\s+lift|front[\s-]*(?:end\s+)?lift(?:\s+system)?\b|nose[\s-]*lift|\blift\s+system\b", re.I)
_WEISSACH = re.compile(r"\bweissach\b", re.I)


def kind_of(value: str | None) -> str | None:
    """'manual' | 'pdk' | 'conflict' for specific wording, else None (a bare "Automatic" is None)."""
    t = _AUTOMATED.sub(" PDK ", _NOT_GEARBOX.sub(" ", value or ""))
    man, pdk = bool(_MANUAL.search(t)), bool(_PDK.search(t))
    if man and pdk:
        return "conflict"
    return "manual" if man else "pdk" if pdk else None


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return f if PRICE_MIN <= f <= PRICE_MAX else None


def _walk(node, vin: str, out: dict) -> None:
    if isinstance(node, dict):
        other_vin = node.get("vehicleIdentificationNumber") or node.get("vin")
        mine = not other_vin or str(other_vin).upper() == vin
        tx = node.get("vehicleTransmission")
        if tx and mine:
            out["gearbox"].append(tx.get("name") if isinstance(tx, dict) else str(tx))
        if mine and isinstance(node.get("name"), str) and node.get("@type") in ("Vehicle", "Car", "Product"):
            out["gearbox"].append(node["name"])
        offers = node.get("offers")
        if mine and offers and (other_vin or node.get("@type") in ("Vehicle", "Car", "Product")):
            for o in offers if isinstance(offers, list) else [offers]:
                if isinstance(o, dict):
                    p = _num(o.get("price") or (o.get("priceSpecification") or {}).get("price"))
                    if p:
                        out["prices"].append(p)
                    if o.get("availability"):
                        out["availability"].append(str(o["availability"]))
        for v in node.values():
            _walk(v, vin, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, vin, out)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def read_page(page: str, vin: str) -> dict:
    """What a listing page says about one car.

    {'result': manual|pdk|conflict|inconclusive|vin_missing, 'evidence': str|None, 'data': {...}} where data
    may hold price, price_evidence, price_on_request, avail ('sold'|'pending'|'ok'), avail_evidence and the
    equipment flags lift / pccb / weissach / seats.
    """
    vin = (vin or "").upper()
    head = [htmllib.unescape(_TAG_RE.sub(" ", t)) for t in _TITLE_RE.findall(page)[:1] + _H1_RE.findall(page)[:1]]
    if not vin or vin not in page.upper():
        # not (or no longer) this car's page; a "sold" headline is still worth keeping
        data = ({"avail": "gone", "avail_evidence": f"the page no longer shows this car and is headed \u201c{_clean(head[0])[:60]}\u201d"}
                if head and _SOLD_HEAD.search(head[0]) else {})
        return {"result": "vin_missing", "evidence": None, "data": data}
    ld = {"gearbox": [], "prices": [], "availability": []}
    for block in _LD_RE.findall(page):
        try:
            _walk(json.loads(htmllib.unescape(block).strip()), vin, ld)
        except (ValueError, TypeError):
            continue
    for tag in _META_RE.findall(page):
        m = _CONTENT_RE.search(tag)
        if m:
            head.append(htmllib.unescape(m.group(1)))
    text = htmllib.unescape(_TAG_RE.sub("\n", _SCRIPT_RE.sub("\n", page)))
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    data: dict = {}

    # -- gearbox: a labelled value is short and starts with a word; prose after the word "transmission" is not one
    labelled = [v for v in (m.group(1).strip() for m in _LABEL_RE.finditer(text))
                if v[:1].isalnum() and len(v.split()) <= 7]
    found: dict[str, str] = {}
    for value in ld["gearbox"] + labelled + head:
        k = kind_of(value)
        if k and k not in found:
            found[k] = _clean(value)[:90]
    kinds = [k for k in SPECIFIC if k in found]
    if len(kinds) == 1 and "conflict" not in found:
        result, evidence = kinds[0], found[kinds[0]]
    elif kinds or "conflict" in found:
        result, evidence = "conflict", " / ".join(found.values())[:180]
    else:
        result, evidence = "inconclusive", (labelled[0][:90] if labelled else None)

    # -- price: structured data for this car, then a labelled asking price, then the only price on the page
    meta_prices = [p for p in (_num((_CONTENT_RE.search(t) or [None, None])[1]) for t in _META_PRICE_RE.findall(page)) if p]
    labelled_prices = [(_num(m.group(2)), _clean(m.group(1))) for m in _PRICE_LABEL_RE.finditer(text)
                       if not _NOT_ASKING.search(m.group(1))]
    labelled_prices = [(p, lab) for p, lab in labelled_prices if p]
    loose = {p for p in (_num(m.group(2)) for m in _ANY_PRICE_RE.finditer(text) if not _NOT_ASKING.search(m.group(1))) if p}
    if ld["prices"]:
        data["price"], data["price_evidence"] = ld["prices"][0], "the page's structured data for this car"
    elif meta_prices:
        data["price"], data["price_evidence"] = meta_prices[0], "the page's price tag"
    elif labelled_prices and len({p for p, _ in labelled_prices}) == 1:
        data["price"], data["price_evidence"] = labelled_prices[0][0], f"“{labelled_prices[0][1][:40]}” on the page"
    elif not labelled_prices and len(loose) == 1:
        data["price"], data["price_evidence"] = next(iter(loose)), "the only price shown on the page"
    if "price" not in data and _ON_REQUEST.search(text):
        data["price_on_request"] = True

    # -- still for sale?  Only the part of the page about this car counts, not the other cars shown under it.
    cut = _OTHER_CARS.search(text, min(400, len(text) // 4))      # skip the site's top menu
    text = text[:cut.start()] if cut else text
    sold_ev, strong = None, False
    if any(_LD_GONE.search(a) for a in ld["availability"]):
        sold_ev, strong = "the page's structured data marks it sold or out of stock", True
    elif any(_SOLD_HEAD.search(h) for h in head[:2]):
        sold_ev, strong = _clean(next(h for h in head[:2] if _SOLD_HEAD.search(h)))[:90], True
    else:
        m = next((m for m in _SOLD_BODY.finditer(text) if not _CONDITIONAL.search(text[max(0, m.start() - 40):m.start()])), None)
        if m:
            sold_ev = _clean(text[max(0, m.start() - 20):m.end() + 20])[:90]
    if sold_ev:
        # a headline or structured data is taken at once; a sentence in the body is confirmed on a second day
        data["avail"], data["avail_evidence"], data["avail_strong"] = "sold", sold_ev, strong
    else:
        m = _PENDING.search(text)
        data["avail"] = "pending" if m else "ok"
        if m:
            data["avail_evidence"] = _clean(text[max(0, m.start() - 20):m.end() + 20])[:90]

    # -- equipment the page names (a dealer's statement, not a build sheet)
    if _LIFT.search(text):
        data["lift"] = True
    if _PCCB.search(text):
        data["pccb"] = True
    if _WEISSACH.search(text):
        data["weissach"] = True
    if _BUCKET.search(text):
        data["seats"] = "bucket"
    elif _ADJ.search(text):
        data["seats"] = "adjustable"
    return {"result": result, "evidence": evidence, "data": data}


# ---------------------------------------------------------------------------------- scheduling
def candidates(con, cfg: dict, now: str, limit: int) -> list[dict]:
    """Live listings outside Porsche Finder that are due a page read, most useful first."""
    pc = cfg.get("page_checks") or {}
    now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
    retry_days = {"robots": float(pc.get("retry_after_days", 14)), "blocked": float(pc.get("retry_blocked_days", 3)),
                  "error": float(pc.get("retry_blocked_days", 3))}
    recheck = timedelta(days=float(pc.get("recheck_days", 7)))
    rows = con.execute("""
        SELECT l.source, l.source_listing_id, l.url, v.key, v.vin, v.variant, v.generation, v.category,
               v.merged, p.result, p.checked_at, p.avail, p.avail_count, p.data AS pdata
        FROM listings l JOIN vehicles v ON v.key = l.vehicle_key
        LEFT JOIN page_checks p ON p.listing_key = l.source || ':' || l.source_listing_id
        WHERE l.status != 'removed' AND l.source != 'porsche_finder' AND l.url LIKE 'http%'
          AND v.status IN ('active', 'unverified') AND v.vin IS NOT NULL""").fetchall()
    out = []
    for r in rows:
        m = json.loads(r["merged"]) if r["merged"] else {}
        age = now_dt - datetime.fromisoformat(r["checked_at"].replace("Z", "+00:00")) if r["checked_at"] else None
        in_play = r["category"] in ("confirmed", "lead", "near_miss")
        no_price = m.get("price") is None
        no_gearbox = r["variant"] in ("gt3", "gt3_touring") and (
            not m.get("transmission") or (m.get("transmission_note") or "").startswith("Per the dealer feed only"))
        if age is None or r["pdata"] is None:
            rank = 1                                             # never read (or read before prices and status were kept)
        elif (r["avail"] == "gone" or (r["avail"] == "sold" and not json.loads(r["pdata"]).get("avail_strong"))) and (r["avail_count"] or 0) < 2:
            if age < timedelta(hours=20):
                continue
            rank = 0                                             # a missing page is confirmed on a second day
        elif r["avail"] in ("sold", "gone"):
            continue                                             # settled
        elif r["result"] in ("blocked", "robots", "error"):
            if age < timedelta(days=retry_days[r["result"]]):
                continue
            rank = 4
        else:
            if age < recheck:
                continue
            rank = 3                                             # routine "is it still for sale" re-read
        if (r["pdata"] is None and r["result"] in ("blocked", "robots", "error") and age is not None
                and age < timedelta(days=retry_days[r["result"]])):
            continue                                             # refused recently; do not ask again yet
        want = (0 if (no_price and in_play) else 1, 0 if (no_gearbox and r["generation"] == "991.2") else 1, 0 if in_play else 1)
        out.append({"listing_key": f"{r['source']}:{r['source_listing_id']}", "url": r["url"], "vin": r["vin"], "key": r["key"],
                    "rank": (rank, *want, r["checked_at"] or "")})
    out.sort(key=lambda c: (c["rank"], c["listing_key"]))
    return out[:limit]


def _moved(requested: str, final: str | None) -> bool:
    """True when the site answered with a different page (typically the inventory list or home page)."""
    if not final:
        return False
    a, b = urlparse(requested), urlparse(final)
    host = lambda u: u.netloc.split(":")[0].lower().removeprefix("www.")   # noqa: E731
    return host(a) == host(b) and a.path.rstrip("/").lower() != b.path.rstrip("/").lower()


def run(con, cfg: dict, fetcher, now: str | None = None, limit: int | None = None) -> dict:
    now = now or D.now_iso()
    pc = cfg.get("page_checks") or {}
    if not pc.get("enabled", True):
        return {"enabled": False}
    todo = candidates(con, cfg, now, int(limit if limit is not None else pc.get("max_per_run", 150)))
    tally: dict[str, int] = {}
    found = {"prices": 0, "sold": 0, "gone": 0, "settled": 0}
    gap, refused_run, stopped = float(pc.get("global_interval_seconds", 1.5)), 0, None
    for n, c in enumerate(todo):
        # Many dealer sites share one hosting platform, so requests are spaced overall, not only per site,
        # and a run of refusals means the platform is throttling us: stop and leave the rest for tomorrow.
        if refused_run >= int(pc.get("stop_after_refusals", 12)):
            stopped = f"stopped after {refused_run} refusals in a row; {len(todo) - n} pages left for the next run"
            break
        if n and gap and not getattr(fetcher, "no_wait", False):
            time.sleep(gap)
        resp = fetcher.get(c["url"])
        final = getattr(resp, "final_url", None)
        if resp.status == -1:
            res = {"result": "robots", "evidence": None, "data": {}}
        elif resp.status in (404, 410):
            res = {"result": "gone", "evidence": f"HTTP {resp.status}",
                   "data": {"avail": "gone", "avail_evidence": f"the page returns HTTP {resp.status}"}}
        elif resp.status != 200 or not resp.text:
            refused = resp.status in (202, 401, 403, 406, 429, 503) or (resp.error or "").startswith(("HTTP 429", "HTTP 503"))
            res = {"result": "blocked" if refused else "error", "evidence": resp.error, "data": {}}
        else:
            res = read_page(resp.text, c["vin"])
            if res["result"] == "vin_missing" and "avail" not in res["data"] and _moved(c["url"], final):
                res["result"] = "gone"
                res["data"] = {"avail": "gone", "avail_evidence": "the link now leads to a different page that does not show this car"}
        tally[res["result"]] = tally.get(res["result"], 0) + 1
        refused_run = refused_run + 1 if res["result"] == "blocked" else 0
        old = con.execute("SELECT * FROM page_checks WHERE listing_key=?", (c["listing_key"],)).fetchone()
        old_data = json.loads(old["data"]) if old and old["data"] else {}
        data = dict(res["data"])
        # keep what an earlier successful read established when this one could not see the page
        if res["result"] in ("blocked", "robots", "error", "vin_missing", "gone"):
            data = {**{k: v for k, v in old_data.items() if k not in ("avail", "avail_evidence")}, **data}
        gearbox, evidence = res["result"], res["evidence"]
        if gearbox in SPECIFIC:
            data["gearbox"], data["gearbox_evidence"] = gearbox, evidence
        elif old and gearbox != "conflict" and (old_data.get("gearbox") or old["result"]) in SPECIFIC:
            data["gearbox"] = old_data.get("gearbox") or old["result"]
            data["gearbox_evidence"] = old_data.get("gearbox_evidence") or old["evidence"]
        avail = data.get("avail")
        if avail in ("sold", "gone"):
            same = bool(old) and old["avail"] == avail
            new_day = not old or (old["checked_at"] or "")[:10] != now[:10]
            count = ((old["avail_count"] or 0) + (1 if new_day else 0)) if same else 1
            first = old["avail_first"] if same and old["avail_first"] else now
        else:
            if avail not in ("ok", "pending"):
                avail = old["avail"] if old and res["result"] in ("blocked", "robots", "error") else None
            count, first = 0, None
        found["prices"] += 1 if res["data"].get("price") else 0
        found["sold"] += 1 if res["data"].get("avail") == "sold" else 0
        found["gone"] += 1 if res["data"].get("avail") == "gone" else 0
        found["settled"] += 1 if gearbox in SPECIFIC else 0
        con.execute("""INSERT INTO page_checks(listing_key, vin, url, checked_at, http_status, result, evidence, data, avail, avail_first, avail_count)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(listing_key) DO UPDATE SET vin=excluded.vin, url=excluded.url, checked_at=excluded.checked_at,
                         http_status=excluded.http_status, result=excluded.result, evidence=excluded.evidence, data=excluded.data,
                         avail=excluded.avail, avail_first=excluded.avail_first, avail_count=excluded.avail_count""",
                    (c["listing_key"], c["vin"], c["url"], now, resp.status, gearbox, (evidence or None) and str(evidence)[:200],
                     json.dumps(data, ensure_ascii=False), avail, first, count))
    con.commit()
    return {"enabled": True, "checked": sum(tally.values()), "stopped": stopped, "results": tally, "settled": found["settled"], "prices_found": found["prices"],
            "sold": found["sold"], "gone": found["gone"]}


# ---------------------------------------------------------------------------------- lookups for the merge
def readings(con, listing_keys: list[str]) -> dict[str, dict]:
    """{listing_key: {result, evidence, checked_at, avail, avail_first, avail_count, data}} for a vehicle's listings."""
    if not listing_keys:
        return {}
    marks = ",".join("?" * len(listing_keys))
    out = {}
    for r in con.execute(f"SELECT * FROM page_checks WHERE listing_key IN ({marks})", listing_keys):
        d = dict(r)
        d["data"] = json.loads(r["data"]) if r["data"] else {}
        out[r["listing_key"]] = d
    return out


def gearbox_from(rows: dict[str, dict]) -> dict | None:
    """The page-stated gearbox if the vehicle's pages agree."""
    hits = []
    for r in rows.values():
        g = r["data"].get("gearbox") or (r["result"] if r["result"] in SPECIFIC else None)
        if g in SPECIFIC:
            hits.append({"result": g, "evidence": r["data"].get("gearbox_evidence") or r["evidence"], "checked_at": r["checked_at"]})
    return hits[0] if hits and len({h["result"] for h in hits}) == 1 else None
