# Visual evaluation (human-labeled)

Measures the production vision contract — the same prompt, JSON schema,
parser and admission rules the app runs — against **labels written by a
person**. Nothing here has been run on real images yet: all 40 slots in
`manifest.json` are `pending`. Until they are labeled, no visual accuracy
claim can be made, and the real-image gate stays an open demo blocker.

## What is needed

1. **40 real, permissioned images**, in the scenario buckets already laid out
   in `manifest.json` (front 4, rear 2, side 4, tire 4, dashboard 3,
   odometer 6, cab 2, chassis 3, badge 3, dark/blurry 3, partial truck 2,
   wrong vehicle/irrelevant 4). Include F-MAX and Actros/Scania, unreadable
   or tiny text, a misleading decal, and visible two- and three-axle cases.
   Put files under `evaluation/visual/images/` (gitignored).
2. For each row: `path`, `sha256` (`sha256sum`), `permission.source` and
   `permission.granted_by`, a `vehicle_group` (crops/retakes of one truck
   share it), and a `split` of `dev` or `holdout` — about 20/20 by vehicle.
3. A **human** label per image, following `label_template`. Label every view
   for every image. "unknown"/not readable is a valid label. Set
   `status: "labeled"`; a second person adjudicating a disputed critical
   reading sets `adjudicator` and `status: "adjudicated"`. Claude or any
   model output must never be copied into a label — the validator rejects
   model-named labelers, but it cannot detect copying, so that rule is on
   the labelers.
4. `prices.json` (copy `prices.example.json`) with current per-model USD
   prices per million tokens. The run refuses without it, because the cost
   ceiling cannot otherwise be enforced.

## Commands

From `backend/` (so `backend/.env` supplies the key; nothing prints it):

```bash
.venv/bin/python ../evaluation/visual/evaluate_vision.py validate
.venv/bin/python ../evaluation/visual/evaluate_vision.py plan --split dev --models claude-sonnet-5,claude-opus-5
.venv/bin/python ../evaluation/visual/evaluate_vision.py run --split dev \
    --models claude-sonnet-5,claude-opus-5 --prices ../evaluation/visual/prices.json \
    --max-calls 60 --max-cost-usd 5 --confirm-paid
# after freezing the winner on dev only:
.venv/bin/python ../evaluation/visual/evaluate_vision.py run --split holdout \
    --models <winner> --prices ../evaluation/visual/prices.json --max-calls 20 --max-cost-usd 5 --confirm-paid
.venv/bin/python -m pytest ../evaluation/visual/test_scoring.py -q   # scorer tests, no API
```

`run` refuses unless every row in the split is labeled, permissioned, hashed
and group-consistent. Every real request counts toward `--max-calls`
(SDK retries are disabled; the structured-output fallback is counted), and
the run stops at the first call/cost limit or access failure — no
indefinite retries, no silent model swap. Results go to
`evaluation/visual/runs/` (gitignored); `rescore` re-scores saved raw
proposals after a label is adjudicated, with no API calls.

## Metrics and pass criteria

Per model: strict-schema errors, call failures, per-view TP/FP/FN with
precision/recall, total-odometer exact/wrong/abstained/invented counts,
model-family correct/wrong/abstained/promoted counts, clear-case success
with its denominator, critical false admissions (invented or wrong
mileage, unsupported or wrong family, wrong-view coverage, false tractor,
forbidden claims), hallucinated claims, p50/p95 latency and cost.

Targets (not results): zero critical false admissions; view precision
≥95% and recall ≥80%; every admitted odometer exact in digits and unit;
≥80% usable results on clear supported identity/readout cases. A zero
denominator is reported as N/A and counts as **not passed**. Twenty holdout
images are a demo check, not a population guarantee. Choose prompt/model on
`dev` only; never tune on `holdout`.
