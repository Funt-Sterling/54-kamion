# API contract — Kamion Inspect backend

Base URL: `http://localhost:8000` in dev (see `backend/.env.example`).
All bodies are JSON except media upload, which is `multipart/form-data`.

## `POST /sessions`

Start one vehicle inspection.

Request body: `{}` (empty — a session starts blank).

Response `201`:
```json
{
  "id": "sess_...",
  "status": "collecting_evidence",
  "created_at": "2026-09-12T20:00:00Z"
}
```

## `POST /sessions/{id}/media`

Accept a photo, video, or sampled frame. `multipart/form-data`:

- `file`: the image/video binary
- `kind`: `photo` | `video` | `frame`
- `source`: `captured` | `imported`
- `component_hint` (optional): what the uploader believes this shows, e.g. `front_exterior`, `tire`, `odometer`

Response `201`:
```json
{
  "id": "media_...",
  "session_id": "sess_...",
  "kind": "photo",
  "accepted": true,
  "reject_reason": null,
  "component_tag": "front_exterior",
  "quality_notes": []
}
```

`accepted: false` means the quality/duplicate/vehicle check rejected the item (e.g. too dark, not a truck, duplicate of an existing frame) — `reject_reason` explains why and `GET /sessions/{id}` will include the resulting capture instruction.

## `GET /sessions/{id}`

Return progress, coverage, findings, and next capture instruction.

Response `200`:
```json
{
  "id": "sess_...",
  "status": "collecting_evidence",
  "coverage": {
    "front_exterior": "captured",
    "rear_exterior": "missing",
    "tire": "attention",
    "odometer": "missing"
  },
  "next_instruction": "Capture the dashboard odometer straight-on, in good light.",
  "declared_details": [
    {"field": "year", "value": "2019", "source": "seller_declared"}
  ],
  "findings": [],
  "latest_appraisal_id": null
}
```

## `PATCH /sessions/{id}/details`

Record confirmed or seller-declared specifications with provenance.

Request:
```json
{"field": "mileage_km", "value": "480000", "source": "seller_declared"}
```

Response `200`: the updated details list (same shape as in `GET /sessions/{id}`).

## `POST /sessions/{id}/appraisals`

Produce a versioned appraisal from the current evidence. No request body.

Response `201`:
```json
{
  "id": "appr_...",
  "session_id": "sess_...",
  "version": 1,
  "status": "priced",
  "price_low": 850000,
  "price_mid": 960000,
  "price_high": 1080000,
  "currency": "TRY",
  "comparable_count": 8,
  "comparables": [{"listing_id": "lst_...", "similarity_weight": 0.82}],
  "findings": [
    {
      "component": "front_left_tire",
      "observation": "Visible sidewall cracking",
      "media_id": "media_...",
      "visibility": "clear",
      "recommended_action": "none"
    }
  ],
  "reasons": []
}
```

`status` is one of `priced`, `needs_evidence`, `unsupported`, `insufficient_market_data`, `inspection_required`. Only `priced` carries a price range; every other status carries `reasons` explaining what's missing and `next_instruction`-style guidance surfaces via `GET /sessions/{id}`.

## Notes

- Every finding references the `media_id` it came from — never present a finding without evidence.
- Every price explanation references the `listing_id`s of the comparables actually used — never a bare number.
- These endpoints are intentionally minimal for the hackathon; the mobile/web client polls `GET /sessions/{id}` after each upload rather than needing a websocket.
