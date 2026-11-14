"""Porsche Finder (finder.porsche.com) adapter.

Access path (all permitted by the site's robots.txt, no search pages, no /api):
  1. /sitemap.xml                         -> index of per-dealer sitemaps
  2. /us/en-US/sitemap-dealer/<id>/sitemap.xml -> every listing URL of that dealer (+ lastmod)
  3. /us/en-US/details/<slug>             -> listing page; the server-rendered HTML embeds the
     complete structured record (VIN, factory option codes, CPO flag, seller, every gallery image id).
"""
from __future__ import annotations

import json
import re

from .. import classify as C
from ..http import Fetcher
from .base import Listing, SourceResult

SOURCE = "porsche_finder"
BASE = "https://finder.porsche.com"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"
IMAGE_HOST = "https://images.finder.porsche.com"

_DEALER_SITEMAP_RE = re.compile(r"https://finder\.porsche\.com/us/en-US/sitemap-dealer/(\d+)/sitemap\.xml")
_DETAIL_RE = re.compile(
    r"https://finder\.porsche\.com/us/en-US/details/(porsche-[a-z0-9-]+?)-(new|preowned)-([A-Z0-9]{5,8})(?=[<\s\"])")
_URL_ENTRY_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_STATE_RE = re.compile(r",\s*([A-Z]{2})\s*,?\s*(\d{5})")
_MSRP_RE = re.compile(r"\bMSRP", re.I)
_ADDON_RE = re.compile(r"add-?\s?ons?\b|adjustment|market", re.I)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def slug_variant(slug: str) -> str | None:
    """Target variant for a listing slug such as 'porsche-911-gt3-with-touring-package'."""
    return C.classify_text(slug.replace("porsche-", "", 1))


def parse_sitemap_index(xml: str) -> list[str]:
    return sorted(set(_DEALER_SITEMAP_RE.findall(xml)), key=int)


def parse_dealer_sitemap(xml: str) -> list[dict]:
    """All listing URLs in a dealer sitemap: [{url, slug, condition, id, lastmod, variant}]."""
    out = []
    for block in _URL_ENTRY_RE.findall(xml):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        m = _DETAIL_RE.match(loc.group(1) + " ")
        if not m:
            continue
        lastmod = _LASTMOD_RE.search(block)
        out.append({"url": loc.group(1), "slug": m.group(1), "condition": m.group(2), "id": m.group(3),
                    "lastmod": lastmod.group(1) if lastmod else None, "variant": slug_variant(m.group(1))})
    return out


