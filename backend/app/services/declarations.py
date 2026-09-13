"""Server-side validation of what a seller is allowed to declare.

The allowlist lives in `contract.DECLARABLE_FIELDS`; this module checks the
VALUES. Both halves matter: restricting the field stops a caller declaring
`vehicle_category` and handing themselves a tractor identity, and
validating the value stops "365000.0" or "about 400k" entering the
evidence ledger as a number the pricing engine will later trust.

Declarations are honest B-type evidence. They may fill year and
configuration for a conditional range, but they never satisfy a view and
never become `observed_from_photo`.
"""

from __future__ import annotations

from datetime import datetime

from .canonical import canonicalize

MIN_YEAR = 1990
MAX_MILEAGE_KM = 3_000_000


def validate_declared_value(field: str, value: str) -> str | None:
    """Return a human-readable problem, or None when the value is usable."""
    text = value.strip()
    if not text:
        return "value must not be empty"

    if field == "year":
        if not text.isdigit() or len(text) != 4:
            return "year must be a four-digit number"
        year = int(text)
        current = datetime.now().year
        if not MIN_YEAR <= year <= current + 1:
            return f"year must be between {MIN_YEAR} and {current + 1}"
        return None

    if field == "mileage_km":
        # Same strictness as an admitted reading: no separator guessing.
        cleaned = text.replace(" ", "")
        if not cleaned.isdigit():
            return "mileage must be whole kilometres, digits only (e.g. 365000)"
        km = int(cleaned)
        if not 0 < km <= MAX_MILEAGE_KM:
            return f"mileage must be between 1 and {MAX_MILEAGE_KM} km"
        return None

    if field in ("model_family", "make", "axle_config", "vat_basis"):
        resolved = canonicalize(field, text)
        if not resolved.resolved:
            return f"{field!r} value {text!r} is not recognised"
        return None

    # Unreachable while the route checks DECLARABLE_FIELDS first, but a
    # second gate here keeps this module safe to call from anywhere.
    return f"{field!r} cannot be declared"
