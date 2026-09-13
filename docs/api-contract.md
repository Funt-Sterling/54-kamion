# API contract — TIRage backend (evidence contract v1)

Base URL: `http://localhost:8000` in dev. JSON everywhere except media upload
(`multipart/form-data`). Source of truth: `backend/app/schemas.py` and
`backend/app/contract.py`.

A vision model's output is a **proposal**, never a fact. It is strictly parsed
(`services/proposal.py`), then only supported claims are admitted
(`services/admission.py`). The client's requested view is request metadata: it
never reaches the prompt and never credits coverage.

## `POST /sessions`

Response `201`: `{"id": "sess_...", "status": "collecting_evidence", "created_at": "..."}`

## `POST /sessions/{id}/media`

Form fields:

- `file`: image bytes (≤ `MAX_UPLOAD_MB`)
- `kind`: `photo` | `video` | `frame`
- `source`: `captured` | `imported`
- `component_hint` (optional): the view the client was **asked** for — a
  checklist slot (`front_exterior`, `tire`, …), a gate target (`model_family`,
  `mileage_km`, `axle_config`, …) or a contract view (`chassis`, …)
- `capture_origin`: `camera` | `gallery` | `unknown` — reported origin, not
  authenticity proof

Response `201`:

```json
{
  "id": "media_...",
  "session_id": "sess_...",
  "kind": "photo",
  "accepted": true,
  "reject_reason": null,
  "quality_notes": [],
  "vision_status": "ok",
  "requested_view": "front_exterior",
  "observed_views": [{"view": "tire", "visibility": "visible", "usable": true, "limitation": null}],
  "capture_origin": "gallery",
  "requested_view_satisfied": false
}
```

- `vision_status: "failed"` with a `vision_analysis_failed:<timeout|transport|schema|retry_limit|rate_limited>`
  note means no evidence was written. Re-uploading the **same bytes** re-analyses
  the same media row (at most `MAX_ANALYSIS_ATTEMPTS_PER_MEDIA` model calls).
- Exact bytes that were already rejected return the existing rejected row.
- A near-duplicate of an analysed photo is rejected (`duplicate_of_existing_photo`)
  and adds no support.
- `not_a_tractor_unit:other_vehicle` / `no_vehicle_detected`: nothing from that
  image enters the truck's evidence.
- `429` once a session has `MAX_MEDIA_PER_SESSION` uploads.

## `GET /sessions/{id}`

```json
{
  "id": "sess_...",
  "status": "collecting_evidence",
  "coverage": {"front_exterior": "captured", "tire": "missing", "dashboard_odometer": "attention"},
  "evidence": [
    {
      "field": "model_family",
      "status": "conflicting",
      "value": null,
      "canonical_value": null,
      "participants": [
        {"provenance": "seller_declared", "raw_value": "F-MAX", "canonical_value": "f-max", "display_value": "F-MAX", "media_id": null, "state": "active", "state_reason": null},
        {"provenance": "inferred_candidate", "raw_value": "R-series", "canonical_value": "r-series", "display_value": "R-series", "media_id": "media_...", "state": "active", "state_reason": null}
      ],
      "superseded": [],
      "seller_declared": "F-MAX", "observed_from_photo": null, "inferred_candidates": ["R-series"], "supporting_media_id": null
    }
  ],
  "next_photo": {"requested_view": "A close, straight-on photo of the model badge…", "reason": "…", "resolves": "model_family"},
  "gate_status": "needs_evidence",
  "gate_reasons": ["The model family is contradicted between sources (…). Nothing is chosen automatically."],
  "evidence_revision": 7,
  "findings": [],
  "concerns": [{"id": "conc_...", "component": "chassis", "severity": "structural", "description": "…", "status": "open"}],
  "limitations": ["heavy glare over the odometer digits"],
  "latest_appraisal_id": null
}
```

- `coverage`: `captured` only from an admitted visible **and** usable observed
  view; `attention` = partial (e.g. odometer crop only) or flagged.
- Field `status`: a provenance name (one source), `sources_agree` (independent
  sources agree — corroboration, not verification), `conflicting`, or `unknown`.
  No numeric tolerance. History states: `superseded`, `disputed`,
  `legacy_unverified` (pre-contract visual evidence, demoted by migration).
- Readiness comes from `gate_status` only (`ready_to_price`, `needs_evidence`,
  `unsupported`, `inspection_required`). `next_photo` may be `null` while
  blocked (e.g. a declaration is needed) and non-null while ready (optional view).
- Concern status: `open` | `resolved` | `review_required` (structural concerns are
  never auto-resolved by a photo).

## `PATCH /sessions/{id}/details`

```json
{"field": "axle_config", "value": "4x2", "intent": "seller_declared"}
```

- `field` ∈ `make`, `model_family`, `year`, `mileage_km`, `axle_config`, `vat_basis`; anything else → `422`.
- `intent` ∈ `seller_declared` (default) | `user_corrected`. The **server**
  assigns provenance; a client cannot create `observed_from_photo`.
- Values are validated (4-digit year, whole-km mileage, registry-known family /
  make / configuration). A new declaration supersedes only the same source's
  earlier declaration; it never replaces a photo reading.
- Declared mileage and declared model family are saved but do not unlock `priced`.

Response `200`: the tracked evidence list.

## `POST /sessions/{id}/appraisals`

```json
{
  "id": "appr_...", "session_id": "sess_...", "version": 2,
  "status": "priced",
  "price_low": 2200000, "price_mid": 2500000, "price_high": 2840909, "currency": "TRY",
  "comparable_count": 10, "distinct_vehicle_groups": 10,
  "comparables": [{"listing_id": "lst_...", "similarity_weight": 0.82, "make": "FORD", "model": "F-MAX", "year": 2021, "mileage_km": 365452, "axle_config": "4x2", "price": 2350000, "currency": "TRY", "source_url": "https://…"}],
  "matched_attributes": {"make": "ford", "model_family": "f-max", "axle_config": "4x2", "year": "2021", "mileage_km": "365000", "vat_basis": "unknown", "country": "TR", "currency": "TRY"},
  "findings": [],
  "reasons": ["Assumption: assuming seller-declared 4x2 (2 axles visible)"],
  "next_photo": null,
  "evidence_revision": 8,
  "method_note": "Provisional range of comparable asking prices: … not a calibrated coverage interval …",
  "tax_note": "source asking prices; VAT treatment not stated"
}
```

`status`: `priced` | `needs_evidence` | `unsupported` | `insufficient_market_data` | `inspection_required`.

`priced` requires the gate (audit section E) plus ≥5 **distinct vehicle groups**
after repost deduplication, with hard filters TR / TRY / tractor unit / make /
family / configuration / VAT basis, listings ≤180 days old, and year / log-mileage
distance caps that are never widened. Both bounds must be finite and ordered. The
range is a provisional heuristic over asking prices — no calibrated coverage, tax
normalisation, deductions or transaction promise. `inspection_required` returns
comparables as market context with no price. `insufficient_market_data` requests
no photo. If `evidence_revision` differs from the session's, the result is stale.
