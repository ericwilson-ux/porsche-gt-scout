# Sources and coverage

Checked 2026-11-13.

## Porsche Finder — covered

| | |
|---|---|
| Discovery | `finder.porsche.com/sitemap.xml` lists one sitemap per US dealer (`/us/en-US/sitemap-dealer/<id>/sitemap.xml`, about 230). Each dealer sitemap lists every listing URL with a `lastmod`. Listing slugs name the model, so target cars are selected without opening other pages. |
| Detail | `/us/en-US/details/<slug>`. The server-rendered HTML embeds the full record: VIN, model year, price and fee breakdown, mileage, colours, transmission, factory option list with option codes, Porsche Approved flag, warranty, previous owners, service history, seller, window-sticker / build-sheet availability, and every gallery image id with category. |
| Not used | Search pages (filter URLs are disallowed by robots.txt) and `/api` (disallowed). |
| Volume | About 231 sitemap requests plus one request per target listing per day, one request per second. |
| Removal signal | Listing absent from its dealer sitemap **and** its page returns 404, on two separate days. |
| Known gaps | No accident/damage field is published for US listings (`accidentFree` is empty), so accident history can only be seller-stated. Window sticker and build sheet PDFs sit behind `/api` and are linked, not downloaded. Listing first-seen dates are not published; days on market count from first observation. |

Verified against the three example listings (ZO80OE, 058WLN, 5007XV): 32/32, 36/36 and 47/47
gallery images; lift option codes 474, 2UH, 474.

## MarketCheck — covered when a key is configured

Licensed dealer-inventory API (`GET https://api.marketcheck.com/v2/search/car/active`).
One facet call per week discovers the feed's trim names; each run then pages through used
Porsche listings for those trims (50 per call). Every row is re-classified locally and checked
against its VIN, so a mislabelled 911 cannot enter as a GT3.

- Supplies: VIN, price, miles, colours, dealer, photo links, days on market, CPO flag, clean-title flag, previous price.
- Does not supply: factory option lists. Front lift on a feed-only car is `unknown` → the car is a *lead* until Porsche Finder or the seller documents it.
- Budget: roughly 10-13 calls per run against the free plan's 500 per month. If a run hits the plan's pagination cap it is recorded as `partial` and no listing is aged out.
- This adapter has been unit-tested against the documented response shape only; it has not yet run against the live API (no key was available when it was written).

## Cars.com — not covered

Cars.com disallows automated access in robots.txt and publishes no inventory API. It is not
scraped. Dealer stock that appears on Cars.com is reached through the MarketCheck feed, which
collects from dealer sites; that is a substitute for the inventory, not for Cars.com itself
(Cars.com-only listings, its price badges and its listing history are not available).

## Autotrader, CarGurus — not covered directly

Neither permits automated collection. An official CarGurus connector for Claude exists
(listing search and detail) and can be used in a Claude session for manual cross-checks.

## Bring a Trailer, Cars & Bids, PCarMarket — manual

Not crawled (access is blocked or disallowed). Completed sales are entered in
`config/manual_sales.csv` with the public URL of each result. Reserve-not-met auctions are not
sales and are not entered. Until rows are added, the Market page shows no realised prices and
says so.

## What would improve coverage

1. MarketCheck's VIN decode (`/decode/car/neovin/{vin}/specs`, about $0.08 per call) returns
   installed option packages; calling it for feed-only leads would verify front lift without the seller.
2. MarketCheck's past-90-days search adds delisted listings (last asking prices) for deeper comps.
3. A paid auction-results data source would replace the manual sales file.
