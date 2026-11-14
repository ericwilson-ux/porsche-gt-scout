"""Seller outreach briefs: target price, open questions and a first-contact email draft for one car.

Nothing here sends anything.  It prepares material that a person approves before any
message leaves their mailbox.  Targets are private working numbers and are never exported
to the public site.
"""
from __future__ import annotations

GREAT_DEAL = 0.94        # "great" in valuation.py: asking at least 6% under the mileage-adjusted comp median
GOOD_DEAL = 0.98


def _round(x: float, step: int = 500) -> int:
    return int(x // step * step)


def target_price(m: dict, cfg: dict) -> dict:
    """Target, opening and ceiling prices for one merged vehicle.

    target  = the price at which the car is a "great" deal against comparables (6% under the
              mileage-adjusted median asking price), never above the current asking price.
              A car already priced that low gets a modest 3% ask instead.
    opening = 3% under target (room to land on target).
    ceiling = 2% under the comparable median ("good" deal), and under the hard price cap.
    """
    val, ask = m.get("valuation") or {}, m.get("price")
    grule = (cfg.get("generation_rules") or {}).get(m.get("generation") or "", {})
    cap = grule.get("max_price", (cfg["variants"].get(m.get("variant") or "") or {}).get("max_price"))
    out = {"asking": ask, "hard_cap": cap, "basis": None, "target": None, "opening": None, "ceiling": None, "notes": []}
    mid = val.get("fair_mid")
    if not mid:
        out["notes"].append(val.get("note") or "Too few comparable listings for a price target; gather a quote first.")
        return out
    great = mid * GREAT_DEAL
    if ask is None:
        target = great
        out["notes"].append("No advertised price: target is set from comparables alone.")
    elif ask <= great * 1.015:
        target = ask * 0.97
        out["notes"].append(f"Already priced {abs(val.get('delta_pct', 0)):.1%} under comparable cars; little room is likely.")
    else:
        target = great
    if cap:
        target = min(target, cap - 500)
    ceiling = min(mid * GOOD_DEAL, (cap - 1) if cap else mid, ask if ask else mid)
    out.update({"target": _round(target), "opening": _round(target * 0.97), "ceiling": _round(max(ceiling, target)),
                "basis": {"comparable_median": mid, "fair_low": val.get("fair_low"), "fair_high": val.get("fair_high"),
                          "comps": val.get("n"), "confidence": val.get("confidence"), "year_window": val.get("year_window"),
                          "slope_per_mile": val.get("slope_per_mile")}})
    if ask:
        out["discount_needed"] = round(1 - out["target"] / ask, 4)
    out["notes"].append("Comparables are asking prices, not sale prices; real transactions usually close below asking.")
    if val.get("confidence") in ("very low", "low"):
        out["notes"].append(f"Only {val.get('n')} comparables ({val.get('confidence')} confidence): treat the target as a starting point.")
    if (m.get("days_on_market") or 0) >= 45:
        out["notes"].append(f"Listed about {m['days_on_market']} days: leverage for a lower number.")
    lpc = m.get("last_price_change")
    if lpc and lpc.get("delta", 0) < 0:
        out["notes"].append(f"Seller already cut the price by ${abs(lpc['delta']):,.0f} on {lpc['at'][:10]}.")
    return out


def open_questions(m: dict) -> list[dict]:
    """What still has to be learned to judge the match and the value, most important first."""
    eq, h = m.get("equipment") or {}, m.get("history") or {}
    is_gt3 = m.get("variant") in ("gt3", "gt3_touring")
    q: list[dict] = [{"key": "availability", "why": "match", "ask": "Is the car still available, and is anyone ahead of me on it?"}]
    lift = (eq.get("lift") or {}).get("status")
    if lift in (None, "unknown", "decode_absent"):
        q.append({"key": "lift", "why": "hard requirement",
                  "ask": "Does it have the factory Front Axle Lift System? I need it for my driveway, so this one is a must."})
    elif lift == "seller_stated":
        q.append({"key": "lift", "why": "hard requirement", "ask": "Can you confirm the Front Axle Lift System is on the window sticker?"})
    q.append({"key": "documents", "why": "verification",
              "ask": "Could you send the window sticker or build sheet and the Carfax or AutoCheck?"})
    if (h.get("accident") or {}).get("status") in (None, "unknown"):
        q.append({"key": "accident", "why": "hard requirement", "ask": "Any accident, damage or paintwork history, including anything not on the report?"})
    q.append({"key": "track", "why": "hard requirement",
              "ask": "Has it been tracked?" + (" Can you share the DME over-rev report?" if is_gt3 else " Any track or DE use you know of?")})
    if not m.get("transmission"):
        q.append({"key": "transmission", "why": "hard requirement" if m.get("generation") == "991.2" else "preference",
                  "ask": "Is it the 6-speed manual or PDK?"})
    if not (eq.get("seats") or {}).get("value"):
        q.append({"key": "seats", "why": "preference", "ask": "Which seats does it have: full buckets or the adjustable sport seats?"})
    if not is_gt3 and (eq.get("weissach") or {}).get("value") is None:
        q.append({"key": "weissach", "why": "preference", "ask": "Does it have the Weissach Package?"})
    if m.get("price") is None:
        q.append({"key": "price", "why": "value", "ask": "What is the asking price?"})
    if m.get("cpo") and m.get("cpo_status", "verified") == "verified":
        q.append({"key": "warranty", "why": "value", "ask": "When does the Porsche Approved warranty run to?"})
    elif m.get("cpo"):
        q.append({"key": "warranty", "why": "value",
                  "ask": "Is the car Porsche Approved Certified Pre-Owned, and if so when does that warranty run to?"})
    elif (m.get("seller") or {}).get("kind") == "porsche_dealer":
        q.append({"key": "warranty", "why": "value",
                  "ask": "What was the original in-service date, and can the car be Porsche Approved certified?"})
    else:
        q.append({"key": "warranty", "why": "value", "ask": "What was the original in-service date, and is any factory warranty left?"})
    q.append({"key": "condition", "why": "value",
              "ask": "How many owners, is the service history complete, and is there any PPF, modification, or wear I should know about (tires, brakes, windshield, wheels)?"})
    if m.get("location_note"):
        q.append({"key": "location", "why": "logistics", "ask": "Which location is the car physically at?"})
    q.append({"key": "otd", "why": "value",
              "ask": "What is your best out-the-door price for a Texas buyer, with dealer fees itemized? I would register it in Austin."})
    return q


def inquiry_email(m: dict) -> dict:
    """First-contact email in the buyer's voice.  It asks; it does not reveal a budget or make an offer."""
    s = m.get("seller") or {}
    car = f"{m.get('year')} {m.get('variant_label')}"
    colour = f" in {m['exterior_color']}" if m.get("exterior_color") else ""
    ident = ", ".join(x for x in (f"stock {m['stock_number']}" if m.get("stock_number") else None,
                                   f"VIN {m['vin']}" if m.get("vin") else None) if x)
    qs = open_questions(m)
    lines = [f"Hi,", "",
             f"I'm interested in your {car}{colour}" + (f" ({ident})" if ident else "") + ". I'm in Austin, Texas and looking to buy "
             "in the near term, so I'd like to work out quickly whether this is the right car.", "",
             qs[0]["ask"], "", "A few questions:"]
    lines += [f"- {x['ask']}" for x in qs[1:]]
    lines += ["", "If it checks out I can move promptly, and I'm glad to get on a call once I've seen the documents.", "", "Thanks,", "Eric"]
    return {"to_name": s.get("name"), "to_email": s.get("email"), "to_phone": s.get("phone"),
            "subject": f"{car}" + (f", stock {m['stock_number']}" if m.get("stock_number") else f", VIN {m['vin'][-6:]}" if m.get("vin") else ""),
            "body": "\n".join(lines)}


def brief(m: dict, cfg: dict) -> dict:
    s, el = m.get("seller") or {}, m["eligibility"]
    return {"key": m["key"], "vin": m.get("vin"), "car": f"{m.get('year')} {m.get('variant_label')} ({m.get('generation')})",
            "asking": m.get("price"), "mileage": m.get("mileage"), "status": m.get("status"),
            "category": el["category"], "unverified": el["unverified"], "preference_match": m["score"]["total"],
            "seller": {"name": s.get("name"), "email": s.get("email"), "phone": s.get("phone"), "website": s.get("website"),
                       "city": s.get("city"), "state": s.get("state"), "location_note": m.get("location_note")},
            "listing_urls": [x["url"] for x in m.get("sources") or [] if x.get("url")],
            "price_plan": target_price(m, cfg), "out_the_door_at_asking": (m.get("otd") or {}).get("total"),
            "questions": open_questions(m), "email": inquiry_email(m)}
