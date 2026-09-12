"""Comparable-based pricing: hard filtering, a similarity-weighted median,
and a conformal-style range. See docs/plan.md section 2 ("Mathematical
pricing") for the derivation — this module is a direct implementation of it.

No monetary deductions are invented here anywhere: condition findings are
reported alongside the range, never subtracted from it.
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Listing


@dataclass
class VehicleSpec:
    category: str
    model_family: str
    axle_config: str
    year: int
    mileage_km: int
    country: str = "TR"
    vat_basis: str = "vat_excluded"


@dataclass
class Comparable:
    listing: Listing
    weight: float


@dataclass
class PricingResult:
    status: str  # "priced" | "insufficient_market_data"
    price_mid: float | None
    price_low: float | None
    price_high: float | None
    comparables: list[Comparable]
    reasons: list[str]


def _feature_distance(spec: VehicleSpec, listing: Listing, age_scale: float = 3.0, log_mileage_scale: float = 0.5) -> float:
    """Normalized age + log-mileage distance. Scales are engineering defaults
    for the hackathon, not fit to data yet — see docs/plan.md's note that
    "scaling is learned from development data" as a follow-up."""
    age_diff = abs(spec.year - listing.year) / age_scale
    log_mileage_diff = abs(math.log1p(spec.mileage_km) - math.log1p(listing.mileage_km)) / log_mileage_scale
    return math.sqrt(age_diff**2 + log_mileage_diff**2)


def weighted_median(values: list[float], weights: list[float]) -> float:
    if not values:
        raise ValueError("weighted_median() needs at least one value")
    pairs = sorted(zip(values, weights), key=lambda p: p[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        ordered = sorted(values)
        n = len(ordered)
        return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2

    cumulative = 0.0
    half = total / 2
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= half:
            return value
    return pairs[-1][0]


def find_comparables(
    db: Session,
    spec: VehicleSpec,
    partitions: tuple[str, ...] = ("dev", "calibration"),
    max_comparables: int = 10,
    exclude_listing_ids: frozenset[str] = frozenset(),
) -> list[tuple[Listing, float]]:
    """Hard-filter by category/model_family/axle_config/country/vat_basis —
    these never trade off against similarity score, so an incompatible
    listing can never outvote a compatible-but-distant one."""
    stmt = select(Listing).where(
        Listing.category == spec.category,
        Listing.model_family == spec.model_family,
        Listing.axle_config == spec.axle_config,
        Listing.country == spec.country,
        Listing.vat_basis == spec.vat_basis,
        Listing.partition.in_(partitions),
    )
    candidates = [row for row in db.scalars(stmt).all() if row.id not in exclude_listing_ids]
    scored = sorted(((c, _feature_distance(spec, c)) for c in candidates), key=lambda p: p[1])
    return scored[:max_comparables]


def conformal_quantile(residuals: list[float], alpha: float) -> float:
    """Finite-sample split-conformal quantile of |log(actual) - log(predicted)|
    residuals, for a nominal (1 - alpha) interval.
    https://arxiv.org/abs/2107.07511 — gives marginal, not individual-truck,
    coverage, and only once residuals come from a genuine held-out partition."""
    if not residuals:
        raise ValueError("conformal_quantile() needs at least one residual")
    n = len(residuals)
    rank = min(math.ceil((n + 1) * (1 - alpha)), n)
    return sorted(residuals)[rank - 1]


def estimate_quantile_from_comparables(scored: list[tuple[Listing, float]], alpha: float) -> float | None:
    """Fallback used until data_pipeline/evaluate.py has produced a real
    held-out calibration quantile: leave-one-out residuals computed from the
    comparables found for *this* request. This is weaker than genuine
    held-out calibration (same small pool predicts and is predicted) —
    callers must label ranges built this way as provisional."""
    if len(scored) < 4:
        return None
    residuals = []
    for i, (listing, _) in enumerate(scored):
        others = scored[:i] + scored[i + 1 :]
        weights = [1.0 / (1.0 + d) for _, d in others]
        prices = [l.price for l, _ in others]
        pred = weighted_median(prices, weights)
        residuals.append(abs(math.log(listing.price) - math.log(pred)))
    return conformal_quantile(residuals, alpha)


def price_from_comparables(
    scored: list[tuple[Listing, float]],
    min_comparables: int,
    quantile: float | None,
    quantile_is_provisional: bool,
) -> PricingResult:
    if len(scored) < min_comparables:
        return PricingResult(
            status="insufficient_market_data",
            price_mid=None,
            price_low=None,
            price_high=None,
            comparables=[],
            reasons=[
                f"Only {len(scored)} compatible Turkish comparable(s) found; "
                f"{min_comparables} required for a defensible range."
            ],
        )

    weights = [1.0 / (1.0 + d) for _, d in scored]
    prices = [listing.price for listing, _ in scored]
    price_mid = weighted_median(prices, weights)

    reasons: list[str] = []
    if quantile is None:
        price_low = price_high = None
        reasons.append(
            "Not enough comparables to estimate a range yet — showing a point "
            "estimate only. No range should be shown as if it were calibrated."
        )
    else:
        price_low = price_mid * math.exp(-quantile)
        price_high = price_mid * math.exp(quantile)
        if quantile_is_provisional:
            reasons.append(
                "Range is PROVISIONAL: calibrated from this response's own comparables, "
                "not a held-out calibration partition. Replace with "
                "data_pipeline/evaluate.py's measured quantile before the judged demo."
            )

    comparables = [Comparable(listing=listing, weight=w) for (listing, _d), w in zip(scored, weights)]

    return PricingResult(
        status="priced",
        price_mid=price_mid,
        price_low=price_low,
        price_high=price_high,
        comparables=comparables,
        reasons=reasons,
    )
