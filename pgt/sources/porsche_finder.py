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
