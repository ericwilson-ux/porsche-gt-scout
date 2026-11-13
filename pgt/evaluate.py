"""Hard-requirement eligibility and the preference-fit score.

Eligibility and preference are deliberately separate:
  * hard requirements decide the category (confirmed / lead / near_miss / excluded)
  * the 0-100 preference score ranks cars inside a category
  * deal quality (valuation.py) is a third, independent axis
A requirement is only "pass" when a source documents it; anything undocumented is
"unverified" and keeps the car out of the confirmed set.
"""
from __future__ import annotations

from datetime import date

PASS, FAIL, UNVERIFIED = "pass", "fail", "unverified"


def _check(key, label, status, detail, provenance=None):
    return {"key": key, "label": label, "status": status, "detail": detail, "provenance": provenance}


def eligibility(v: dict, cfg: dict) -> dict:
    """v is the merged vehicle view.  Returns {category, checks[], summary}."""
    hard = cfg["hard"]
    vcfg = cfg["variants"].get(v.get("variant") or "", None)
    checks = []

    if not vcfg:
        checks.append(_check("variant", "Target model", FAIL, "Not one of the four target variants"))
    elif v.get("generation") not in vcfg["generations"]:
        checks.append(_check("variant", "Target model", FAIL,
                             f"{vcfg['label']} but generation {v.get('generation') or 'unknown'} is not targeted"))
    else:
        checks.append(_check("variant", "Target model", PASS, f"{vcfg['label']} ({v['generation']})", "listing + VIN"))

    # -- front axle lift
    lift = (v.get("equipment") or {}).get("lift") or {}
    st = lift.get("status", "unknown")
    if not hard["require_front_lift"]:
        pass
    elif st == "verified":
        checks.append(_check("lift", "Front axle lift", PASS, lift.get("detail"), "manufacturer equipment data"))
    elif st == "seller_stated":
        checks.append(_check("lift", "Front axle lift", PASS, lift.get("detail"), "seller statement"))
    elif st == "verified_absent":
        checks.append(_check("lift", "Front axle lift", FAIL, lift.get("detail"), "manufacturer equipment data"))
    else:
        checks.append(_check("lift", "Front axle lift", UNVERIFIED,
                             "No equipment list or window sticker available from any source; confirm with the seller"))

    # -- price cap
    near_miss_only = False
    price = v.get("price")
    if vcfg:
        cap = vcfg["max_price"]
        if price is None:
            checks.append(_check("price", f"Under ${cap:,.0f}", UNVERIFIED, "No advertised price"))
        elif price < cap:
            checks.append(_check("price", f"Under ${cap:,.0f}", PASS, f"Advertised ${price:,.0f}", v.get("price_source")))
        elif price <= cap * (1 + hard["near_miss_price_over"]):
            near_miss_only = True
            checks.append(_check("price", f"Under ${cap:,.0f}", FAIL,
                                 f"Advertised ${price:,.0f} is {price / cap - 1:.1%} over the cap (near miss)",
                                 v.get("price_source")))
        else:
            checks.append(_check("price", f"Under ${cap:,.0f}", FAIL,
                                 f"Advertised ${price:,.0f} is {price / cap - 1:.1%} over the cap", v.get("price_source")))
        # -- mileage cap (GT3 / GT3 Touring)
        max_miles = vcfg.get("max_miles")
        if max_miles:
            miles = v.get("mileage")
            if miles is None:
                checks.append(_check("mileage", f"At most {max_miles:,} miles", UNVERIFIED, "Mileage not published"))
            elif miles <= max_miles:
                checks.append(_check("mileage", f"At most {max_miles:,} miles", PASS, f"{miles:,} mi"))
            else:
                checks.append(_check("mileage", f"At most {max_miles:,} miles", FAIL, f"{miles:,} mi"))

    # -- accident history
    if hard["require_clean_accident_history"]:
        acc = (v.get("history") or {}).get("accident") or {}
        st = acc.get("status", "unknown")
        if st == "verified_clean":
            checks.append(_check("accident", "Clean accident history", PASS, acc.get("detail"), "source history flag"))
        elif st == "seller_stated_clean":
            checks.append(_check("accident", "Clean accident history", PASS, acc.get("detail"), "seller statement"))
        elif st == "reported":
            checks.append(_check("accident", "Clean accident history", FAIL, acc.get("detail"), "listing text"))
        else:
            checks.append(_check("accident", "Clean accident history", UNVERIFIED,
                                 acc.get("detail") or "No accident statement or history report in any listing; request Carfax/AutoCheck"))

    # -- documented track use
    if hard["reject_documented_track_use"]:
        trk = (v.get("history") or {}).get("track") or {}
        if trk.get("status") == "documented":
            checks.append(_check("track", "No documented track use", FAIL, trk.get("detail"), "listing text"))
        else:
            checks.append(_check("track", "No documented track use", PASS,
                                 "No track use documented in any listing (absence of evidence, not proof)",
                                 "listing text scan"))

    fails = [c for c in checks if c["status"] == FAIL]
    unver = [c for c in checks if c["status"] == UNVERIFIED]
    if fails:
        category = "near_miss" if (near_miss_only and len(fails) == 1) else "excluded"
    elif unver:
        category = "lead"
    else:
        category = "confirmed"
    summary = {"confirmed": "Meets every hard requirement with documentation",
               "lead": "Nothing disqualifying, but needs verification: " + ", ".join(c["label"] for c in unver),
               "near_miss": "Only miss: " + "; ".join(c["detail"] for c in fails),
               "excluded": "Fails: " + "; ".join(f"{c['label']} ({c['detail']})" for c in fails)}[category]
    return {"category": category, "checks": checks, "summary": summary,
            "unverified": [c["key"] for c in unver], "failed": [c["key"] for c in fails]}


