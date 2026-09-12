# Evaluation — not yet built

This is where the held-out evaluation from `docs/plan.md` section 5 goes
once `data_pipeline/` is producing real volume:

- **`evaluate.py`** (to build): run the pricing engine
  (`backend/app/services/pricing.py`) against the `test` partition,
  measuring asking-price error, interval coverage/width, acceptance rate,
  and wrong-object acceptance — see the "Required tests" list in
  `docs/plan.md` section 5.
- **Calibration:** compute the real held-out conformal quantile from the
  `calibration` partition's residuals (using
  `backend/app/services/pricing.conformal_quantile()`, which already
  exists and is tested) and feed it back into the backend, replacing the
  in-request provisional fallback in
  `pricing.estimate_quantile_from_comparables()`.
- **CatBoost comparison** (optional per the plan): only worth building if
  the weighted-median baseline is in place and measured first — promote it
  only if it beats the baseline on the test partition.

Blocked on `data_pipeline/` producing enough listings to split into
dev/calibration/test (docs/plan.md targets 200-400 total). Don't build a
calibration/evaluation script against fewer than a few dozen test-partition
vehicles — the numbers it reports wouldn't mean anything yet.
