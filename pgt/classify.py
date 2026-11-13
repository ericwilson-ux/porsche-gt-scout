"""Variant / generation classification and attribute normalisation.

The goal is to never confuse an ordinary 911 (or a GT3 RS, GT4, Spyder, 991 GT3)
with one of the four target variants.  Text classification must agree with the
VIN-derived Porsche type when a VIN is present.
"""
from __future__ import annotations

import re

from . import vin as vinlib

VARIANTS = ("gt3", "gt3_touring", "gt4rs", "spyder_rs")
VARIANT_LABEL = {
    "gt3": "911 GT3",
    "gt3_touring": "911 GT3 Touring",
    "gt4rs": "718 Cayman GT4 RS",
    "spyder_rs": "718 Spyder RS",
}
_EXPECTED_TYPE = {"gt3": "992", "gt3_touring": "992", "gt4rs": "982", "spyder_rs": "982"}


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("-", " ")).strip()


def classify_text(text: str | None) -> str | None:
    """Return a target variant key from free text (title / trim / slug), else None."""
    t = _norm(text)
    if not t:
        return None
    if re.search(r"\bgt3\b", t):
        if re.search(r"\bgt3 ?rs\b|\bgt3 r\b|\bcup\b|\bgt3 r rennsport\b", t):
            return None
        if re.search(r"\btouring\b", t):
            return "gt3_touring"
        return "gt3"
    if re.search(r"\bgt4 ?rs\b", t):
        if "clubsport" in t:
            return None
        return "gt4rs"
    if re.search(r"\bspyder ?rs\b", t):
        return "spyder_rs"
    return None


def generation_for(variant: str, year: int | None, platform_text: str | None = None) -> str | None:
    """992.1 / 992.2 for GT3s, 982 for the 718 RS cars."""
    if variant in ("gt4rs", "spyder_rs"):
        return "982"
    p = _norm(platform_text)
    if re.search(r"\b992 ?(ii|2)\b|992\.2", p):
        return "992.2"
    if re.search(r"\b992 ?(i|1)\b|992\.1", p):
        return "992.1"
    if re.search(r"\b991\b", p):
        return "991"
    if year is None:
        return None
    if year <= 2019:
        return "991"
    if 2022 <= year <= 2024:
        return "992.1"
    if year >= 2025:
        return "992.2"
    return None


def classify(text: str | None, vin: str | None = None, year: int | None = None,
             platform_text: str | None = None) -> dict:
    """Classify a listing.  Returns {variant, generation, year, issues[]}.

    variant is None when the car is not one of the four targets or when the
    VIN contradicts the text (e.g. a "GT3" title on a 991 VIN).
    """
    issues: list[str] = []
    variant = classify_text(text)
    v = vinlib.normalize(vin)
    vin_year = vinlib.model_year(v) if v else None
    if v and not vinlib.is_valid(v):
        issues.append("vin_check_digit_invalid")
    if year and vin_year and abs(year - vin_year) > 0:
        issues.append(f"year_mismatch_listing_{year}_vin_{vin_year}")
    yr = vin_year or year
    if variant and v:
        if not vinlib.is_porsche_sports_car(v):
            issues.append("vin_not_porsche_sports_car")
            variant = None
        else:
            ptype = vinlib.porsche_type(v)
            if ptype and ptype != _EXPECTED_TYPE[variant]:
                issues.append(f"vin_type_{ptype}_not_{_EXPECTED_TYPE[variant]}")
                variant = None
    gen = generation_for(variant, yr, platform_text) if variant else None
    if variant in ("gt3", "gt3_touring") and gen == "991":
        issues.append("generation_991_not_target")
        variant = None
    return {"variant": variant, "generation": gen, "year": yr, "issues": issues}


# ---- colours -----------------------------------------------------------------
_EXT_FAMILIES = [
    ("white", r"white|carrara"),
    ("silver", r"silver|rhodium|dolomite"),
    ("purple", r"purple|violet|ultraviolet|viola|amethyst|rubystar|ruby star|frozen berry|star ruby"),
    ("gray", r"grey|gray|agate|slate|arctic|graphite|quartz|vanadium|anthracite|chalk|crayon"),
    ("black", r"black|basalt"),
    ("blue", r"blue|gentian|shark|miami|azure|oslo|ipanema|sapphire|maritime|mexico"),
    ("red", r"\bred\b|guards|carmine|cherry|arena"),
    ("yellow", r"yellow"),
    ("green", r"green|python|lizard|mamba|viper|irish|brewster|jade|mint|aventurine"),
    ("orange", r"orange|lava|papaya"),
    ("brown", r"brown|mahogany|cognac|macadamia|truffle|bronze"),
]
_INT_FAMILIES = [
    ("beige", r"beige|luxor|sand|crema|cream|mojave"),
    ("tan", r"\btan\b|cognac|natural|espresso|truffle|saddle|mocha|club leather|brown|chestnut"),
    ("red", r"\bred\b|bordeaux|carmine|guards|garnet"),
    ("blue", r"\bblue\b|graphite blue|navy|yachting"),
    ("gray", r"grey|gray|slate|agate|crayon|chalk|pebble|platinum"),
    ("black", r"black"),
]


