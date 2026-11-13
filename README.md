# GT Scout

Inventory discovery, hard-requirement verification, preference scoring, market data and a
conditional daily email for four cars: 992 GT3, 992 GT3 Touring, 718 Cayman GT4 RS, 718 Spyder RS.

## How it runs

```
GitHub Actions (daily 10:23 UTC)
  ├─ python -m pgt run
  │    ├─ Porsche Finder: sitemap index → ~230 US dealer sitemaps → target listing pages
  │    ├─ MarketCheck API: used GT3 / GT4 RS / Spyder RS dealer listings (needs MARKETCHECK_API_KEY)
  │    ├─ upsert → price observations → conservative lifecycle → merge by VIN
  │    ├─ eligibility (hard requirements) · preference score · Texas out-the-door · valuation
  │    ├─ queue the day's digest if a new qualifying car appeared
  │    └─ export JSON + copy site/ → _site/
  ├─ commit state/ (SQLite database, digests, optional photo copies) to the `state` branch
  └─ deploy _site/ to GitHub Pages
Claude scheduled task (weekday-agnostic, ~7:10 AM America/Chicago)
  └─ reads state/digests/pending.json → sends one Gmail message → writes a receipt
```

| Piece | Choice | Why |
|---|---|---|
| Database | SQLite file on the `state` branch | One daily writer, relational history (vehicles → listings → prices / photos / evidence / events). The file is versioned on every run, so history survives deployments and every past state is recoverable from git. |
| Site | Static files on GitHub Pages | No server to keep alive; the site can only ever show what the last run stored, and says how old that is. |
| Scheduler | GitHub Actions cron | Unattended, logged, retriable from the Actions tab. |
| Photos | Displayed from the listing site's image host | Every image id is captured; durable copies are optional (below). |

## Setup (once)

1. Create the repository and push this code to `main`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Secrets and variables → Actions:
   - secret `MARKETCHECK_API_KEY` (free plan at developers.marketcheck.com; about 12 calls per run)
   - secret `ALERT_RECIPIENT` (the address the digest is written for)
   - optional variable `PHOTO_ARCHIVE=repo`, optional `EMAIL_MODE=smtp` plus `SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM`
4. Actions → **Daily inventory run** → Run workflow. The first run is the backfill: everything
   it finds is recorded as existing inventory and never emailed as "new".
5. Create the email task described in `docs/EMAIL.md`.

Local use:

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m pgt probe https://finder.porsche.com/us/en-US/details/<slug>     # parse one listing
PGT_STATE=./state python -m pgt run --max-dealers 5                          # small live run
PGT_STATE=/tmp/demo python scripts/seed_examples.py                          # offline, 3 captured examples
```

## Criteria

All buyer criteria live in `config/criteria.toml`: price and mileage caps, preference
weights, out-the-door assumptions, which categories trigger an email, and lifecycle thresholds.
Every run re-evaluates every stored vehicle against the current file.

### Categories

| Category | Meaning |
|---|---|
| confirmed | Every hard requirement passes **with a documented source** |
| lead | Nothing fails, but at least one requirement is undocumented (shown by name) |
| near miss | Only failure is a price up to 5% over the cap |
| excluded | Fails a hard requirement; kept for market data, never alerted |

Evidence levels for the front axle lift: `verified` (option code 474 / 2UH or the option name in
the manufacturer equipment list), `seller_stated` (seller text only), `verified_absent` (equipment
list present, no lift → excluded), `unknown` (no list anywhere → lead).

Accident history passes only on an explicit source flag or an explicit seller statement
("clean Carfax"); CPO status or a clean title alone leaves it unverified.

### Three separate judgements

1. **Eligibility** decides the category. It never changes the score.
2. **Preference match (0-100)**: mileage for age, colours, transmission, seats, Weissach (718),
   CPO/warranty, documentation, distance from Austin. The detail page shows every line.
3. **Deal quality**: asking price against mileage-adjusted comparables (`pgt/valuation.py`).
   No estimate below 5 comparables; confidence is stated from sample size.

## Lifecycle rules

- A listing is `unverified` after one "gone" signal and `removed` only after the source says so on
  two separate days (Porsche Finder: listing page returns 404) or it is absent from three
  successful feed pulls (MarketCheck), and at least 36 hours have passed.
- A failed or partial fetch produces no signal at all. Timeouts, 403/429 and parser failures are
  recorded as *inconclusive*.
- Removed vehicles keep every record. The last asking price is never stored as a sale price.
  Completed sales come only from `config/manual_sales.csv`, each with a public URL.

## Photos

Every gallery image id a source publishes is stored (deduplicated by the source's image id, so
several resolutions of one photo count once). Advertised and captured counts are compared per
car and incomplete galleries are flagged on the card, the detail page and the Sources page.

By default the site displays images from the listing site's own image host. Set
`PHOTO_ARCHIVE=repo` to also keep copies for confirmed and lead cars in the `state` branch so
galleries persist after delisting. Copying a seller's photographs may be restricted by the
source's terms; that switch is the owner's decision.

## Email

At most one digest per America/Chicago date, only when a car newly qualifies, each vehicle
announced once however many sites list it. `alert_log.vehicle_key` and `digests.digest_date`
are primary keys, so re-running any step cannot duplicate an email. See `docs/EMAIL.md`.

## Limits that are not solved by this code

See `docs/SOURCES.md`: Cars.com is not covered directly; auction results are manual; favourites
and comparison selections are stored in the browser, not the database.
