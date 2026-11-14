"""MarketCheck licensed dealer-inventory API adapter.

This is the authorised channel for nationwide dealer inventory beyond Porsche
Finder (the same dealer stock that appears on consumer marketplaces).  It is
NOT Cars.com itself: Cars.com publishes no API and disallows automated access.

Docs: https://docs.marketcheck.com/docs/api/cars/inventory/inventory-search
Endpoint: GET https://api.marketcheck.com/v2/search/car/active  (api_key query parameter)
"""
from __future__ import annotations

import json
import os
import re

from .. import classify as C
from ..http import Fetcher
from .base import Listing, SourceResult

SOURCE = "marketcheck"
ENDPOINT = "https://api.marketcheck.com/v2/search/car/active"
ROWS = 50

# Trim names are discovered from the feed's own facet counts (cached for a week by the
# caller) so a naming difference can never silently drop cars; every row is then classified
# locally from heading/trim text and validated against its VIN.
BASE_PARAMS = {"make": "porsche", "car_type": "used", "country": "us"}
# The feed refuses to page past 500 rows per query, so the sweep is split into model-year
# buckets that each stay under that cap.  Each bucket is its own coverage unit.
YEAR_BUCKETS = ["2022-2023", "2024-2025", "2026-2027"]
ALL_YEARS = "2022-2027"
PAGE_CAP = 500
_TRIM_RE = re.compile(r"gt3|gt4\s*rs|spyder\s*rs|^rs$", re.I)
_TRIM_EXCLUDE_RE = re.compile(r"gt3\s*rs|gt2|cup|clubsport", re.I)
SCOPE = "us-used"


def pick_trims(facet_items: list[dict]) -> list[str]:
    out = []
    for it in facet_items or []:
        name = str(it.get("item") or "")
        if name and _TRIM_RE.search(name) and not _TRIM_EXCLUDE_RE.search(name):
            out.append(name)
    return sorted(set(out))


