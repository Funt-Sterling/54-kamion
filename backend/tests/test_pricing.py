import math

from app.services.pricing import conformal_quantile, weighted_median


def test_weighted_median_picks_high_weight_value():
    # A single dominant weight should pull the result to that value.
    values = [100.0, 200.0, 1_000_000.0]
    weights = [0.49, 0.49, 0.02]
    result = weighted_median(values, weights)
    assert result in (100.0, 200.0)


def test_weighted_median_matches_plain_median_when_equal_weights():
    values = [10.0, 20.0, 30.0]
    weights = [1.0, 1.0, 1.0]
    assert weighted_median(values, weights) == 20.0


def test_weighted_median_handles_zero_total_weight():
    values = [10.0, 20.0, 30.0]
    weights = [0.0, 0.0, 0.0]
    assert weighted_median(values, weights) == 20.0


def test_conformal_quantile_is_monotonic_in_alpha():
    residuals = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4]
    wide = conformal_quantile(residuals, alpha=0.5)
    narrow = conformal_quantile(residuals, alpha=0.05)
    assert narrow >= wide


def test_conformal_quantile_single_residual():
    assert conformal_quantile([0.2], alpha=0.1) == 0.2


def test_conformal_quantile_never_exceeds_max_residual():
    residuals = [0.1, 0.2, 0.3]
    q = conformal_quantile(residuals, alpha=0.01)
    assert q <= max(residuals)
    assert not math.isnan(q)
