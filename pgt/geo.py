"""ZIP-code geocoding and distance from the buyer's home."""
from __future__ import annotations

import math
from functools import lru_cache

try:
    import zipcodes
except Exception:  # pragma: no cover - dependency is declared; guard keeps imports safe
    zipcodes = None


@lru_cache(maxsize=4096)
def zip_latlon(zip_code: str | None) -> tuple[float, float, str, str] | None:
    """(lat, lon, city, state) for a US ZIP, or None."""
    if not zip_code or zipcodes is None:
        return None
    z = str(zip_code).strip()[:5]
    if len(z) != 5 or not z.isdigit():
        return None
    try:
        m = zipcodes.matching(z)
    except Exception:
        return None
    if not m:
        return None
    return float(m[0]["lat"]), float(m[0]["long"]), m[0]["city"], m[0]["state"]


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def distance_from(home_zip: str, zip_code: str | None = None, lat: float | None = None,
                  lon: float | None = None, road_factor: float = 1.17) -> dict | None:
    """Estimated road miles from home.  Great-circle distance times a road factor."""
    home = zip_latlon(home_zip)
    if not home:
        return None
    if lat is None or lon is None:
        loc = zip_latlon(zip_code)
        if not loc:
            return None
        lat, lon = loc[0], loc[1]
    gc = haversine_miles(home[0], home[1], lat, lon)
    return {"great_circle_miles": round(gc), "road_miles_est": round(gc * road_factor)}