def exterior_family(name: str | None) -> str | None:
    t = _norm(name)
    if not t:
        return None
    # order matters for names such as "GT Silver Metallic" / "Arctic Grey"
    if re.search(r"silver", t):
        return "silver"
    for fam, pat in _EXT_FAMILIES:
        if re.search(pat, t):
            return fam
    return "other"


def interior_family(name: str | None) -> str | None:
    """Family of the *primary* interior colour.

    Porsche interior names carry stitching colours ("... in Black with Arctic
    Grey Stitching"); the stitching clause is dropped before matching.
    """
    t = _norm(name)
    if not t:
        return None
    t = re.sub(r"\b(with|and|w/)\b[^,;]*stitching[^,;]*", " ", t)
    t = re.sub(r"\bstitching in [a-z ]+", " ", t)
    two_tone = re.search(r"black\s*/\s*([a-z ]+)|black and ([a-z]+ ?[a-z]*)", t)
    for fam, pat in _INT_FAMILIES:
        if re.search(pat, t):
            return fam
    if two_tone or "standard interior" in t:
        return "black"        # Porsche's no-cost standard interior is black
    return "other"


def transmission_kind(text: str | None) -> str | None:
    t = _norm(text)
    if not t:
        return None
    if re.search(r"pdk|doppelkupplung|dual clutch|automatic|auto\b|dct", t):
        return "pdk"
    if re.search(r"manual|6 speed|six speed|\bmt\b|stick", t):
        return "manual"
    return None


# ---- equipment ---------------------------------------------------------------
LIFT_CODES = {"474", "2UH"}          # 718 (982) / 911 (992) Front Axle Lift System
_LIFT_RE = re.compile(r"front[\s-]*axle[\s-]*lift|\blift system\b|nose lift|front lift|lifting system", re.I)
_WEISSACH_RE = re.compile(r"weissach", re.I)
_BUCKET_RE = re.compile(r"full bucket|bucket seat|lightweight bucket|carbon.{0,20}bucket", re.I)
_ADJ_RE = re.compile(r"adaptive sport seats|sport seats plus|18[\s-]*way|4[\s-]*way|sports seats", re.I)
_PCCB_RE = re.compile(r"pccb|ceramic composite", re.I)
_TRACK_RE = re.compile(
    r"\btrack(ed| day| days| use| time| events?)\b|\bhpde\b|\bde events?\b|autocross|"
    r"roll (cage|bar)|half cage|race(d)? car|time attack|lap(s|ped) at|pca club race", re.I)
_NO_TRACK_RE = re.compile(r"never (been )?track|no track|not track|never raced|never seen (a |the )?track", re.I)
_CLEAN_RE = re.compile(r"clean (car ?fax|auto ?check|history|title)|no accidents?|accident[\s-]*free|zero accidents", re.I)
_ACCIDENT_RE = re.compile(
    r"accident reported|damage reported|prior (accident|damage|paintwork repair)|"
    r"salvage|rebuilt title|structural damage|frame damage|branded title", re.I)


def has_lift_text(text: str | None) -> bool:
    return bool(text and _LIFT_RE.search(text))


def detect_equipment(options: list[dict], standard: list[str], highlights: list[str]) -> dict:
    """Derive lift / Weissach / seats / brakes from a manufacturer equipment list.

    `options` are factory-selected options [{code, name}], `standard` the
    standard-equipment names, `highlights` normalised highlight keys.
    Only meaningful when the source provides a real option list.
    """
    names = [o.get("name") or "" for o in options]
    codes = {str(o.get("code") or "").upper() for o in options}
    hl = {h.lower() for h in highlights}
    out: dict = {}
    out["lift"] = bool(codes & LIFT_CODES or any(_LIFT_RE.search(n) for n in names) or "lifting-system" in hl)
    out["weissach"] = any(_WEISSACH_RE.search(n) for n in names)
    opt_bucket = any(_BUCKET_RE.search(n) for n in names) or "full-bucket-seats" in hl
    opt_adj = any(_ADJ_RE.search(n) for n in names) or "sports-seats" in hl
    std_bucket = any(_BUCKET_RE.search(n) for n in standard)
    std_adj = any(_ADJ_RE.search(n) for n in standard)
    if opt_adj and not opt_bucket:
        out["seats"] = "adjustable"
    elif opt_bucket:
        out["seats"] = "bucket"
    elif std_bucket and not std_adj:
        out["seats"] = "bucket"
    elif std_adj:
        out["seats"] = "adjustable"
    else:
        out["seats"] = None
    out["pccb"] = any(_PCCB_RE.search(n) for n in names) or "pccb" in hl
    return out


def scan_seller_text(text: str | None) -> dict:
    """Signals from free-form seller comments.  These are claims, never verification."""
    t = text or ""
    track = bool(_TRACK_RE.search(t)) and not _NO_TRACK_RE.search(t)
    return {
        "claims_clean_history": bool(_CLEAN_RE.search(t)),
        "mentions_accident": bool(_ACCIDENT_RE.search(t)),
        "mentions_track_use": track,
        "mentions_lift": bool(_LIFT_RE.search(t)),
        "mentions_weissach": bool(_WEISSACH_RE.search(t)),
    }
