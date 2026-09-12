# Kamion Inspect — backend

FastAPI service implementing `docs/api-contract.md`: sessions, media upload
with quality/duplicate/vehicle checks, a comparable-based pricing engine, and
appraisals that always cite the evidence and comparables behind them.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs` once running.

By default `VISION_ADAPTER=mock` — the pipeline runs end-to-end with no API
key, but photo analysis is a placeholder. For real appraisals, set
`VISION_ADAPTER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.

## Tests

```bash
pytest
```

`test_sessions_flow.py` seeds fake `Listing` rows directly (standing in for
`data_pipeline/` output) and drives the full create-session → declare-specs →
appraise loop through the actual HTTP API, so it doubles as a contract test.

## Layout

```
app/
  main.py              FastAPI app, router wiring
  config.py            env-driven settings
  db.py                SQLAlchemy engine/session
  models.py            ORM: sessions, media, details, findings, listings, appraisals
  schemas.py           Pydantic request/response shapes
  constants.py         required capture components, coverage/status vocab
  routers/
    sessions.py        POST /sessions, GET /sessions/{id}, PATCH .../details
    media.py           POST /sessions/{id}/media — quality + vision pipeline
    appraisals.py       POST /sessions/{id}/appraisals — pricing pipeline
  services/
    quality.py         resolution/brightness/blur/duplicate checks (Pillow+numpy)
    vision.py           VisionAdapter interface + mock + Anthropic implementation
    pricing.py           comparable filtering, weighted median, conformal range
    session_state.py     coverage computation + next-capture-instruction logic
```

## What's real vs. stubbed right now

- **Real, working, tested:** the API contract, the SQLite schema, the
  quality/duplicate checks, the comparable filtering + weighted-median +
  conformal-range math (`services/pricing.py`, see `tests/test_pricing.py`
  and `tests/test_sessions_flow.py`).
- **Stubbed pending a real dataset:** there are no `Listing` rows yet — see
  `data_pipeline/`. Until it's populated, every appraisal will correctly
  return `insufficient_market_data`.
- **Stubbed pending an API key:** `VISION_ADAPTER=mock` never really looks at
  pixels beyond the quality checks. Swap to `anthropic` + a key for the demo.
- **Not yet implemented:** video/frame-sequence sampling (`kind="video"` is
  accepted and stored, but not decoded into frames — needs `ffmpeg`, see
  `docs/plan.md`), and the offline held-out calibration script
  (`estimate_quantile_from_comparables` is an in-request provisional
  substitute — every response using it says so in `reasons`).
