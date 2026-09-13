import math

import pytest

from app.models import Listing
from app.services.pricing import (
    InsufficientCalibration,
    conformal_quantile,
    deduplicate_by_vehicle,
    price_from_comparables,
    weighted_median,
)


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
    residuals = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.45, 0.5, 0.6, 0.7]
    wide = conformal_quantile(residuals, alpha=0.5)
    narrow = conformal_quantile(residuals, alpha=0.1)
    assert narrow >= wide


def test_conformal_quantile_refuses_a_single_residual():
    """rank ceil(2 * 0.9) = 2 > n = 1: a finite 90% interval is unsupported."""
    with pytest.raises(InsufficientCalibration):
        conformal_quantile([0.2], alpha=0.1)


def test_conformal_quantile_refuses_instead_of_clamping_to_the_maximum():
    """Five residuals at 90% need rank 6. The old code clamped to the max and
    reported a ~83% interval as if it were 90%."""
    with pytest.raises(InsufficientCalibration):
        conformal_quantile([0.1, 0.2, 0.3, 0.4, 0.5], alpha=0.1)


def test_conformal_quantile_uses_the_finite_sample_rank():
    residuals = [i / 10 for i in range(1, 10)]  # n = 9, rank ceil(10 * 0.9) = 9
    assert conformal_quantile(residuals, alpha=0.1) == pytest.approx(0.9)


def _listing(price, year=2021, mileage=350_000, make="FORD", url="u"):
    return Listing(
        source_url=f"https://example.test/{url}", location="TR", country="TR", make=make, model="F-MAX",
        model_family="f-max", category="tractor_unit", year=year, mileage_km=mileage, axle_config="4x2",
        price=price, currency="TRY", vat_basis="unknown", partition="dev",
    )


def test_priced_result_requires_finite_ordered_bounds_and_distinct_groups():
    scored = [(_listing(3_000_000 + i * 100_000, mileage=300_000 + i * 20_000, url=str(i)), 0.1 * i) for i in range(6)]
    result = price_from_comparables(scored, min_distinct_groups=5)
    assert result.status == "priced"
    assert all(math.isfinite(v) for v in (result.price_low, result.price_mid, result.price_high))
    assert result.price_low <= result.price_mid <= result.price_high

    assert price_from_comparables(scored[:4], min_distinct_groups=5).status == "insufficient_market_data"


def test_reposts_collapse_to_one_vehicle_group():
    same = [(_listing(3_100_000, url=f"repost-{i}"), 0.2 + i * 0.01) for i in range(5)]
    distinct = deduplicate_by_vehicle(same)
    assert len(distinct) == 1
    assert distinct[0][1] == 0.2
