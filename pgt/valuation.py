"""Comparable-based valuation.

Method (kept simple enough to audit by hand):
  1. Comparables = other cars of the same variant and generation with a price and mileage.
     Asking comps are current listings plus the last asking price of listings delisted in
     the past 120 days.  Realised sale prices are tracked separately and never mixed in.
  2. The mileage effect is the Theil-Sen slope (median of pairwise $/mile slopes) across
     the comps, clamped to be non-positive.  It needs at least 8 comps; otherwise 0.
  3. Each comp's price is shifted to the subject car's mileage; comps within one model
     year are preferred when at least 6 exist.
  4. Fair range = 25th-75th percentile of the adjusted comps; midpoint = median.
  5. Confidence is reported from sample size.  Under 5 comps no estimate is produced.
Asking prices are not transaction prices; the output always says which it is based on.
"""
from __future__ import annotations

from statistics import median

MIN_COMPS = 5
MIN_SLOPE_COMPS = 8


def percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        raise ValueError("empty")
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def theil_sen_slope(points: list[tuple[float, float]]) -> float | None:
    """Median pairwise slope dy/dx; None when undefined."""
    slopes = []
    n = len(points)
    for i in range(n):
        for k in range(i + 1, n):
            dx = points[k][0] - points[i][0]
            if abs(dx) >= 250:          # ignore near-identical mileages
                slopes.append((points[k][1] - points[i][1]) / dx)
    return median(slopes) if slopes else None


def value(subject: dict, pool: list[dict], sales: list[dict] | None = None) -> dict:
    """subject / pool rows: {key, variant, generation, year, mileage, price}."""
    comps = [c for c in pool
             if c.get("key") != subject.get("key")
             and c.get("variant") == subject.get("variant") and c.get("generation") == subject.get("generation")
             and c.get("price") and c.get("mileage") is not None]
    # Gearbox moves GT3 prices a lot (a manual 991.2 asks far more than a PDK one), so compare like with
    # like whenever enough comps have a confirmed gearbox; cars with an unconfirmed gearbox stay out of that set.
    gearbox = None
    if subject.get("transmission"):
        same = [c for c in comps if c.get("transmission") == subject["transmission"]]
        if len(same) >= MIN_COMPS and len(same) < len(comps):
            comps, gearbox = same, subject["transmission"]
    out = {"basis": "asking prices", "n": len(comps), "confidence": "insufficient", "method": "theil-sen mileage adjustment"}
    if gearbox:
        out["gearbox"] = gearbox
    sales_comps = [s for s in (sales or [])
                   if s.get("variant") == subject.get("variant") and s.get("generation") == subject.get("generation")]
    if sales_comps:
        out["sales"] = {"n": len(sales_comps), "median": median(s["sale_price"] for s in sales_comps),
                        "note": "realised sale prices, reported separately from asking-based range"}
    if subject.get("price") is None or subject.get("mileage") is None or len(comps) < MIN_COMPS:
        out["note"] = f"Only {len(comps)} comparable listing(s); no fair-value estimate is shown below {MIN_COMPS}."
        return out

    slope = 0.0
    if len(comps) >= MIN_SLOPE_COMPS:
        s = theil_sen_slope([(c["mileage"], c["price"]) for c in comps])
        slope = min(0.0, s) if s is not None else 0.0
    near = [c for c in comps if subject.get("year") and c.get("year") and abs(c["year"] - subject["year"]) <= 1]
    used = near if len(near) >= 6 else comps
    adjusted = [c["price"] + slope * (subject["mileage"] - c["mileage"]) for c in used]
    mid, lo, hi = median(adjusted), percentile(adjusted, 0.25), percentile(adjusted, 0.75)
    delta = (subject["price"] - mid) / mid
    n = len(used)
    conf = "moderate" if n >= 15 else "low" if n >= 8 else "very low"
    if delta <= -0.06:
        rating = "great"
    elif delta <= -0.02:
        rating = "good"
    elif delta < 0.02:
        rating = "fair"
    elif delta < 0.06:
        rating = "high"
    else:
        rating = "overpriced"
    out.update({"n": n, "n_pool": len(comps), "confidence": conf, "fair_low": round(lo), "fair_mid": round(mid),
                "fair_high": round(hi), "slope_per_mile": round(slope, 3), "delta_pct": round(delta, 4),
                "rating": rating, "year_window": "±1 model year" if used is near else "all model years",
                "note": f"Based on {n} asking-price comps" + (f" with a confirmed {'manual' if gearbox == 'manual' else 'PDK'} gearbox"
                                                                if gearbox else "") + "; asking prices are not sale prices."})
    return out