# ---------------------------------------------------------------------------
# detail page parsing
# ---------------------------------------------------------------------------
def extract_rsc(html: str) -> str:
    """Concatenate the React Server Components stream embedded in the page."""
    parts = []
    for m in _PUSH_RE.finditer(html):
        try:
            parts.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def _match_json(s: str, start: int) -> int:
    """Index just past the JSON value starting at s[start] ('{' or '['), or -1."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def find_objects(rsc: str, prefix: str = '{"listingId"') -> list[dict]:
    out, pos = [], -1
    while True:
        pos = rsc.find(prefix, pos + 1)
        if pos < 0:
            break
        end = _match_json(rsc, pos)
        if end < 0:
            continue
        try:
            out.append(json.loads(rsc[pos:end]))
        except json.JSONDecodeError:
            continue
    return out


def _text_row(rsc: str, ref: str) -> str | None:
    """Resolve an RSC text reference like '$72' -> body of row '72:T<hexlen>,<text>'."""
    m = re.search(r"(?:^|\n)" + re.escape(ref) + r":T([0-9a-f]+),", rsc)
    if not m:
        return None
    n = int(m.group(1), 16)
    body = rsc[m.end():].encode("utf-8")[:n]
    return body.decode("utf-8", errors="ignore")


def _clean(value, rsc: str = ""):
    """Recursively turn RSC sentinels into plain values ('$undefined' -> None, '$$x' -> '$x')."""
    if isinstance(value, dict):
        return {k: _clean(v, rsc) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, rsc) for v in value]
    if isinstance(value, str):
        if value == "$undefined":
            return None
        if value.startswith("$$"):
            return value[1:]
        if re.fullmatch(r"\$[0-9a-f]{1,4}", value):
            return _text_row(rsc, value[1:])
        if value.startswith("$@") or value.startswith("$L") or re.fullmatch(r"\$[0-9a-f]+:.*", value):
            return None
    return value


def _money(text) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(text))
    return float(m.group(0).replace(",", "")) if m else None


def parse_detail(html: str, url: str, scope: str | None = None) -> Listing | None:
    """Parse a listing page.  Returns None when the page carries no listing record."""
    rsc = extract_rsc(html)
    objs = find_objects(rsc)
    main = next((o for o in objs if isinstance(o.get("sections"), dict) and isinstance(o.get("meta"), dict)), None)
    if main is None:
        return None
    datalayer = next((o for o in objs if "realcarStatus" in o or "modelCode" in o), {}) or {}
    main = _clean(main, rsc)
    meta, sec = main["meta"], main["sections"]
    info = sec.get("info") or {}
    equipment = sec.get("equipment") or {}
    seller = meta.get("seller") or info.get("seller") or {}
    comp = seller.get("addressComponents") or {}

    lst = Listing(source=SOURCE, source_listing_id=str(main.get("listingId")), url=meta.get("detailsUrl") or url,
                  scope=scope or (str(seller.get("sellerId")) if seller.get("sellerId") else None))
    lst.extra["listed"] = main.get("listed")
    lst.extra["inventory_id"] = main.get("inventoryId")
    lst.extra["model_code"] = datalayer.get("modelCode")
    lst.extra["exterior_color_code"] = datalayer.get("exteriorColorCode")
    lst.extra["commission_number"] = info.get("commissionNumber")
    lst.vin = meta.get("vin") or info.get("vin")
    title = (sec.get("title") or {}).get("title") or meta.get("title")
    lst.title = title
    cls = C.classify(meta.get("modelCategory") or meta.get("title") or title, lst.vin, meta.get("modelYear"),
                     platform_text=meta.get("model"))
    lst.variant, lst.generation, lst.year, lst.issues = cls["variant"], cls["generation"], cls["year"], cls["issues"]
    lst.condition = "new" if (meta.get("condition") or info.get("condition")) == "new" else "used"
    lst.cpo = bool(info.get("porscheApproved") or (sec.get("conditionAndHistory") or {}).get("porscheApproved"))

    # -- price: the headline figure may include dealer fees; keep the vehicle price separately
    lst.price_as_listed = _money(meta.get("priceValue")) or _money(info.get("price"))
    breakdown = (info.get("overviewBreakdown") or {}).get("items") or []
    vehicle_price, fees = None, []
    for item in breakdown:
        label, val = (item.get("label") or "").strip(), _money(item.get("value"))
        if val is None:
            continue
        if re.search(r"vehicle price", label, re.I):
            vehicle_price = val
        else:
            fees.append({"label": label, "amount": val})
    lst.fee_items = fees
    lst.dealer_fees_listed = round(sum(f["amount"] for f in fees), 2) if fees else None
    lst.price = vehicle_price if vehicle_price is not None else lst.price_as_listed

    mil = meta.get("mileage") or {}
    if isinstance(mil.get("value"), (int, float)):
        miles = float(mil["value"])
        lst.mileage = int(round(miles * 0.621371)) if mil.get("unitCode") == "KMT" else int(round(miles))
    summary = sec.get("summary") or {}
    ext = summary.get("exteriorColor") or {}
    lst.exterior_color = re.sub(r"\bGt\b", "GT", ext.get("name") or meta.get("color") or "") or None
    lst.exterior_hex = ext.get("value")
    lst.interior_color = (summary.get("interior") or {}).get("name") or meta.get("interiorName") or meta.get("interiorColor")
    lst.transmission_raw = meta.get("transmission")
    lst.transmission = C.transmission_kind(meta.get("transmission"))

    state = None
    m = _STATE_RE.search(seller.get("formattedCity") or "") or _STATE_RE.search((seller.get("address") or "").replace("\n", ","))
    if m:
        state = m.group(1)
    lst.seller = {"name": seller.get("name"), "kind": "porsche_dealer", "street": comp.get("street"),
                  "city": comp.get("city"), "state": state, "zip": comp.get("postalCode"),
                  "phone": None, "website": seller.get("websiteUrl"),
                  "source_seller_id": seller.get("sellerId"), "partner_number": seller.get("porschePartnerNumber")}

    # -- equipment (manufacturer data)
    for grp in equipment.get("specialEquipment") or []:
        for it in grp.get("items") or []:
            if it.get("name"):
                lst.options.append({"code": it.get("code"), "name": it["name"], "group": grp.get("name")})
    for grp in equipment.get("standardEquipment") or []:
        for it in grp.get("items") or []:
            name = it.get("name") if isinstance(it, dict) else it
            if name and len(name) < 200:
                lst.standard_equipment.append(name)
    lst.highlights = [o.get("value") for o in equipment.get("importantEquipmentOptions") or [] if o.get("value")]
    lst.has_equipment_list = bool(lst.options)

    comment = (sec.get("sellerComment") or {}).get("sellerComment")
    lst.seller_comment = comment if isinstance(comment, str) else None
    phone = re.search(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", lst.seller_comment or "")
    if phone:
        lst.seller["phone"] = phone.group(0)

    # -- photos: every image id in the media centre, de-duplicated across categories
    media = sec.get("mediaCenter") or {}
    seen, pos = set(), 0
    for category, block in (media.get("images") or {}).items():
        for img in (block or {}).get("images") or []:
            iid = img.get("id") if isinstance(img, dict) else img
            if not iid or iid in seen:
                continue
            seen.add(iid)
            lst.photos.append({"id": iid, "url": f"{IMAGE_HOST}/{iid}/1280", "category": category,
                               "position": pos, "aspect": img.get("aspectRatio") if isinstance(img, dict) else None})
            pos += 1
    gallery = sec.get("gallery") or {}
    lst.photos_advertised = gallery.get("totalNumberOfImages")
    if lst.photos_advertised is None:
        lst.photos_advertised = sum((b or {}).get("totalCount") or 0 for b in (media.get("images") or {}).values()) or None
    lst.photos_are_real = (media.get("imagesType") or gallery.get("imagesType")) == "real"

    # -- history / documents
    af = meta.get("accidentFree")
    lst.accident_free = af if isinstance(af, bool) else None
    po = meta.get("numberOfPreviousOwners")
    lst.previous_owners = po if isinstance(po, int) else None
    lst.one_owner = True if lst.previous_owners == 1 else None
    for h in (sec.get("conditionAndHistory") or {}).get("history") or []:
        if re.search(r"service history", h.get("title") or "", re.I):
            lst.service_history = h.get("value")
    for w in (sec.get("warrantyDetails") or {}).get("warranties") or []:
        wt = w.get("warrantyType") or {}
        lst.warranty.append({"type": wt.get("value"), "label": wt.get("label"), "duration": w.get("duration")})
    start = re.search(r"warranty start:?\s*([\d/-]{8,10})", lst.seller_comment or "", re.I)
    if start:
        lst.extra["warranty_start_stated"] = start.group(1)
    res = sec.get("importantResources") or {}
    lst.documents = {"window_sticker_available": bool(res.get("monroneyLabelAvailable")),
                     "build_sheet_available": bool(res.get("buildsheetAvailable")),
                     "car_pass_url": sec.get("carPassUrl")}
    lst.stock_number = info.get("stockNumber")
    return lst


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------
def collect(fetcher: Fetcher, known: dict[str, dict] | None = None, max_dealers: int | None = None,
            extra_urls: list[str] | None = None) -> SourceResult:
    """Discover and fetch every target-variant listing on Porsche Finder (US).

    known: {source_listing_id: {"url":..., "scope":...}} for listings already in the
    database with a non-removed status, so listings that dropped out of a dealer's
    sitemap get an explicit detail-page check instead of being assumed gone.
    """
    res = SourceResult(SOURCE)
    known = known or {}
    idx = fetcher.get(SITEMAP_INDEX)
    if not idx.ok:
        res.status = "failed"
        res.messages.append(f"sitemap index: HTTP {idx.status} {idx.error or ''}".strip())
        res.requests, res.errors = fetcher.requests, fetcher.errors
        return res
    dealers = parse_sitemap_index(idx.text)
    if max_dealers:
        dealers = dealers[:max_dealers]
    res.scopes_total = len(dealers)
    if not dealers:
        res.status = "failed"
        res.messages.append("sitemap index contained no US dealer sitemaps (site structure changed?)")
        return res

    targets: dict[str, dict] = {}
    total_urls = 0
    for dealer in dealers:
        r = fetcher.get(f"{BASE}/us/en-US/sitemap-dealer/{dealer}/sitemap.xml")
        if not r.ok:
            res.messages.append(f"dealer sitemap {dealer}: HTTP {r.status} {r.error or ''}".strip())
            continue
        res.scopes_ok.add(dealer)
        entries = parse_dealer_sitemap(r.text)
        total_urls += len(entries)
        for e in entries:
            if e["variant"]:
                e["scope"] = dealer
                targets[e["id"]] = e
    for u in extra_urls or []:
        m = _DETAIL_RE.match(u + " ")
        if m and m.group(3) not in targets:
            targets[m.group(3)] = {"url": u, "id": m.group(3), "scope": None, "variant": slug_variant(m.group(1))}

    # known listings missing from a successfully fetched dealer sitemap -> explicit check
    recheck = {lid: k for lid, k in known.items()
               if lid not in targets and (k.get("scope") in res.scopes_ok or k.get("scope") is None)}

    fetched = 0
    for lid, e in list(targets.items()) + [(lid, {"url": k["url"], "scope": k.get("scope"), "id": lid})
                                             for lid, k in recheck.items()]:
        r = fetcher.get(e["url"])
        if r.status in (404, 410):
            res.explicit_gone.add(lid)
            res.checked_ids.add(lid)
            continue
        if not r.ok:
            res.inconclusive.add(lid)
            res.messages.append(f"detail {lid}: HTTP {r.status} {r.error or ''}".strip())
            continue
        lst = parse_detail(r.text, e["url"], scope=e.get("scope"))
        if lst is None:
            res.inconclusive.add(lid)
            res.messages.append(f"detail {lid}: page had no listing record (parser or site change)")
            continue
        res.checked_ids.add(lid)
        if lst.extra.get("listed") is False:
            res.explicit_gone.add(lid)
            continue
        fetched += 1
        if lst.variant:
            res.listings.append(lst)
        else:
            res.messages.append(f"detail {lid}: not a target variant after validation ({lst.title}; {lst.issues})")

    coverage = len(res.scopes_ok) / max(1, res.scopes_total)
    parse_failures = sum(1 for m in res.messages if "no listing record" in m)
    if coverage < 0.5 or (targets and fetched == 0):
        res.status = "failed"
    elif coverage < 0.97 or parse_failures or any(m.startswith("detail") and "HTTP" in m for m in res.messages):
        res.status = "partial"
    res.detail = {"dealer_sitemaps": res.scopes_total, "dealer_sitemaps_ok": len(res.scopes_ok),
                  "listing_urls_scanned": total_urls, "target_urls": len(targets),
                  "rechecked_missing": len(recheck), "explicit_gone": len(res.explicit_gone)}
    res.requests, res.errors = fetcher.requests, fetcher.errors
    return res


# ---------------------------------------------------------------------------
# browser capture import
# ---------------------------------------------------------------------------
# When the site cannot be reached from the cloud runner, the same pages (dealer sitemaps and
# listing pages) are read in the owner's own browser and saved as one JSON "capture" file.
# Each record is the listing page's embedded data reduced to the fields used below.
def from_capture(rec: dict) -> Listing | None:
    if not rec or not rec.get("id"):
        return None
    lst = Listing(source=SOURCE, source_listing_id=str(rec["id"]),
                  url=rec.get("url") or f"{BASE}/us/en-US/details/{rec.get('slug') or 'porsche'}-{rec.get('cond') or 'preowned'}-{rec['id']}",
                  scope=str(rec["d"]) if rec.get("d") else (str(rec["sid"]) if rec.get("sid") else None))
    lst.extra.update({"listed": rec.get("listed"), "inventory_id": rec.get("inv"), "model_code": rec.get("mc"),
                      "exterior_color_code": rec.get("ecc"), "commission_number": rec.get("com"), "via": "browser capture",
                      "capture_level": rec.get("level") or "full"})
    lst.vin = rec.get("vin")
    lst.title = f"{rec.get('yr') or ''} Porsche {rec.get('cat') or ''}".strip()
    cls = C.classify(rec.get("cat"), lst.vin, rec.get("yr"), platform_text=rec.get("mdl"))
    lst.variant, lst.generation, lst.year, lst.issues = cls["variant"], cls["generation"], cls["year"], cls["issues"]
    lst.condition = "new" if rec.get("cond") == "new" else "used"
    lst.cpo = bool(rec.get("pa"))
    lst.price_as_listed = rec.get("pv")
    # The page's price breakdown mixes three kinds of line: the MSRP of a new car (the price itself),
    # dealer add-ons / market adjustments (part of what the car costs) and documentary-type fees.
    items = [(str(f[0]), f[1]) for f in rec.get("fees") or [] if f and f[1] is not None]
    msrp = [a for lab, a in items if _MSRP_RE.search(lab)]
    addons = [a for lab, a in items if _ADDON_RE.search(lab)]
    fees = [{"label": lab, "amount": a} for lab, a in items if not _MSRP_RE.search(lab) and not _ADDON_RE.search(lab)]
    lst.fee_items = fees
    lst.dealer_fees_listed = round(sum(f["amount"] for f in fees), 2) if fees else None
    if lst.price_as_listed is not None:
        price = round(lst.price_as_listed - (lst.dealer_fees_listed or 0), 2)
        lst.price = int(price) if float(price).is_integer() else price
    else:
        lst.price = rec.get("vp")
    if msrp:
        lst.extra["msrp"] = msrp[0]
    if addons:
        lst.extra["dealer_add_ons"] = round(sum(addons), 2)
    if isinstance(rec.get("mi"), (int, float)):
        lst.mileage = int(round(rec["mi"] * 0.621371)) if rec.get("miu") == "KMT" else int(round(rec["mi"]))
    lst.exterior_color = re.sub(r"\bGt\b", "GT", rec.get("ext") or "") or None
    lst.exterior_hex = rec.get("hex")
    lst.interior_color = rec.get("int")
    lst.transmission_raw = rec.get("tr")
    lst.transmission = C.transmission_kind(rec.get("tr"))
    m = _STATE_RE.search(rec.get("fcity") or "")
    lst.seller = {"name": rec.get("sname"), "kind": "porsche_dealer", "street": rec.get("street"), "city": rec.get("city"),
                  "state": m.group(1) if m else None, "zip": rec.get("zip"), "phone": None, "website": None,
                  "source_seller_id": rec.get("sid"), "partner_number": rec.get("pn")}
    lst.options = [{"code": o[0], "name": o[1], "group": o[2] if len(o) > 2 else None} for o in rec.get("opts") or [] if o and o[1]]
    lst.standard_equipment = [n for n in rec.get("std") or [] if isinstance(n, str)]
    lst.highlights = list(rec.get("hl") or [])
    total = rec.get("opts_total")
    lst.has_equipment_list = bool(lst.options) or bool(total)
    if total and total > len(lst.options):
        # summary-level capture: only the options that decide lift, seats, brakes and packages came across
        lst.extra["options_total"] = int(total)
        lst.extra["options_partial"] = True
    lst.seller_comment = rec.get("comment") if isinstance(rec.get("comment"), str) else None
    phone = re.search(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", lst.seller_comment or "")
    if phone:
        lst.seller["phone"] = phone.group(0)
    seen, pos = set(), 0
    for category, ids in (rec.get("imgs") or {}).items():
        for iid in ids or []:
            if iid and iid not in seen:
                seen.add(iid)
                lst.photos.append({"id": iid, "url": f"{IMAGE_HOST}/{iid}/1280", "category": category, "position": pos, "aspect": None})
                pos += 1
    lst.photos_advertised = rec.get("adv") if rec.get("adv") is not None else (len(lst.photos) or None)
    lst.photos_are_real = bool(rec.get("real"))
    lst.accident_free = rec.get("af") if isinstance(rec.get("af"), bool) else None
    lst.previous_owners = rec.get("own") if isinstance(rec.get("own"), int) else None
    lst.one_owner = True if lst.previous_owners == 1 else None
    lst.service_history = rec.get("svc")
    lst.warranty = [{"type": w[0], "label": w[1], "duration": w[2]} for w in rec.get("warr") or [] if w]
    start = re.search(r"warranty start:?\s*([\d/-]{8,10})", lst.seller_comment or "", re.I)
    if start:
        lst.extra["warranty_start_stated"] = start.group(1)
    docs = rec.get("docs") or [False, False]
    lst.documents = {"window_sticker_available": bool(docs[0]), "build_sheet_available": bool(docs[1]), "car_pass_url": None}
    lst.stock_number = rec.get("stock")
    return lst


def collect_from_capture(capture: dict) -> SourceResult:
    """Turn a browser capture into a source result with the same coverage semantics as collect()."""
    res = SourceResult(SOURCE)
    res.scopes_total = int(capture.get("dealers_total") or 0)
    res.scopes_ok = {str(d) for d in capture.get("dealers_ok") or []}
    res.explicit_gone = {str(x) for x in capture.get("gone") or []}
    res.inconclusive = {str(x) for x in capture.get("failed") or []}
    for rec in capture.get("listings") or []:
        lst = from_capture(rec)
        if lst is None:
            continue
        res.checked_ids.add(lst.source_listing_id)
        if rec.get("listed") is False:
            res.explicit_gone.add(lst.source_listing_id)
        elif lst.variant:
            res.listings.append(lst)
        else:
            res.messages.append(f"capture {lst.source_listing_id}: not a tracked variant ({lst.title}; {lst.issues})")
    coverage = len(res.scopes_ok) / max(1, res.scopes_total)
    if not res.listings or coverage < 0.5:
        res.status = "failed"
    elif coverage < 0.97 or res.inconclusive:
        res.status = "partial"
    res.detail = {"via": "browser capture", "captured_at": capture.get("captured_at"), "dealer_sitemaps": res.scopes_total,
                  "dealer_sitemaps_ok": len(res.scopes_ok), "listing_urls_scanned": capture.get("listing_urls"),
                  "target_urls": capture.get("targets"), "inconclusive": len(res.inconclusive)}
    return res


# ---------------------------------------------------------------------------
# compact ("lite") capture transfer
# ---------------------------------------------------------------------------
# A full capture is a few megabytes.  When no file channel exists between the browser and the
# pipeline, the browser emits this line format instead: dictionaries (D dealers, E exterior colours,
# I interiors, F fee labels, G option groups, O options) followed by one line per listing.
# "A" listings carry the whole option list and the first photos; "B" listings (cars that already
# fail a hard requirement) carry the deciding options only.  Every line ends in a checksum.
LITE_SLUGS = ["porsche-911-gt3", "porsche-911-gt3-with-touring-package", "porsche-718-cayman-gt4-rs", "porsche-718-spyder-rs"]
LITE_CATS = ["911 GT3", "911 GT3 Touring", "718 Cayman GT4 RS", "718 Spyder RS"]
LITE_MODELS = ["982", "991 II", "992 I", "992 II"]
LITE_HIGHLIGHTS = ["bose-sound-system", "front-led", "front-led-matrix", "full-bucket-seats", "lifting-system",
                   "lightweight-roof-carbon", "pccb", "rear-axle-steering", "reversing-camera", "sport-chrono-package",
                   "sport-exhaust", "sports-seats"]
LITE_SERVICE = [None, "Yes, every service done", "Yes, every service done in Porsche Center"]
LITE_PHOTO_CATS = {"e": "exterior", "i": "interior", "h": "highlights", "u": "uncategorized"}


def lite_checksum(body: str) -> str:
    h = 0x811C9DC5
    for ch in body:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    n, digits = h % 46656, "0123456789abcdefghijklmnopqrstuvwxyz"
    return digits[n // 1296] + digits[(n // 36) % 36] + digits[n % 36]


def _num(v: str):
    if v == "":
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def parse_lite(lines: list[str], meta: dict | None = None) -> dict:
    """Rebuild a capture dict (see collect_from_capture) from checksummed lite lines."""
    from .. import geo
    D, E, I, F, G, O, recs = {}, {}, {}, {}, {}, {}, []
    for n, line in enumerate(l for l in lines if l.strip()):
        body, _, cs = line.rpartition("|")
        if lite_checksum(body) != cs:
            raise ValueError(f"lite capture line {n + 1} fails its checksum: {line[:40]}")
        f = body.split("|")
        kind = f[0]
        if kind == "D":
            D[f[1]] = {"name": f[2], "city": f[3], "zip": f[4]}
        elif kind == "E":
            E[f[1]] = (f[2], f[3] or None)
        elif kind == "I":
            I[f[1]] = f[2]
        elif kind == "F":
            F[f[1]] = f[2]
        elif kind == "G":
            G[f[1]] = f[2]
        elif kind == "O":
            O[f[1]] = [f[2] or None, f[3], f[4]]
        elif kind in ("A", "B"):
            recs.append(f)
        else:
            raise ValueError(f"lite capture line {n + 1}: unknown record type {kind!r}")

    def opt(i):
        code, name, g = O[i]
        return [code, name, G.get(g)]

    listings = []
    for f in recs:
        kind, lid, s, cond, d, vin, yr, mdl, tr, pv, vp, mi, pa, own, real, adv = f[:16]
        dealer = D[d]
        loc = geo.zip_latlon(dealer["zip"])
        state = loc[3] if loc else None
        rec = {"id": lid, "listed": True, "slug": LITE_SLUGS[int(s)], "cond": "new" if cond == "n" else "preowned", "d": d,
               "sid": d, "vin": vin, "yr": int(yr), "cat": LITE_CATS[int(s)], "mdl": LITE_MODELS[int(mdl)],
               "tr": "Manual" if tr == "M" else "PDK (Automatic)", "pv": _num(pv), "vp": _num(vp), "mi": _num(mi), "miu": "SMI",
               "pa": pa == "1", "own": int(own) if own != "" else None, "real": real == "1", "adv": _num(adv),
               "sname": dealer["name"], "city": dealer["city"], "zip": dealer["zip"],
               "fcity": ", ".join(x for x in (dealer["city"], state, dealer["zip"]) if x)}
        rec["url"] = f"{BASE}/us/en-US/details/{rec['slug']}-{rec['cond']}-{lid}"
        if kind == "A":
            docs, e, i, stock, svc, w, fees, opts, hl, imgs = f[16:26]
            comment = "|".join(f[26:])
            rec.update({"docs": [c == "1" for c in docs], "int": I[i], "stock": stock or None, "svc": LITE_SERVICE[int(svc)],
                        "opts": [opt(x) for x in opts.split(",") if x], "comment": comment.replace(" \\n ", "\n") or None,
                        "level": "detail"})
            photos: dict = {}
            for token in (t for t in imgs.split(",") if t):
                h = token[1:]
                photos.setdefault(LITE_PHOTO_CATS.get(token[0], "uncategorized"), []).append(
                    f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}")
            rec["imgs"] = photos
        else:
            e, w, fees, lift, total, opts, hl = f[16:23]
            rec.update({"opts": [opt(x) for x in opts.split(",") if x], "opts_total": int(total), "imgs": {}, "level": "summary"})
            if (lift == "1") != any(str(o[0]) in ("474", "2UH") for o in rec["opts"]):
                raise ValueError(f"lite capture {lid}: lift flag disagrees with its option list")
        rec["ext"], rec["hex"] = E[e]
        rec["fees"] = [[F[x.split(":")[0]], float(x.split(":")[1])] for x in fees.split(",") if x]
        rec["hl"] = [LITE_HIGHLIGHTS[ord(c) - 97] for c in hl]
        warr = []
        if rec["pa"]:
            warr += [["PORSCHE_APPROVED_WARRANTY", "Porsche Approved Warranty", "24 months"],
                     ["ASSISTANCE", "Porsche Roadside Assistance", "24 months"]]
        if w:
            warr.append(["NEW_CAR_WARRANTY", "New Car Warranty", f"{int(w[1:])} months" + (" remaining term" if w[0] == "R" else "")])
        rec["warr"] = warr
        listings.append(rec)
    out = {"format": "lite", "listings": listings, "gone": [], "failed": []}
    out.update(meta or {})
    return out
