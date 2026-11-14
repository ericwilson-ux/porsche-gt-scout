# Sources and coverage

Checked 2026-11-13.

## Porsche Finder — adapter built, blocked from the cloud runner

Status on 2026-11-13: **not collecting.** finder.porsche.com answers GitHub-hosted runners with a
"Vercel Security Checkpoint" page (HTTP 429) for every path, including robots-allowed sitemaps and
listing pages. That bot check is an access control and is not bypassed (no browser spoofing, no
challenge solving). The scheduled workflow therefore runs MarketCheck only (`PGT_SOURCES`).

What the adapter does when it can reach the site (verified on the three example listings, fetched
in a normal browser: 32/32, 36/36 and 47/47 gallery images; lift option codes 474, 2UH, 474):

| | |
|---|---|
| Discovery | `finder.porsche.com/sitemap.xml` lists one sitemap per US dealer (about 230); each lists every listing URL with a `lastmod`. |
| Detail | `/us/en-US/details/<slug>`: VIN, price and fee breakdown, mileage, colours, factory option list with codes, Porsche Approved flag, warranty, owners, service history, seller, window-sticker / build-sheet availability, every gallery image id. |
| Not used | Search pages and `/api` (both disallowed by robots.txt). |

Ways to restore it, in order of preference:
1. Run the same workflow on a self-hosted runner on a home machine (`runs-on: self-hosted`, set
   repository variable `PGT_SOURCES=porsche_finder,marketcheck`). Only valid if the site serves an
   honestly identified client from that network without a challenge; test with `python -m pgt probe <url>`.
2. Ask Porsche Cars North America or a dealer group for a sanctioned inventory feed.
3. Verify individual shortlisted cars by hand from their Porsche Finder page (window sticker / build sheet).

Consequence today: no factory option data, so every car's front-lift status is `unknown` and no car
can reach **confirmed**; qualifying cars appear as **leads**.

## MarketCheck — covered when a key is configured

Licensed dealer-inventory API (`GET https://api.marketcheck.com/v2/search/car/active`).
One facet call per week discovers the feed's trim names; each run then pages through used
Porsche listings for those trims (50 per call) in model-year buckets 2022-2023, 2024-2025, 2026-2027. Every row is re-classified locally and checked
against its VIN, so a mislabelled 911 cannot enter as a GT3.

- Supplies: VIN, price, miles, colours, dealer, photo links, days on market, CPO flag, clean-title flag, previous price.
- Does not supply: factory option lists. Front lift on a feed-only car is `unknown` → the car is a *lead* until Porsche Finder or the seller documents it.
- Budget: roughly 10-13 calls per run against the free plan's 500 per month. If a run hits the plan's pagination cap it is recorded as `partial` and no listing is aged out.
- Live since 2026-11-13: 644 used listings across three model-year buckets (the feed will not page past 500 rows per query), about 16 calls per run.

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
