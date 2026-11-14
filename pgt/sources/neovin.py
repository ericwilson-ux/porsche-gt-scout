"""MarketCheck NeoVIN decode: factory build data (installed options) for one VIN.

GET https://api.marketcheck.com/v2/decode/car/neovin/{vin}/specs?api_key=...
Docs: https://docs.marketcheck.com/docs/api/cars/vehicle-specs/neovin

This is a paid, per-call endpoint, so it is only called for cars that already pass every
other hard requirement, once per VIN, inside a monthly call budget.  Results are cached in
the `vin_decodes` table forever (a car's factory build does not change).
"""
from __future__ import annotations

import json
import os
import re

from ..http import Fetcher

ENDPOINT = "https://api.marketcheck.com/v2/decode/car/neovin/{vin}/specs"
_KEEP_FEATURE = re.compile(r"lift|seat|bucket|brake|ceramic|weissach|clubsport|roll", re.I)


def decode(fetcher: Fetcher, vin: str, api_key: str | None = None) -> dict:
    """Returns {status, http_status, data?, error?}.  status: ok | empty | not_entitled | error."""
    api_key = api_key or os.environ.get("MARKETCHECK_API_KEY")
    r = fetcher.get(ENDPOINT.format(vin=vin), params={"api_key": api_key}, check_robots=False)
    if r.status in (401, 402, 403):
        return {"status": "not_entitled", "http_status": r.status, "error": (r.text or "")[:200]}
    if r.status in (404, 422):
        return {"status": "empty", "http_status": r.status}
    if not r.ok:
        return {"status": "error", "http_status": r.status, "error": r.error or (r.text or "")[:200]}
    try:
        raw = json.loads(r.text)
    except ValueError:
        return {"status": "error", "http_status": 200, "error": "response was not JSON"}
    return {"status": "ok", "http_status": 200, "data": compact(raw), "raw": raw}


def compact(raw: dict) -> dict:
    """Keep what the pipeline uses: installed options plus seat / brake / lift related features."""
    opts = []
    for o in raw.get("installed_options_details") or []:
        if isinstance(o, dict) and (o.get("name") or o.get("code")):
            opts.append({"code": o.get("code"), "name": o.get("name") or "", "msrp": o.get("msrp"),
                         "type": o.get("type"), "confidence": o.get("confidence"), "verified": o.get("verified")})
    feats = []
    for group in (raw.get("features") or {}, raw.get("high_value_features") or {}):
        for items in (group.values() if isinstance(group, dict) else []):
            for f in items or []:
                d = (f or {}).get("description") if isinstance(f, dict) else None
                if d and _KEEP_FEATURE.search(d) and d not in feats:
                    feats.append(d)
    def colour(c):
        return {"name": c.get("name"), "code": c.get("code"), "confidence": c.get("confidence")} if isinstance(c, dict) else None
    return {"options": opts, "options_packages": raw.get("options_packages"), "features": feats[:60],
            "trim": raw.get("trim"), "version": raw.get("version"), "msrp": raw.get("msrp"),
            "combined_msrp": raw.get("combined_msrp"), "installed_options_msrp": raw.get("installed_options_msrp"),
            "msrp_label": raw.get("msrp_label"), "record_confidence": raw.get("record_confidence"),
            "record_source": raw.get("record_source"), "exterior_color": colour(raw.get("exterior_color")),
            "interior_color": colour(raw.get("interior_color")), "transmission": raw.get("transmission_description") or raw.get("transmission")}
