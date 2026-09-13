"""Evidence resolution: what a field's accumulated facts actually mean.

Every pricing-relevant fact is stored as an immutable, provenanced
`EvidenceRecord`. Nothing is overwritten and nothing is deleted. This
module decides, at read time, whether the *active* records for a field
agree, disagree, or leave it unknown.

Three defects from the audit (section B.4) are fixed here:

1. **Only the newest observation survived.** Two contradicting photo
   readings silently collapsed to the newest one while every inferred
   candidate persisted forever — the system hid strong disagreement and
   preserved weak guesses. Now *every* active record participates, and
   two disagreeing observations are a conflict.
2. **A 15% relative mileage tolerance.** At 1,000,000 km that band is
   ±150,000 km, so 850,000 and 1,000,000 were called agreement. The
   tolerance is gone: mileage and year agree only on equal canonical
   readings.
3. **No alias handling.** `F Max` and `FMAX` were a conflict. Comparison
   now runs through the shared registry in `services.canonical`, which is
   the same one ingestion and market retrieval use.

Two vocabulary rules the audit insists on and this module enforces:

* Agreement between sources is reported as ``sources_agree`` — never
  "verified". A seller agreeing with a photo is corroboration, not
  independent verification of lifetime mileage.
* Repeated analyses of the *same* image are not independent confirmation,
  so independence is counted per (provenance, media) source, not per row.

Supersession (`supersede_weaker`) exists so a stronger same-vehicle
observation can retire a weaker candidate *without deleting history*: the
old row stays queryable with ``state="superseded"``, a pointer to what
replaced it, and a human-readable reason. Seller edits supersede earlier
seller declarations only — a declaration may never retire a visual record.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contract import (
    PROV_CANDIDATE,
    PROV_OBSERVED,
    PROV_SELLER,
    PROV_USER_CORRECTED,
    STATE_ACTIVE,
    STATE_SUPERSEDED,
    STATUS_AGREE_SOURCES,
    STATUS_CONFLICTING,
    STATUS_UNKNOWN,
)
from ..models import EvidenceRecord
from .canonical import canonicalize

# Re-exported for callers that predate contract.py.
SELLER_DECLARED = PROV_SELLER
OBSERVED_FROM_PHOTO = PROV_OBSERVED
INFERRED_CANDIDATE = PROV_CANDIDATE
USER_CORRECTED = PROV_USER_CORRECTED

#: Human claims about their own vehicle. Strong enough to declare a spec,
#: never strong enough to stand in for a photograph.
DECLARED_PROVENANCES = frozenset({PROV_SELLER, PROV_USER_CORRECTED})

#: Fields whose values are integers. They get whitespace and leading-zero
#: normalisation ONLY — no tolerance, and no guessing at ambiguous
#: separators (a separator that could be decimal or thousands is left
#: alone, so it reads as a discrepancy rather than a silent 10x error).
NUMERIC_FIELDS = frozenset({"mileage_km", "year", "visible_axle_count"})

_WHITESPACE = (" ", " ", " ", " ", "\t")


def _fold(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def comparison_key(field: str, raw: str | None) -> str:
    """The key two readings must share to count as the same fact.

    Canonicalisation is delegated to the shared registry so ingestion,
    resolution and market retrieval cannot drift apart. An unknown value
    keeps a folded form of its own text: two identical unknown readings
    still agree, but an unknown never merges into a known family.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    if field in NUMERIC_FIELDS:
        compact = text
        for space in _WHITESPACE:
            compact = compact.replace(space, "")
        if compact.isdigit():
            return str(int(compact))
        return _fold(text)

    resolved = canonicalize(field, text)
    if resolved.canonical is not None:
        return resolved.canonical
    return _fold(text)


def values_agree(field: str, a: str, b: str) -> bool:
    """Whether two recorded values for `field` are the same fact.

    Exact canonical equality. There is deliberately no numeric tolerance:
    a later legitimate odometer reading needs a timestamp and context, not
    a blanket percentage band that swallows most of a truck's life.
    """
    return comparison_key(field, a) == comparison_key(field, b)


