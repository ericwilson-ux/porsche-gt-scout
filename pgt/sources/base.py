"""Shared types for inventory sources."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Listing:
    """One listing on one source, normalised."""
    source: str
    source_listing_id: str
    url: str
    scope: str | None = None            # coverage unit this listing belongs to (dealer id / query)
    vin: str | None = None
    title: str | None = None
    variant: str | None = None
    generation: str | None = None
    year: int | None = None
    condition: str | None = None        # new | used
    cpo: bool | None = None
    price: float | None = None          # advertised vehicle price before dealer fees when the source splits them
    price_as_listed: float | None = None
    dealer_fees_listed: float | None = None
    fee_items: list = field(default_factory=list)
    mileage: int | None = None
    exterior_color: str | None = None
    exterior_hex: str | None = None
    interior_color: str | None = None
    transmission: str | None = None     # manual | pdk
    transmission_raw: str | None = None
    seller: dict = field(default_factory=dict)   # name, kind, street, city, state, zip, phone, website, lat, lon
    options: list = field(default_factory=list)  # [{code, name, group}] factory-selected options
    standard_equipment: list = field(default_factory=list)
    highlights: list = field(default_factory=list)
    has_equipment_list: bool = False    # True when the source exposes manufacturer equipment data
    seller_comment: str | None = None
    photos: list = field(default_factory=list)   # [{id, url, category, position, aspect}]
    photos_advertised: int | None = None
    photos_are_real: bool | None = None
    accident_free: bool | None = None   # explicit source flag only
    carfax_clean_title: bool | None = None
    one_owner: bool | None = None
    previous_owners: int | None = None
    service_history: str | None = None
    warranty: list = field(default_factory=list)
    documents: dict = field(default_factory=dict)  # window_sticker_available, build_sheet_available, ...
    stock_number: str | None = None
    source_first_seen: str | None = None
    source_days_on_market: int | None = None
    source_previous_price: float | None = None
    origin_site: str | None = None      # for aggregators: the site the listing was collected from
    issues: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceResult:
    source: str
    status: str = "ok"                  # ok | partial | failed | skipped
    listings: list = field(default_factory=list)
    scopes_total: int = 0
    scopes_ok: set = field(default_factory=set)       # coverage units fetched successfully
    explicit_gone: set = field(default_factory=set)   # source_listing_ids the source says no longer exist
    checked_ids: set = field(default_factory=set)     # ids whose detail page was conclusively checked this run
    inconclusive: set = field(default_factory=set)    # ids whose check failed (network / parse): never count as a miss
    requests: int = 0
    errors: int = 0
    detail: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