# ---------------------------------------------------------------------------
# preference score
# ---------------------------------------------------------------------------
def _age_years(year: int, today: date) -> float:
    # model-year cars typically reach owners from the autumn before the model year
    return max(0.5, (today - date(year - 1, 10, 1)).days / 365.25)


def _item(key, label, frac, weight, note):
    frac = max(0.0, min(1.0, frac))
    return {"key": key, "label": label, "fraction": round(frac, 3), "max": weight,
            "points": round(frac * weight, 2), "note": note}


def preference_score(v: dict, cfg: dict, today: date | None = None) -> dict:
    today = today or date.today()
    w, pref, buyer = cfg["weights"], cfg["preferences"], cfg["buyer"]
    variant = v.get("variant")
    is_718 = variant in ("gt4rs", "spyder_rs")
    vcfg = cfg["variants"].get(variant or "", {})
    items = []

    # mileage relative to model year
    miles, year = v.get("mileage"), v.get("year")
    if miles is None or not year:
        items.append(_item("mileage", "Mileage for its age", 0.5, w["mileage"], "Mileage or year unknown"))
    else:
        age = _age_years(year, today)
        expected = age * buyer["annual_miles_high"] + 1500
        ratio = miles / expected
        frac = 1.0 if ratio <= 1 else (1.0 - 0.65 * (ratio - 1) / 2 if ratio <= 3 else 0.2)
        note = f"{miles:,} mi at ~{age:.1f} yrs; your own pace would put it near {expected:,.0f} mi"
        pm = vcfg.get("preferred_max_miles")
        if pm and miles > pm:
            capfrac = max(0.3, 0.6 - 0.3 * (miles - pm) / pm)
            frac = min(frac, capfrac)
            note += f"; above your preferred {pm:,} mi"
        items.append(_item("mileage", "Mileage for its age", frac, w["mileage"], note))

    fam = v.get("exterior_family")
    if fam is None:
        items.append(_item("exterior_color", "Exterior colour", 0.5, w["exterior_color"], "Colour not published"))
    elif fam in pref["exterior_families"]:
        items.append(_item("exterior_color", "Exterior colour", 1.0, w["exterior_color"], f"{v.get('exterior_color')} ({fam})"))
    else:
        items.append(_item("exterior_color", "Exterior colour", 0.4, w["exterior_color"],
                           f"{v.get('exterior_color')} ({fam}) is outside white/silver/blue/gray/black"))

    ifam = v.get("interior_family")
    if ifam is None:
        items.append(_item("interior_color", "Interior colour", 0.5, w["interior_color"], "Interior not published"))
    elif ifam in pref["interior_families"]:
        items.append(_item("interior_color", "Interior colour", 1.0, w["interior_color"], f"{v.get('interior_color')} ({ifam})"))
    else:
        items.append(_item("interior_color", "Interior colour", 0.4, w["interior_color"],
                           f"{v.get('interior_color')} ({ifam}) is outside black/beige/tan"))

    trans = v.get("transmission")
    if is_718:
        frac, note = (1.0, "PDK") if trans == "pdk" else ((0.5, "Transmission not published") if trans is None else (0.3, trans))
    elif trans == "manual":
        frac, note = pref["gt3_manual_score"], "6-speed manual (your slight preference)"
    elif trans == "pdk":
        frac, note = pref["gt3_pdk_score"], "PDK (also highly desirable)"
    else:
        frac, note = 0.6, "Transmission not published"
    items.append(_item("transmission", "Transmission", frac, w["transmission"], note))

    eq = v.get("equipment") or {}
    seats = (eq.get("seats") or {}).get("value")
    if seats == "adjustable":
        items.append(_item("seats", "Seats", 1.0, w["seats"], "Adjustable sport seats"))
    elif seats == "bucket":
        items.append(_item("seats", "Seats", 0.7, w["seats"], "Full bucket seats (acceptable, not preferred)"))
    else:
        items.append(_item("seats", "Seats", 0.6, w["seats"], "Seat type not documented"))

    if is_718:
        ws = eq.get("weissach") or {}
        if ws.get("status") in ("verified", "seller_stated") and ws.get("value"):
            items.append(_item("weissach", "No Weissach Package", 0.4, w["weissach"], "Has the Weissach Package"))
        elif ws.get("status") == "verified_absent":
            items.append(_item("weissach", "No Weissach Package", 1.0, w["weissach"], "No Weissach Package in the factory option list"))
        else:
            items.append(_item("weissach", "No Weissach Package", 0.7, w["weissach"], "Weissach status not documented"))

    wr = v.get("warranty_summary") or {}
    if v.get("cpo"):
        items.append(_item("warranty", "CPO / warranty", 1.0, w["warranty"], "Porsche Approved Certified Pre-Owned"))
    elif wr.get("factory_months_remaining_est") is not None and wr["factory_months_remaining_est"] >= 12:
        items.append(_item("warranty", "CPO / warranty", 0.75, w["warranty"],
                           f"Not CPO; ~{wr['factory_months_remaining_est']} months of factory warranty left (estimate)"))
    elif wr.get("factory_months_remaining_est"):
        items.append(_item("warranty", "CPO / warranty", 0.55, w["warranty"],
                           f"Not CPO; ~{wr['factory_months_remaining_est']} months of factory warranty left (estimate)"))
    else:
        items.append(_item("warranty", "CPO / warranty", 0.3, w["warranty"], "Not CPO; factory warranty likely expired"))

    docs, seller = v.get("documents") or {}, v.get("seller") or {}
    frac, notes = 0.0, []
    if seller.get("kind") == "porsche_dealer":
        frac += 0.4
        notes.append("Porsche dealer")
    elif seller.get("kind") == "dealer":
        frac += 0.2
        notes.append("independent dealer")
    if docs.get("window_sticker_available") or docs.get("build_sheet_available"):
        frac += 0.3
        notes.append("window sticker / build sheet on file")
    if (v.get("history") or {}).get("service_history"):
        frac += 0.2
        notes.append("service history stated")
    if (v.get("history") or {}).get("one_owner"):
        frac += 0.1
        notes.append("one owner")
    items.append(_item("documentation", "Seller & documentation", frac, w["documentation"], ", ".join(notes) or "Little documentation"))

    dist = (v.get("distance") or {}).get("road_miles_est")
    if dist is None:
        items.append(_item("distance", f"Distance from {buyer['home_label']}", 0.5, w["distance"], "Location unknown"))
    else:
        frac = 1.0 if dist <= 250 else 0.8 if dist <= 600 else 0.6 if dist <= 1000 else 0.45
        how = "drivable" if dist <= 600 else "fly-and-drive or enclosed transport"
        items.append(_item("distance", f"Distance from {buyer['home_label']}", frac, w["distance"], f"~{dist:,} road miles ({how})"))

    mx = sum(i["max"] for i in items)
    total = round(100 * sum(i["points"] for i in items) / mx) if mx else 0
    misses = [i["label"] + ": " + i["note"] for i in items if i["fraction"] < 0.75]
    strengths = [i["label"] + ": " + i["note"] for i in items if i["fraction"] >= 0.99]
    return {"total": total, "items": items, "strengths": strengths, "misses": misses}
