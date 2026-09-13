"""Comparable-based pricing: hard filtering, a similarity-weighted median,
and a conformal-style range. See docs/plan.md section 2 ("Mathematical
pricing") for the derivation — this module is a direct implementation of it.

No monetary deductions are invented here anywhere: condition findings are
reported alongside the range, never subtracted from it.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contract import (
    MAX_COMPARABLE_LOG_MILEAGE_DISTANCE,
    MAX_COMPARABLE_YEAR_DISTANCE,
    MAX_LISTING_AGE_DAYS,
    SUPPORTED_CURRENCY,
)
from ..models import Listing
from .canonical import canonicalize


@dataclass
class VehicleSpec:
    category: str
    model_family: str
    axle_config: str
    year: int
    mileage_km: int
    country: str = "TR"
    vat_basis: str = "vat_excluded"
    #: Canonical make. When known it is a HARD filter — an F-MAX population
    #: must never be used to price a Scania that happens to share a
    #: configuration.
    make_canonical: str | None = None


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
    """Hard filters, then similarity. None of these trade off against the
    distance score — an incompatible listing can never outvote a
    compatible-but-distant one.

    Currency and make were missing and are now explicit. The all-TRY
    snapshot masked the currency gap: the moment a EUR listing arrives,
    the old query would have mixed currencies into one median. Freshness
    and maximum feature distance are fixed from development data here and
    must NOT be widened per query to reach the minimum count — doing that
    turns "no defensible answer" into a confident wrong one.
    """
    stmt = select(Listing).where(
        Listing.category == spec.category,
        Listing.model_family == spec.model_family,
        Listing.axle_config == spec.axle_config,
        Listing.country == spec.country,
        Listing.currency == SUPPORTED_CURRENCY,
        Listing.vat_basis == spec.vat_basis,
        Listing.partition.in_(partitions),
    )
    candidates = [row for row in db.scalars(stmt).all() if row.id not in exclude_listing_ids]

    if spec.make_canonical:
        candidates = [
            row for row in candidates if canonicalize("make", row.make).canonical == spec.make_canonical
        ]

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_LISTING_AGE_DAYS)
    fresh: list[Listing] = []
    for row in candidates:
        collected = row.collected_at
        if collected is None:
            continue
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        if collected >= cutoff:
            fresh.append(row)

    scored = [
        (row, distance)
        for row, distance in ((c, _feature_distance(spec, c)) for c in fresh)
        if abs(spec.year - row.year) <= MAX_COMPARABLE_YEAR_DISTANCE
        and abs(math.log1p(spec.mileage_km) - math.log1p(row.mileage_km)) <= MAX_COMPARABLE_LOG_MILEAGE_DISTANCE
    ]
    scored.sort(key=lambda pair: pair[1])
    return scored[:max_comparables]


def vehicle_group_key(listing: Listing) -> tuple:
    """Identity of the underlying VEHICLE, not of the listing row.

    Reposts and re-listings of one truck share this key, so five rows from
    one dealer advertising the same vehicle cannot masquerade as five
    independent market observations. A unique URL proves nothing here.
    """
    return (
        canonicalize("make", listing.make).canonical or listing.make.strip().lower(),
        (listing.model_family or "").strip().lower(),
        listing.year,
        round(listing.mileage_km, -3),
        round(listing.price, -3),
    )


def distinct_vehicle_groups(listings: list[Listing]) -> int:
    return len({vehicle_group_key(listing) for listing in listings})


def deduplicate_by_vehicle(scored: list[tuple[Listing, float]]) -> list[tuple[Listing, float]]:
    """Keep the closest listing per distinct vehicle group."""
    best: dict[tuple, tuple[Listing, float]] = {}
    for listing, distance in scored:
        key = vehicle_group_key(listing)
        if key not in best or distance < best[key][1]:
            best[key] = (listing, distance)
    return sorted(best.values(), key=lambda pair: pair[1])


class InsufficientCalibration(ValueError):
    """Too few residuals to support the requested coverage level."""


def conformal_quantile(residuals: list[float], alpha: float) -> float:
    """Finite-sample split-conformal quantile of |log(actual) - log(predicted)|.

    https://arxiv.org/abs/2107.07511 — marginal, not individual-truck,
    coverage, and only when residuals come from a genuinely held-out
    partition.

    The required rank is ceil((n+1)(1-alpha)). When that exceeds n the
    sample cannot support the level at all: with 5 residuals, 90% coverage
    needs the 5.4th smallest, which does not exist. The previous code
    clamped the rank to n and returned the maximum residual, which quietly
    reports a ~83% interval as if it were the requested 90%. Refusing is
    the honest answer — the caller then says "insufficient calibration"
    rather than overstating its guarantee.
    """
    if not residuals:
        raise InsufficientCalibration("no residuals")
    n = len(residuals)
    rank = math.ceil((n + 1) * (1 - alpha))
    if rank > n:
        raise InsufficientCalibration(
            f"{n} residual(s) cannot support {(1 - alpha) * 100:.0f}% coverage "
            f"(needs rank {rank}); report insufficient calibration instead of clamping"
        )
    return sorted(residuals)[rank - 1]


def estimate_quantile_from_comparables(scored: list[tuple[Listing, float]], alpha: float) -> float | None:
    """Fallback used until data_pipeline/evaluate.py has produced a real
    held-out calibration quantile: leave-one-out residuals computed from the
    comparables found for *this* request. This is weaker than genuine
    held-out calibration (same small pool predicts and is predicted) —
    callers must label ranges built this way as provisional."""
    if len(scored) < 4:
        return None
    # Leave-one-out over n comparables yields n residuals; if that cannot
    # support the level, say so rather than returning a narrower interval
    # dressed up as the requested one.
    residuals = []
    for i, (listing, _) in enumerate(scored):
        others = scored[:i] + scored[i + 1 :]
        weights = [1.0 / (1.0 + d) for _, d in others]
        prices = [l.price for l, _ in others]
        pred = weighted_median(prices, weights)
        residuals.append(abs(math.log(listing.price) - math.log(pred)))
    try:
        return conformal_quantile(residuals, alpha)
    except InsufficientCalibration:
        # Not enough support for this coverage level. The caller shows a
        # point estimate with no range rather than a narrower band
        # mislabelled as the requested one.
        return None


def provisional_log_spread(scored: list[tuple[Listing, float]]) -> float | None:
    """Heuristic spread for the provisional range: the LARGEST leave-one-out
    log error among the comparables themselves.

    This is deliberately not called a quantile and makes no coverage claim.
    Each comparable is predicted from the others by the same weighted median
    the estimate uses; the widest miss bounds how far these asking prices
    disagree with each other. The same small pool predicts and is predicted,
    so it says nothing about how often a real sale falls inside it.
    """
    if len(scored) < 2:
        return None
    worst = 0.0
    for i, (listing, _) in enumerate(scored):
        others = scored[:i] + scored[i + 1 :]
        weights = [1.0 / (1.0 + d) for _, d in others]
        prices = [row.price for row, _ in others]
        predicted = weighted_median(prices, weights)
        if predicted <= 0 or listing.price <= 0:
            return None
        worst = max(worst, abs(math.log(listing.price) - math.log(predicted)))
    return worst


METHOD_NOTE = (
    "Provisional range of comparable asking prices: a similarity-weighted median of "
    "the matched listings, widened by the largest leave-one-out error among them. "
    "A heuristic spread, not a calibrated coverage interval; no condition deductions, "
    "tax normalisation or transaction-price promise."
)


def price_from_comparables(
    scored: list[tuple[Listing, float]],
    min_distinct_groups: int,
) -> PricingResult:
    """Price from already deduplicated comparables.

    `scored` must contain one row per distinct vehicle group — the caller
    deduplicates first, so the count checked here is independent vehicles,
    not listing rows. `priced` requires BOTH finite, ordered bounds; a point
    estimate with no range is not a priced result.
    """
    if len(scored) < min_distinct_groups:
        return PricingResult(
            status="insufficient_market_data",
            price_mid=None,
            price_low=None,
            price_high=None,
            comparables=[],
            reasons=[
                f"Only {len(scored)} distinct compatible Turkish vehicle(s) found after removing reposts; "
                f"{min_distinct_groups} are required. Search limits are not widened to reach that number."
            ],
        )

    weights = [1.0 / (1.0 + d) for _, d in scored]
    prices = [listing.price for listing, _ in scored]
    price_mid = weighted_median(prices, weights)
    spread = provisional_log_spread(scored)
    comparables = [Comparable(listing=listing, weight=w) for (listing, _d), w in zip(scored, weights)]

    if spread is None:
        return PricingResult(
            status="insufficient_market_data",
            price_mid=None,
            price_low=None,
            price_high=None,
            comparables=comparables,
            reasons=["The comparable prices could not support a finite range, so no price is shown."],
        )

    price_low = price_mid * math.exp(-spread)
    price_high = price_mid * math.exp(spread)
    if not (math.isfinite(price_low) and math.isfinite(price_high) and price_low <= price_mid <= price_high):
        return PricingResult(
            status="insufficient_market_data",
            price_mid=None,
            price_low=None,
            price_high=None,
            comparables=comparables,
            reasons=["The comparable prices could not support a finite range, so no price is shown."],
        )

    return PricingResult(
        status="priced",
        price_mid=price_mid,
        price_low=price_low,
        price_high=price_high,
        comparables=comparables,
        reasons=[],
    )