@dataclass
class FieldEvidence:
    """Resolved view of one field.

    The first seven attributes are the shape the routers and the frontend
    already consume and are kept verbatim. The rest are additive: the API
    needs every participant in a conflict (the old payload showed only the
    seller/photo pair, so a candidate-vs-candidate conflict rendered as
    "Conflicting" with two dashes and no explanation).
    """

    field: str
    status: str
    value: str | None
    seller_declared: str | None
    observed_from_photo: str | None
    inferred_candidates: list[str]
    supporting_media_id: str | None

    #: Every ACTIVE record for this field, newest last.
    participants: list[dict] = dc_field(default_factory=list)
    #: Shared canonical key when the active records agree, else None.
    canonical_value: str | None = None
    #: Retained history: superseded and disputed records, never deleted.
    superseded: list[dict] = dc_field(default_factory=list)

    def observations(self) -> list[dict]:
        """Active participants backed by an actual photograph."""
        return [p for p in self.participants if p["provenance"] == PROV_OBSERVED]

    def declarations(self) -> list[dict]:
        return [p for p in self.participants if p["provenance"] in DECLARED_PROVENANCES]

    def candidates(self) -> list[dict]:
        return [p for p in self.participants if p["provenance"] == PROV_CANDIDATE]

    @property
    def has_observation(self) -> bool:
        return bool(self.observations())

    @property
    def is_conflicting(self) -> bool:
        return self.status == STATUS_CONFLICTING


def _state(record: EvidenceRecord) -> str:
    # Records built in memory (tests, pre-flush) have no column default yet.
    return getattr(record, "state", None) or STATE_ACTIVE


def _order_key(record: EvidenceRecord) -> tuple:
    """Chronological, with a stable tiebreak. Never raises on a row whose
    timestamp has not been assigned yet."""
    created = getattr(record, "created_at", None)
    if created is None:
        return (0, 0.0, str(getattr(record, "id", "") or ""))
    try:
        stamp = created.timestamp()
    except (AttributeError, OSError, ValueError, OverflowError):
        stamp = 0.0
    return (1, stamp, str(getattr(record, "id", "") or ""))


def _participant(record: EvidenceRecord) -> dict:
    """Exactly the shape of schemas.EvidenceParticipantOut."""
    return {
        "provenance": record.provenance,
        "raw_value": record.value,
        "canonical_value": record.canonical_value,
        "display_value": record.display_value or record.value,
        "media_id": record.media_id,
        "state": _state(record),
        "state_reason": record.state_reason,
    }


def _record_key(field: str, record: EvidenceRecord) -> str:
    """Prefer a canonical value already assigned at admission time."""
    stored = getattr(record, "canonical_value", None)
    return comparison_key(field, stored if stored else record.value)


def _source_key(record: EvidenceRecord) -> tuple[str, str]:
    """Independence unit. Two readings off the same image via the same
    provenance are one source, however many times it was analysed."""
    return (record.provenance, record.media_id or "")


def resolve_field(field: str, records: list[EvidenceRecord]) -> FieldEvidence:
    ordered = sorted(records, key=_order_key)
    active = [r for r in ordered if _state(r) == STATE_ACTIVE]
    history = [r for r in ordered if _state(r) != STATE_ACTIVE]

    participants = [_participant(r) for r in active]
    superseded = [_participant(r) for r in history]

    # Legacy scalars the routers and UI still read.
    declarations = [r for r in active if r.provenance in DECLARED_PROVENANCES]
    observations = [r for r in active if r.provenance == PROV_OBSERVED]
    candidates = [r for r in active if r.provenance == PROV_CANDIDATE]

    seller_declared = declarations[-1].value if declarations else None
    observed_from_photo = observations[-1].value if observations else None
    observed_media_id = observations[-1].media_id if observations else None
    inferred_candidates = list(dict.fromkeys(r.value for r in candidates))

    if not active:
        return FieldEvidence(
            field=field,
            status=STATUS_UNKNOWN,
            value=None,
            seller_declared=None,
            observed_from_photo=None,
            inferred_candidates=[],
            supporting_media_id=None,
            participants=[],
            canonical_value=None,
            superseded=superseded,
        )

    groups: dict[str, list[EvidenceRecord]] = {}
    for record in active:
        groups.setdefault(_record_key(field, record), []).append(record)

    if len(groups) > 1:
        # Every disagreeing source stays visible in `participants`; `value`
        # is deliberately null so nothing downstream can pick a winner.
        return FieldEvidence(
            field=field,
            status=STATUS_CONFLICTING,
            value=None,
            seller_declared=seller_declared,
            observed_from_photo=observed_from_photo,
            inferred_candidates=inferred_candidates,
            supporting_media_id=observed_media_id,
            participants=participants,
            canonical_value=None,
            superseded=superseded,
        )

    # One agreed value. Display the strongest source's raw reading: a photo
    # first, then the seller's own words, then a model guess.
    chosen = (observations or declarations or candidates)[-1]
    distinct_sources = {_source_key(r) for r in active}

    if len(distinct_sources) > 1:
        status = STATUS_AGREE_SOURCES
    else:
        status = chosen.provenance

    canonical = canonicalize(field, chosen.canonical_value or chosen.value).canonical

    return FieldEvidence(
        field=field,
        status=status,
        value=chosen.value,
        seller_declared=seller_declared,
        observed_from_photo=observed_from_photo,
        inferred_candidates=inferred_candidates,
        supporting_media_id=chosen.media_id,
        participants=participants,
        canonical_value=canonical,
        superseded=superseded,
    )


