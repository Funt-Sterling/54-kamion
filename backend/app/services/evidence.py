"""Evidence model with provenance (Milestone 1).

Every pricing-relevant fact about the vehicle is stored as an immutable,
provenanced `EvidenceRecord` — never overwritten. This module decides, at
read time, what a field's facts actually mean: unknown (nothing recorded),
a single source's value, multiple sources that agree ("confirmed"), or
multiple sources that disagree ("conflicting"). Conflicts are surfaced, not
silently resolved — see `resolve_field()`.

Example the gate relies on: a seller declares 250,000 km but a dashboard
photo reads 650,000 km. Both facts get stored; `resolve_field("mileage_km",
...)` returns status="conflicting" with both values still visible, and
`services.gate` turns that into a needs_evidence response instead of
guessing which one is right.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvidenceRecord

SELLER_DECLARED = "seller_declared"
OBSERVED_FROM_PHOTO = "observed_from_photo"
INFERRED_CANDIDATE = "inferred_candidate"

STATUS_UNKNOWN = "unknown"
STATUS_CONFLICTING = "conflicting"
STATUS_CONFIRMED = "confirmed"
# STATUS_UNKNOWN/CONFLICTING/CONFIRMED are the only *derived* statuses; when
# exactly one provenance has a value, resolve_field() reports that
# provenance's own name as the status (e.g. "seller_declared").

# Relative-difference tolerance before two numeric values count as disagreeing.
# 0.0 means "must match exactly". Fields not listed fall back to
# case-insensitive exact string matching.
NUMERIC_TOLERANCE = {
    "mileage_km": 0.15,
    "year": 0.0,
}


@dataclass
class FieldEvidence:
    field: str
    status: str
    value: str | None
    seller_declared: str | None
    observed_from_photo: str | None
    inferred_candidates: list[str]
    supporting_media_id: str | None


def values_agree(field: str, a: str, b: str) -> bool:
    """Whether two recorded values for `field` should be treated as the same
    fact rather than a conflict. Numeric fields get a relative tolerance
    (mileage readings/roundings drift a little); everything else requires
    an exact case-insensitive match."""
    if a == b:
        return True
    if field in NUMERIC_TOLERANCE:
        try:
            value_a, value_b = float(a), float(b)
        except ValueError:
            return a.strip().lower() == b.strip().lower()
        tolerance = NUMERIC_TOLERANCE[field]
        if tolerance <= 0:
            return value_a == value_b
        denominator = max(abs(value_a), abs(value_b), 1.0)
        return abs(value_a - value_b) / denominator <= tolerance
    return a.strip().lower() == b.strip().lower()


def resolve_field(field: str, records: list[EvidenceRecord]) -> FieldEvidence:
    seller_declared: str | None = None
    observed_from_photo: str | None = None
    observed_media_id: str | None = None
    inferred: dict[str, str | None] = {}

    for record in sorted(records, key=lambda r: r.created_at):
        if record.provenance == SELLER_DECLARED:
            seller_declared = record.value
        elif record.provenance == OBSERVED_FROM_PHOTO:
            observed_from_photo = record.value
            observed_media_id = record.media_id
        elif record.provenance == INFERRED_CANDIDATE:
            inferred.setdefault(record.value, record.media_id)

    inferred_candidates = list(inferred.keys())

    known: list[tuple[str, str, str | None]] = []
    if seller_declared is not None:
        known.append((SELLER_DECLARED, seller_declared, None))
    if observed_from_photo is not None:
        known.append((OBSERVED_FROM_PHOTO, observed_from_photo, observed_media_id))
    for value, media_id in inferred.items():
        known.append((INFERRED_CANDIDATE, value, media_id))

    if not known:
        return FieldEvidence(field, STATUS_UNKNOWN, None, None, None, [], None)

    if len(known) == 1:
        provenance, value, media_id = known[0]
        return FieldEvidence(field, provenance, value, seller_declared, observed_from_photo, inferred_candidates, media_id)

    values = [value for _, value, _ in known]
    all_agree = all(values_agree(field, values[0], other) for other in values[1:])

    if all_agree:
        # Multiple independent sources agreeing is stronger evidence than
        # any one of them — prefer photo evidence as the display value,
        # then the seller's own claim, then a vision guess.
        value = observed_from_photo or seller_declared or inferred_candidates[0]
        media_id = observed_media_id if observed_from_photo else None
        return FieldEvidence(field, STATUS_CONFIRMED, value, seller_declared, observed_from_photo, inferred_candidates, media_id)

    return FieldEvidence(
        field, STATUS_CONFLICTING, None, seller_declared, observed_from_photo, inferred_candidates, observed_media_id
    )


def get_field_evidence(db: Session, session_id: str, field: str) -> FieldEvidence:
    records = db.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.session_id == session_id,
            EvidenceRecord.field == field,
        )
    ).all()
    return resolve_field(field, records)
