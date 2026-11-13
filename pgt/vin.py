"""VIN validation and Porsche-specific decoding.

Only uses facts encoded in the VIN itself: check digit (pos 9), model year
(pos 10) and the Porsche type number (positions 7, 8 and 12 -> e.g. 992, 982).
"""
from __future__ import annotations

import re

_TRANSLIT = {**{str(d): d for d in range(10)},
             "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
             "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
             "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 2010 = A ... repeats every 30 years
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize(vin: str | None) -> str | None:
    if not vin:
        return None
    v = re.sub(r"[^A-Za-z0-9]", "", vin).upper()
    return v if len(v) == 17 else None


def is_valid(vin: str | None) -> bool:
    """True when the VIN is well formed and its check digit matches."""
    v = normalize(vin)
    if not v or not _VIN_RE.match(v):
        return False
    total = sum(_TRANSLIT[c] * w for c, w in zip(v, _WEIGHTS))
    check = total % 11
    return v[8] == ("X" if check == 10 else str(check))


def model_year(vin: str | None) -> int | None:
    v = normalize(vin)
    if not v:
        return None
    idx = _YEAR_CODES.find(v[9])
    if idx < 0:
        return None
    return 2010 + idx  # valid for 2010-2039, which covers every target car


def porsche_type(vin: str | None) -> str | None:
    """Porsche type number from VIN positions 7, 8, 12 (e.g. '992', '982', '991')."""
    v = normalize(vin)
    if not v or not v.startswith("WP0"):
        return None
    a, b, c = v[6], v[7], v[11]
    first = "9" if a == "A" else a
    t = f"{first}{b}{c}"
    return t if t.isdigit() else None


def is_porsche_sports_car(vin: str | None) -> bool:
    v = normalize(vin)
    return bool(v and v.startswith("WP0"))