def get_field_evidence(db: Session, session_id: str, field: str) -> FieldEvidence:
    records = db.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.session_id == session_id,
            EvidenceRecord.field == field,
        )
    ).all()
    return resolve_field(field, list(records))


def active_records(db: Session, session_id: str, field: str) -> list[EvidenceRecord]:
    records = db.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.session_id == session_id,
            EvidenceRecord.field == field,
        )
    ).all()
    return sorted((r for r in records if _state(r) == STATE_ACTIVE), key=_order_key)


# --- Supersession ------------------------------------------------------------


def mark_superseded(record: EvidenceRecord, *, superseded_by: EvidenceRecord, reason: str) -> EvidenceRecord:
    """Retire one record in favour of another. History is preserved: the
    row keeps its value, provenance and media, and gains a pointer to what
    replaced it plus the reason it was retired."""
    record.state = STATE_SUPERSEDED
    record.superseded_by_id = superseded_by.id
    record.state_reason = reason
    return record


def _supersedes(new: EvidenceRecord, old: EvidenceRecord) -> str | None:
    """The complete supersession policy. Returns a reason, or None when the
    older record must stand.

    What is deliberately impossible here:

    * A declaration never retires a photograph. A seller who edits their
      own mileage supersedes their earlier declaration and leaves the
      dashboard reading exactly where it was — so the conflict survives.
    * An observation never retires another observation from a *different*
      image. Two photos that disagree are a real contradiction and must
      reach the gate as one.
    """
    if old is new or old.id is None or old.id == new.id:
        return None

    same_image = bool(new.media_id) and new.media_id == old.media_id

    if new.provenance == PROV_OBSERVED:
        if old.provenance == PROV_CANDIDATE:
            return "superseded by a photo-supported observation of the same vehicle"
        if old.provenance == PROV_OBSERVED and same_image:
            # Re-analysing one image is not a second opinion.
            return "superseded by a later analysis of the same image"
        return None

    if new.provenance in DECLARED_PROVENANCES:
        # A seller edit retires the seller's own earlier claim; a user
        # correction retires an earlier correction. Neither retires the other.
        if old.provenance == new.provenance:
            return "superseded by a later declaration from the same source"
        return None

    if new.provenance == PROV_CANDIDATE and old.provenance == PROV_CANDIDATE and same_image:
        return "superseded by a later analysis of the same image"

    return None


def supersede_weaker(
    db: Session,
    session_id: str,
    field: str,
    new_record: EvidenceRecord,
    *,
    commit: bool = False,
) -> list[EvidenceRecord]:
    """Apply the supersession policy after `new_record` is recorded.

    Call this from the write path (media analysis, the details endpoint)
    once the new record has an id. Returns the rows that changed state so
    the caller can bump the session's evidence revision.
    """
    changed: list[EvidenceRecord] = []
    for old in active_records(db, session_id, field):
        reason = _supersedes(new_record, old)
        if reason is None:
            continue
        changed.append(mark_superseded(old, superseded_by=new_record, reason=reason))

    if changed:
        db.add_all(changed)
        if commit:
            db.commit()
    return changed
