# Kamion Inspect — data pipeline

Collects, cleans, deduplicates, and partitions Turkish used tractor-unit
(çekici) listings into `output/listings.csv`, which
`backend/scripts/load_listings.py` loads into the backend's `listings`
table. See `docs/plan.md` section 3 for the full data collection rules this
implements.

## Candidate sources (researched, not yet scraped — see caveat below)

**This sandbox cannot reach the public internet** — outbound HTTPS to every
classifieds domain tried (arabam.com, truck1.com.tr, even google.com) was
rejected at the egress proxy level with `connect_rejected (organization
policy)`, confirmed via direct `curl`. So none of the below has been fetched
or verified against a live page; it's from search-snippet research only.
**Whoever runs this next needs to do a 2-minute manual robots.txt/ToS check
before scraping anything, on a network that can actually reach these
sites.**

| Source | Recommendation | Why |
|---|---|---|
| **arabam.com** → `/ikinci-el/ticari-arac/cekici` | Build here first | Largest apparent Turkish-only çekici inventory (thousands of listings site-wide, dedicated çekici pages), all Turkey-located — doesn't have Truck1's foreign-inventory mixing problem. No explicit scraping ban surfaced in search snippets, but **unconfirmed** — it's a major commercial platform, likely has bot protection; check robots.txt/ToS first. |
| **OEM certified-used portals** — TruckMarket (Ford Trucks, truckmarket.com.tr), TruckStore (Mercedes-Benz Türk, truckstore.com/TR) | Build in parallel, lowest legal risk | Manufacturers' own retail channels, not third-party classifieds — no aggregator ToS conflict. Clean per-vehicle pages. Downside: small volume per brand (dozens, not hundreds) — combine 2-3 OEM portals to add meaningful volume alongside arabam.com. |
| Truck1.com.tr / via-kamyon.com | Deprioritize | Pan-European aggregators; Turkey is one filter among many countries, and these typically license their listing data to dealers — a strong signal their ToS restricts scraping. |
| sahibinden.com | **Excluded** | [ToS](https://www.sahibinden.com/sozlesmeler/bireysel-hesap-sozlesmesi-0) explicitly restricts collection and AI use — see `docs/plan.md`. |

## The three ways to get to 50+ records (plan's hour-4 checkpoint)

1. **Fastest, works right now, no network needed here:** hand-collect
   listings into `templates/manual_listings.csv` (same columns as
   `ListingRecord`) and run:
   ```bash
   python -m data_pipeline.run --source manual_csv --csv templates/manual_listings.csv
   ```
   This is genuinely how the plan expects the team to bootstrap if a
   scraper isn't ready — "gathering a few hundred is an hour of work" per
   the sponsor brief. Not glamorous, but it unblocks the backend/pricing
   work immediately and produces real, honestly-sourced data (each row's
   `source_url` should be the actual listing page, so the appraisal
   endpoint's comparables are always traceable back to something real).

2. **Higher volume, needs finishing:** fill in the TODOs in
   `sources/arabam_adapter.py` once you have real network access — the
   adapter interface, cleaning, dedup, and partitioning are all already
   wired up, only the selectors are missing. Then:
   ```bash
   python -m data_pipeline.run --source arabam_cekici
   ```

3. **Supplemental, lower risk:** write a small adapter for TruckMarket
   and/or TruckStore's used-truck pages the same way (subclass
   `sources.base.SourceAdapter`) to blend in a legally cleaner slice
   alongside arabam.com.

## Collection rules this pipeline enforces

- **Location filtering:** every `ListingRecord.country` must be `"TR"` —
  `ListingRecord.validate()` flags anything else. A Turkish-language site is
  not necessarily Turkish inventory (Truck1 mixes countries).
- **Excluded listing types:** `pipeline.is_excluded_listing_text()` flags
  deposits, "price on request," new, and salvage listings in Turkish and
  English (`kapora`, `fiyat sorunuz`, `hasar kayıtlı`, `sıfır`, ...) — check
  a listing's raw title/price text against it before building a
  `ListingRecord`.
- **VAT basis is preserved, never silently converted:** `vat_basis` stays
  whatever the listing actually says (`vat_included` / `vat_excluded` /
  `vat_exempt` / `unknown`); the pricing engine hard-filters on it so
  incompatible tax bases never end up in the same comparable pool.
- **Dedup:** `pipeline.clean_and_dedupe()` drops near-identical reposts
  (same make/model/year within a coarse mileage/price bucket) before
  writing output.
- **Partitioning:** `partition.assign_partition()` deterministically splits
  ~60/20/20 into dev/calibration/test, keyed by vehicle identity (not row
  order), so reposts of the same truck can never land in different
  partitions or leak into the test set's comparable pool.

## Running the tests

From the repo root (not from inside `data_pipeline/`):
```bash
python -m venv data_pipeline/.venv && source data_pipeline/.venv/bin/activate
pip install -r data_pipeline/requirements.txt
python -m pytest data_pipeline/tests
```

## What's real vs. stubbed right now

- **Real, tested:** `schema.py` validation, `pipeline.py` cleaning/dedup,
  `partition.py` splitting, `sources/csv_source.py`, the full `run.py` CLI
  (verified end-to-end against `templates/manual_listings.csv`), and
  `backend/scripts/load_listings.py` on the receiving end.
- **Not yet real:** `sources/arabam_adapter.py` is an unverified skeleton —
  see its docstring. It raises `NotImplementedError` on purpose rather than
  guessing at selectors and silently returning wrong data.
- **Not built yet:** the offline held-out calibration script
  (`evaluate.py`) that's supposed to feed a real conformal quantile back
  into the backend, and the OEM-portal adapters. Both are natural next
  steps once a first source is producing real volume.
