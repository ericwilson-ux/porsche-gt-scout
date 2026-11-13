"""Estimated out-the-door cost for a buyer registering the car in Austin (Travis County), Texas.

Texas motor vehicle sales/use tax is 6.25% of the sales price (no local add-on) and is
owed to Texas at registration even when the car is bought out of state, with credit for
tax legally paid to the other state.  Dealer documentary fees are part of the taxable price.
Every figure other than the advertised price is an assumption the site lets the user edit.
"""
from __future__ import annotations


def shipping_estimate(road_miles: float | None, o: dict) -> dict:
    if road_miles is None:
        return {"amount": None, "mode": "unknown", "note": "Seller location unknown"}
    if road_miles <= o["drive_radius_miles"]:
        return {"amount": 0.0, "mode": "drive", "note": f"Within {o['drive_radius_miles']} mi: assumed driven home"}
    short = min(road_miles, 500) * o["ship_rate_short"]
    mid = max(0, min(road_miles, 1500) - 500) * o["ship_rate_mid"]
    long_ = max(0, road_miles - 1500) * o["ship_rate_long"]
    amount = max(o["ship_minimum"], o["ship_base"] + short + mid + long_)
    return {"amount": round(amount, -1), "mode": "enclosed_transport",
            "note": f"Enclosed transport estimate for ~{road_miles:,.0f} road miles"}


def out_the_door(price: float | None, seller_state: str | None, road_miles: float | None,
                 listed_fees: float | None, cfg: dict) -> dict | None:
    if price is None:
        return None
    o = cfg["otd"]
    if listed_fees is not None:
        doc, doc_basis = float(listed_fees), "published by dealer"
    elif (seller_state or "").upper() == "TX":
        doc, doc_basis = o["default_doc_fee_tx"], "assumed (typical Texas dealer)"
    else:
        doc, doc_basis = o["default_doc_fee_other"], "assumed (out-of-state dealers vary widely)"
    taxable = price + doc
    tax = round(taxable * o["sales_tax_rate"], 2)
    ship = shipping_estimate(road_miles, o)
    ttl = o["title_fee"] + o["registration_fee"] + o["county_fee"] + o["state_misc_fees"]
    total = price + doc + tax + ttl + (ship["amount"] or 0)
    return {
        "price": price, "dealer_fees": doc, "dealer_fees_basis": doc_basis,
        "sales_tax": tax, "sales_tax_rate": o["sales_tax_rate"],
        "title_registration": round(ttl, 2),
        "shipping": ship["amount"], "shipping_mode": ship["mode"], "shipping_note": ship["note"],
        "total": round(total, 2),
        "assumptions": [
            f"Texas motor vehicle tax {o['sales_tax_rate']:.2%} on price plus dealer fees",
            f"Dealer fees {doc_basis}",
            f"Title ${o['title_fee']:.2f}, registration ${o['registration_fee']:.2f}, "
            f"Travis County ${o['county_fee']:.2f}, state fees ${o['state_misc_fees']:.2f}",
            ship["note"],
            "Excludes emissions inspection, temp tags, any tax collected by another state (credited in Texas)",
        ],
    }
