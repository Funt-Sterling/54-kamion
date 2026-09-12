"""Deterministic evidence gate (Milestone 2) and next-best-photo priority
function (Milestone 5).

Priority order, fixed and rule-based — no probabilistic information gain:
  1. pricing-blocking identity/specification (model family, axle config, year)
  2. mileage
  3. suspected serious structural damage
  4. an unresolved visible defect (e.g. a tire concern)
  5. missing required component coverage

Tiers 1-3 block pricing outright (`needs_evidence` / `inspection_required` /
`unsupported`). Tiers 4-5 never block pricing — they only drive the
"what photo would help next" suggestion so a report can keep improving
after a price has already been shown.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..constants import REQUIRED_COMPONENTS
from .evidence import FieldEvidence, get_field_evidence
from .session_state import COMPONENT_LABELS, coverage_only

IDENTITY_FIELDS = ["model_family", "axle_config", "year"]
MILEAGE_FIELD = "mileage_km"

STATUS_UNSUPPORTED = "unsupported"
STATUS_NEEDS_EVIDENCE = "needs_evidence"
STATUS_INSPECTION_REQUIRED = "inspection_required"
STATUS_READY = "ready_to_price"

_IDENTITY_VIEW = {
    "model_family": "A clear photo of the model badge or manufacturer spec plate",
    "axle_config": "A full side profile photo showing all axles/wheels",
    "year": "A clear photo of the model badge, registration plate, or spec plate showing the model year",
}
_IDENTITY_PURPOSE = {
    "model_family": "Model family selects which comparable listings are valid.",
    "axle_config": "Axle configuration (e.g. 4x2 vs 6x4) is required to match comparable listings.",
    "year": "Model year is required to match comparable listings by age.",
}


@dataclass
class NextPhotoRequest:
    requested_view: str
    reason: str
    resolves: str  # the field or component name this photo would resolve


@dataclass
class GateDecision:
    status: str
    reasons: list[str]
    next_photo: NextPhotoRequest | None
    identity: dict[str, FieldEvidence]
    mileage: FieldEvidence


def _has_confirmed_tractor_unit(db: Session, session_id: str) -> bool:
    return (
        db.scalars(
            select(models.MediaItem.id).where(
                models.MediaItem.session_id == session_id,
                models.MediaItem.vision_status == "ok",
                models.MediaItem.accepted.is_(True),
            )
        ).first()
        is not None
    )


def _rejected_non_truck_category(db: Session, session_id: str) -> str | None:
    row = db.scalars(
        select(models.MediaItem).where(
            models.MediaItem.session_id == session_id,
            models.MediaItem.reject_reason.isnot(None),
            models.MediaItem.reject_reason.like("not_a_tractor_unit:%"),
        )
    ).first()
    if row is None or row.reject_reason is None:
        return None
    return row.reject_reason.split(":", 1)[1]


def _structural_damage_media(db: Session, session_id: str) -> models.MediaItem | None:
    return db.scalars(
        select(models.MediaItem).where(
            models.MediaItem.session_id == session_id,
            models.MediaItem.vision_status == "ok",
            models.MediaItem.structural_damage_suspected.is_(True),
        )
    ).first()


def _tire_concern_media(db: Session, session_id: str) -> models.MediaItem | None:
    return db.scalars(
        select(models.MediaItem).where(
            models.MediaItem.session_id == session_id,
            models.MediaItem.vision_status == "ok",
            models.MediaItem.tire_concern_noted.is_(True),
        )
    ).first()


def evaluate_gate(db: Session, session_id: str) -> GateDecision:
    identity = {field: get_field_evidence(db, session_id, field) for field in IDENTITY_FIELDS}
    mileage = get_field_evidence(db, session_id, MILEAGE_FIELD)

    # 1a. A confirmed non-truck and nothing that confirms a tractor unit.
    non_truck_category = _rejected_non_truck_category(db, session_id)
    if non_truck_category and not _has_confirmed_tractor_unit(db, session_id):
        return GateDecision(
            status=STATUS_UNSUPPORTED,
            reasons=[f"Detected vehicle category '{non_truck_category}' — this system only appraises tractor units."],
            next_photo=None,
            identity=identity,
            mileage=mileage,
        )

    # 1b. No usable evidence of a truck at all yet.
    if not _has_confirmed_tractor_unit(db, session_id):
        return GateDecision(
            status=STATUS_NEEDS_EVIDENCE,
            reasons=["No usable tractor-unit photo has been accepted yet."],
            next_photo=NextPhotoRequest(
                requested_view="A clear front-three-quarter exterior photo of the whole tractor unit",
                reason="Vehicle identity hasn't been established yet.",
                resolves="vehicle_identity",
            ),
            identity=identity,
            mileage=mileage,
        )

    # 1c. Identity/specification fields, in order — first unresolved one wins.
    for field in IDENTITY_FIELDS:
        field_evidence = identity[field]
        if field_evidence.status == "conflicting":
            return GateDecision(
                status=STATUS_NEEDS_EVIDENCE,
                reasons=[
                    f"Conflicting {field} evidence: seller declared '{field_evidence.seller_declared}', "
                    f"but photo evidence suggests {field_evidence.observed_from_photo or field_evidence.inferred_candidates}."
                ],
                next_photo=NextPhotoRequest(
                    requested_view=_IDENTITY_VIEW[field],
                    reason=f"{field} evidence disagrees between sources and materially affects the comparable group.",
                    resolves=field,
                ),
                identity=identity,
                mileage=mileage,
            )
        if field_evidence.status == "unknown":
            return GateDecision(
                status=STATUS_NEEDS_EVIDENCE,
                reasons=[f"{field} is not yet identified."],
                next_photo=NextPhotoRequest(
                    requested_view=_IDENTITY_VIEW[field],
                    reason=_IDENTITY_PURPOSE[field],
                    resolves=field,
                ),
                identity=identity,
                mileage=mileage,
            )

    # 2. Mileage.
    if mileage.status == "conflicting":
        return GateDecision(
            status=STATUS_NEEDS_EVIDENCE,
            reasons=[
                f"Seller declared {mileage.seller_declared} km, but a photo appears to show "
                f"{mileage.observed_from_photo} km. This is not resolved automatically."
            ],
            next_photo=NextPhotoRequest(
                requested_view="A clear, straight-on photo of the dashboard odometer",
                reason="Declared and observed mileage disagree materially, and mileage is a required pricing input.",
                resolves=MILEAGE_FIELD,
            ),
            identity=identity,
            mileage=mileage,
        )
    if mileage.status == "unknown":
        return GateDecision(
            status=STATUS_NEEDS_EVIDENCE,
            reasons=["Mileage has not been declared or observed yet."],
            next_photo=NextPhotoRequest(
                requested_view="A clear, straight-on photo of the dashboard odometer",
                reason="Mileage materially affects price and is required before pricing.",
                resolves=MILEAGE_FIELD,
            ),
            identity=identity,
            mileage=mileage,
        )

    # 3. Suspected structural damage blocks an individual price outright.
    if _structural_damage_media(db, session_id) is not None:
        return GateDecision(
            status=STATUS_INSPECTION_REQUIRED,
            reasons=[
                "Possible structural/chassis damage was noted in at least one photo — "
                "an individual price is withheld pending in-person inspection."
            ],
            next_photo=NextPhotoRequest(
                requested_view="A clearer, well-lit photo of the chassis/frame area near the suspected damage",
                reason="Confirms whether the damage is cosmetic or structural before any price is shown.",
                resolves="chassis_suspension",
            ),
            identity=identity,
            mileage=mileage,
        )

    return GateDecision(status=STATUS_READY, reasons=[], next_photo=None, identity=identity, mileage=mileage)


def next_best_photo(db: Session, session_id: str) -> NextPhotoRequest | None:
    """Single highest-priority next photo, independent of whether pricing
    already succeeded — tiers 1-3 reuse the gate's own blocking request;
    tiers 4-5 only ever suggest, never block."""
    decision = evaluate_gate(db, session_id)
    if decision.next_photo is not None:
        return decision.next_photo

    if _tire_concern_media(db, session_id) is not None:
        return NextPhotoRequest(
            requested_view="Close-up photos of the tire tread and sidewall",
            reason="A possible tire issue was noted but not clearly resolved.",
            resolves="tire",
        )

    coverage = coverage_only(db, session_id)
    for component in REQUIRED_COMPONENTS:
        if coverage.get(component) == "missing":
            return NextPhotoRequest(
                requested_view=f"A clear photo of the {COMPONENT_LABELS[component]}",
                reason="This view hasn't been captured yet and helps complete the report buyers see.",
                resolves=component,
            )

    return None