def normalise(raw: dict, scope: str) -> Listing | None:
    build = raw.get("build") or {}
    dealer = raw.get("dealer") or raw.get("mc_dealership") or {}
    text = " ".join(str(x) for x in (raw.get("heading"), build.get("model"), build.get("trim"), build.get("version")) if x)
    cls = C.classify(text, raw.get("vin"), build.get("year"))
    if not cls["variant"]:
        return None
    lst = Listing(source=SOURCE, source_listing_id=str(raw.get("id")), url=raw.get("vdp_url") or "", scope=scope)
    lst.vin = raw.get("vin")
    lst.title = raw.get("heading")
    lst.variant, lst.generation, lst.year, lst.issues = cls["variant"], cls["generation"], cls["year"], cls["issues"]
    lst.condition = "new" if raw.get("inventory_type") == "new" else "used"
    lst.cpo = True if raw.get("is_certified") in (1, True, "1") else False
    price = raw.get("price")
    lst.price_as_listed = float(price) if isinstance(price, (int, float)) and price > 0 else None
    fees = raw.get("fees")
    if lst.price_as_listed and raw.get("price_includes_fees") and isinstance(fees, (int, float)) and fees > 0:
        lst.price = lst.price_as_listed - float(fees)
        lst.dealer_fees_listed = float(fees)
        lst.fee_items = [{"label": "Dealer fees (per feed)", "amount": float(fees)}]
    else:
        lst.price = lst.price_as_listed
    miles = raw.get("miles")
    lst.mileage = int(miles) if isinstance(miles, (int, float)) else None
    lst.exterior_color = raw.get("exterior_color") or raw.get("base_ext_color")
    lst.interior_color = raw.get("interior_color") or raw.get("base_int_color")
    lst.transmission_raw = build.get("transmission")
    lst.transmission = C.transmission_kind(build.get("transmission"))
    website = dealer.get("website")
    is_porsche = bool(re.search(r"porsche", f"{dealer.get('name') or ''} {website or ''}", re.I))
    kind = {"fsbo": "private", "auction": "auction"}.get(raw.get("seller_type"),
                                                         "porsche_dealer" if is_porsche else "dealer")
    lst.extra["dealer_type"] = dealer.get("dealer_type")
    lst.extra["first_seen_anywhere"] = raw.get("first_seen_at_mc_date")
    lst.seller = {"name": dealer.get("name"), "kind": kind, "street": dealer.get("street"),
                  "city": dealer.get("city"), "state": dealer.get("state"), "zip": dealer.get("zip"),
                  "phone": dealer.get("phone"), "website": website,
                  "lat": _f(dealer.get("latitude")), "lon": _f(dealer.get("longitude")),
                  "source_seller_id": dealer.get("id")}
    media = raw.get("media") or {}
    links = media.get("photo_links") or []
    seen = set()
    for i, link in enumerate(links):
        if not link or link in seen:
            continue
        seen.add(link)
        lst.photos.append({"id": link, "url": link, "category": "uncategorized", "position": i, "aspect": None})
    lst.photos_advertised = len(links) or None
    lst.photos_are_real = True if links else None
    lst.carfax_clean_title = raw.get("carfax_clean_title")
    lst.one_owner = raw.get("carfax_1_owner")
    lst.stock_number = raw.get("stock_no")
    lst.source_first_seen = raw.get("first_seen_at_source_date") or raw.get("first_seen_at_date")
    lst.source_days_on_market = raw.get("dom_active") if isinstance(raw.get("dom_active"), int) else None
    ref = raw.get("ref_price")
    lst.source_previous_price = float(ref) if isinstance(ref, (int, float)) and ref > 0 else None
    lst.origin_site = raw.get("source")
    lst.extra.update({"model_code": raw.get("model_code"), "msrp": raw.get("msrp"), "dom": raw.get("dom"),
                 "dom_180": raw.get("dom_180"), "price_change_percent": raw.get("price_change_percent"),
                 "data_source": raw.get("data_source"), "ref_price_dt": raw.get("ref_price_dt")})
    return lst


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect(fetcher: Fetcher, api_key: str | None = None, cached_trims: list[str] | None = None,
            max_pages: int = 10) -> SourceResult:
    """Fetch every used target-variant listing in the feed.

    Call budget per run: 1 facet call (skipped when `cached_trims` is given) plus
    ceil(matches / 50) page calls per model-year bucket.
    """
    res = SourceResult(SOURCE)
    api_key = api_key or os.environ.get("MARKETCHECK_API_KEY")
    if not api_key:
        res.status = "skipped"
        res.messages.append("MARKETCHECK_API_KEY not configured")
        return res
    res.scopes_total = len(YEAR_BUCKETS)
    trims = list(cached_trims or [])
    if not trims:
        r = fetcher.get(ENDPOINT, params={"api_key": api_key, **BASE_PARAMS, "year_range": ALL_YEARS, "rows": 1,
                                          "start": 0, "facets": "trim|0|1000"}, check_robots=False)
        if not r.ok:
            res.status = "failed"
            res.messages.append(f"trim facet: HTTP {r.status} {(r.error or r.text[:160]).strip()}")
            res.requests, res.errors = fetcher.requests, fetcher.errors
            return res
        try:
            trims = pick_trims((json.loads(r.text).get("facets") or {}).get("trim") or [])
        except ValueError:
            trims = []
        if not trims:
            res.status = "failed"
            res.messages.append("trim facet returned no GT3 / GT4 RS / Spyder RS trims")
            res.requests, res.errors = fetcher.requests, fetcher.errors
            return res
    res.detail["trims"] = trims
    res.detail["buckets"] = {}

    seen_ids: set[str] = set()
    for bucket in YEAR_BUCKETS:
        scope = f"{SCOPE}:{bucket}"
        ok, start, found = True, 0, None
        for _page in range(max_pages):
            params = {"api_key": api_key, **BASE_PARAMS, "year_range": bucket, "trim": ",".join(trims), "rows": ROWS,
                      "start": start, "sort_by": "first_seen", "sort_order": "desc"}
            r = fetcher.get(ENDPOINT, params=params, check_robots=False)   # licensed API, keyed access
            if r.status == 422:      # documented: start beyond num_found, or the plan's pagination cap
                if found is not None and start < found:
                    ok = False
                    res.messages.append(f"{bucket}: pagination cap reached at start={start} of {found}")
                break
            if not r.ok:
                ok = False
                res.messages.append(f"{bucket} start={start}: HTTP {r.status} {(r.error or r.text[:160]).strip()}")
                break
            try:
                payload = json.loads(r.text)
            except ValueError:
                ok = False
                res.messages.append(f"{bucket} start={start}: response was not JSON")
                break
            found = payload.get("num_found", 0)
            rows = payload.get("listings") or []
            if rows and "raw_sample" not in res.detail:
                res.detail["raw_sample"] = rows[0]
            for raw in rows:
                lst = normalise(raw, scope=scope)
                if lst and lst.source_listing_id not in seen_ids:
                    seen_ids.add(lst.source_listing_id)
                    res.listings.append(lst)
            start += ROWS
            if start >= found or not rows:
                break
            if start >= PAGE_CAP:
                ok = False
                res.messages.append(f"{bucket}: {found} matches exceed the feed's {PAGE_CAP}-row paging cap; split this bucket")
                break
        else:
            if found is not None and start < found:
                ok = False
                res.messages.append(f"{bucket}: stopped after {max_pages} pages ({start} of {found})")
        res.detail["buckets"][bucket] = {"num_found": found, "complete": ok}
        if ok:
            res.scopes_ok.add(scope)
    if not res.scopes_ok:
        res.status = "partial" if res.listings else "failed"
    elif len(res.scopes_ok) < res.scopes_total:
        res.status = "partial"
    res.detail["listings"] = len(res.listings)
    res.requests, res.errors = fetcher.requests, fetcher.errors
    return res
