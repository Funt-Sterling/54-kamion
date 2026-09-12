# Kamion Inspect

An appraisal assistant for used trucks: point a phone at a tractor unit,
get a Turkish-market price range backed by real comparables, and
photo-linked condition findings — or, when the evidence isn't there yet, a
specific instruction for what photo would help, instead of a confident
guess. Built for the [Kamion hackathon challenge](docs/brief.md)
("$1,000 — What's This Truck Worth?").

## Why it's built this way

The brief is explicit about what loses: *"a thin wrapper that sends photos
to a vision API and prints whatever number comes back."* So the vision
model here only ever produces **observations** (is this a tractor unit,
what's visible on this tire, is the odometer legible) — never a price. The
price comes from a separate, auditable pricing engine that filters
comparable Turkish listings and computes a similarity-weighted median with
a calibrated range. See `docs/plan.md` for the full reasoning and math, and
`backend/README.md` for what's implemented vs. stubbed right now.

## Repo layout

```
docs/            the sponsor brief, the team's plan, API contract, frontend brief
backend/         FastAPI service: sessions, media, quality/vision checks, pricing
data_pipeline/   Turkish market listing collection, cleaning, partitioning
evaluation/      held-out evaluation (coverage, width, price error) — not yet built
mobile/ or web/  client app — not yet built, see docs/frontend-brief.md
```

## Quickstart

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload   # http://localhost:8000/docs
pytest                          # 12 tests, all green with zero external deps

# Data pipeline (separate venv — different dependency set)
cd ../data_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
python -m pytest data_pipeline/tests   # run from repo root

# Get some comparables into the DB so appraisals can actually price something
python -m data_pipeline.run --source manual_csv --csv data_pipeline/templates/manual_listings.csv
cd backend && python scripts/load_listings.py ../data_pipeline/output/listings.csv
```

## Current state (see docs/plan.md's "What's real vs. stubbed" notes)

- **Working and tested:** the full API contract, SQLite schema, image
  quality/duplicate checks, and the comparable-based pricing math
  (weighted median + conformal-style range) — 12 backend tests + 9 data
  pipeline tests, all passing against real HTTP requests and real DB rows,
  no mocking of the logic under test.
- **Needs a real dataset:** `data_pipeline/` has a working CSV-import path
  and a documented-but-unverified scraper skeleton for arabam.com (this
  sandbox has no internet access to finish it — see
  `data_pipeline/README.md`). Until real listings are loaded, every
  appraisal correctly returns `insufficient_market_data` rather than
  inventing a price.
- **Needs an API key:** vision analysis defaults to a `mock` adapter so the
  pipeline is runnable offline. Set `VISION_ADAPTER=anthropic` +
  `ANTHROPIC_API_KEY` in `backend/.env` for real photo analysis.
- **Not built yet:** the client app (see `docs/frontend-brief.md` for the
  recommended mobile-web direction and screen spec), video/frame sampling,
  and the offline held-out calibration script.

## Team workflow

See `docs/plan.md` section 5 for the hour-by-hour plan and workstream
split. Keep `docs/api-contract.md` as the source of truth for
frontend/backend integration — update it in the same PR as any endpoint
change.
