# Kamion Inspect — data pipeline

Collects, cleans, deduplicates, and partitions Turkish used tractor-unit
(çekici) listings into `output/listings.csv`, which
`backend/scripts/load_listings.py` loads into the backend's `listings`
table. See `docs/plan.md` section 3 for the full data collection rules this
implements.

## Source bake-off (fetched and verified 2026-09-12)

Earlier notes here were written in a network-sandboxed environment and could
only guess. These numbers come from actually fetching the pages.

| Source | Verdict | Evidence |
|---|---|---|
| **truckmarket.com.tr** (Ford Trucks Türkiye certified-used) | **SELECTED — implemented in `sources/truckmarket_adapter.py`** | 218 listings on a single `/arac-listesi` page, no pagination. Every detail page carries explicit `Araç Tipi` (Çekici), `Şehir` (city), `Çekiş Tipi` (4x2/6x2/6x4), `Model Yılı`, `Km` and a ₺ price. Stable integer listing ids (`/arac-detay/13824`). Plain HTTP fetch with an honest User-Agent works — **no anti-bot evasion needed or used**. Turkey-located by construction (OEM's domestic retail channel). |
| **truck1.com.tr** | Rejected for P0 | Turkey is a minority of its inventory. Country mentions on the çekici page: Hollanda 26, Türkiye 9, Belçika 7, Polonya 5, Almanya 3, plus FR/LT/RO/ES/IT. Confirms the pan-European mixing problem — usable later only with strict location filtering. |
| `truck1-tr.com` | Does not exist | TLS fails: `no alternative certificate subject name matches`. An earlier research pass recommended this hostname; it is not a real site. The real host is `www.truck1.com.tr`. |
| **arabam.com** | Deferred | Its `robots.txt` itself returns a Cloudflare JS challenge to a plain request, so even reading the crawl rules requires passing bot management. Would need a headless browser + proxies. Out of scope: we don't implement evasion. |
| sahibinden.com | **Excluded** | [ToS](https://www.sahibinden.com/sozlesmeler/bireysel-hesap-sozlesmesi-0) explicitly restricts collection and AI use. |
| `seralifatih/Turkish-Automotive-Intelligence-Suite` | Not used | Passenger-cars only (`/ikinci-el/otomobil/`), no truck/çekici path, no LICENSE file, 0 stars/forks, and its distinguishing code is Sahibinden bot-defense evasion. |

### Legal/etiquette note on truckmarket.com.tr

`https://www.truckmarket.com.tr/robots.txt` returns the site's branded 404 —
**no robots.txt exists**, so there is no machine-readable crawl directive to
honour or violate. No terms-of-use or KVKK link was discoverable in the page
markup either. Absence of a prohibition is **not** the same as explicit
permission, so the adapter stays deliberately conservative: one request at a
time, 1.5s apart, honest self-identifying User-Agent, a single one-off
collection run rather than continuous monitoring, and no evasion of any kind.
If the site ever starts challenging requests, that's a signal to stop and ask,
not to work around.

### VAT basis

truckmarket does not publish a VAT/tax basis per listing, so every record
from it is `vat_basis="unknown"`. That is deliberate and must not be
"filled in" — the pricing engine compares only listings that share a basis,
so unknown matches unknown and can never silently merge with a
VAT-inclusive listing from a future source.

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
