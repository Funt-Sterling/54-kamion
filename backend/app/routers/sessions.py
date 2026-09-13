from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..constants import TRACKED_EVIDENCE_FIELDS
from ..contract import (
    DECLARABLE_FIELDS,
    PROV_SELLER,
    PROV_USER_CORRECTED,
    STATE_ACTIVE,
    STATE_SUPERSEDED,
)
from ..db import get_db
from ..services.canonical import canonicalize
from ..services.declarations import validate_declared_value
from ..services import gate
from ..services.evidence import get_field_evidence
from ..services.session_state import coverage_only

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=schemas.SessionOut, status_code=201)
def create_session(db: Session = Depends(get_db)):
    session = models.InspectionSession()
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _get_session_or_404(db: Session, session_id: str) -> models.InspectionSession:
    session = db.get(models.InspectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _next_photo_out(request: gate.NextPhotoRequest | None) -> schemas.NextPhotoOut | None:
    if request is None:
        return None
    return schemas.NextPhotoOut(requested_view=request.requested_view, reason=request.reason, resolves=request.resolves)


def _limitations(db: Session, session_id: str) -> list[str]:
    """Image-level limitations from successful analyses of accepted photos,
    de-duplicated in first-seen order. Kept apart from findings: a thing
    that could not be assessed is not a thing that was found."""
    runs = db.scalars(
        select(models.InferenceRun)
        .join(models.MediaItem, models.MediaItem.id == models.InferenceRun.media_id)
        .where(
            models.InferenceRun.session_id == session_id,
            models.InferenceRun.failure_type.is_(None),
            models.MediaItem.accepted.is_(True),
        )
        .order_by(models.InferenceRun.created_at)
    ).all()
    seen: dict[str, None] = {}
    for run in runs:
        for item in run.limitations or []:
            if isinstance(item, str) and item.strip():
                seen.setdefault(item.strip(), None)
    return list(seen)


@router.get("/{session_id}", response_model=schemas.SessionDetailOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    coverage = coverage_only(db, session_id)
    evidence = [get_field_evidence(db, session_id, field) for field in TRACKED_EVIDENCE_FIELDS]
    # One gate evaluation feeds both the verdict and the photo request, so
    # they can never disagree within a response.
    decision = gate.evaluate_gate(db, session_id)
    next_photo = gate.next_best_photo(db, session_id, decision)
    findings = db.scalars(select(models.Finding).where(models.Finding.session_id == session_id)).all()
    concerns = db.scalars(
        select(models.Concern).where(models.Concern.session_id == session_id).order_by(models.Concern.created_at)
    ).all()
    last_appraisal = db.scalars(
        select(models.Appraisal)
        .where(models.Appraisal.session_id == session_id)
        .order_by(models.Appraisal.version.desc())
        .limit(1)
    ).first()

    return schemas.SessionDetailOut(
        id=session.id,
        status=session.status,
        coverage=coverage,
        evidence=[schemas.FieldEvidenceOut(**vars(e)) for e in evidence],
        next_photo=_next_photo_out(next_photo),
        findings=[schemas.FindingOut.model_validate(f) for f in findings],
        latest_appraisal_id=last_appraisal.id if last_appraisal else None,
        gate_status=decision.status,
        gate_reasons=[*decision.reasons, *(f"Assumption: {a}" for a in decision.assumptions)],
        evidence_revision=session.evidence_revision or 1,
        concerns=[schemas.ConcernOut.model_validate(c) for c in concerns],
        limitations=_limitations(db, session_id),
    )


@router.patch("/{session_id}/details", response_model=list[schemas.FieldEvidenceOut])
def declare_detail(session_id: str, body: schemas.EvidenceIn, db: Session = Depends(get_db)):
    """Record a seller declaration or user correction.

    Two boundaries are enforced here, both of which were previously absent:

    * `field` must be in the declarable allowlist. A caller cannot declare
      `vehicle_category` and hand themselves a tractor identity.
    * provenance is assigned by the SERVER from the request's intent. It is
      no longer accepted from the body, so no caller can mint
      `observed_from_photo` evidence without a photo.

    Declarations never overwrite visual evidence; a disagreement surfaces
    as a conflict rather than silently replacing the observation.
    """
    session = _get_session_or_404(db, session_id)

    if body.field not in DECLARABLE_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=f"{body.field!r} cannot be declared; allowed: {list(DECLARABLE_FIELDS)}",
        )

    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="value must not be empty")

    problem = validate_declared_value(body.field, value)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    provenance = PROV_SELLER if body.intent == "seller_declared" else PROV_USER_CORRECTED
    resolved = canonicalize(body.field, value)
    canonical = resolved.canonical
    if body.field in ("mileage_km", "year"):
        # Validated above as digits (spaces allowed); store the bare number
        # so "365 000" and an odometer's "365000" compare as the same fact.
        canonical = str(int("".join(value.split())))

    # A new seller declaration supersedes the seller's OWN earlier one for
    # the same field — and nothing else. Visual records are untouched.
    previous = db.scalars(
        select(models.EvidenceRecord).where(
            models.EvidenceRecord.session_id == session_id,
            models.EvidenceRecord.field == body.field,
            models.EvidenceRecord.provenance == provenance,
            models.EvidenceRecord.state == STATE_ACTIVE,
        )
    ).all()
    for record in previous:
        record.state = STATE_SUPERSEDED
        record.state_reason = "replaced by a newer declaration from the same source"
        db.add(record)

    db.add(
        models.EvidenceRecord(
            session_id=session_id,
            field=body.field,
            value=value,
            canonical_value=canonical,
            display_value=value,
            provenance=provenance,
            state=STATE_ACTIVE,
            basis={"intent": body.intent},
        )
    )
    session.evidence_revision = (session.evidence_revision or 1) + 1
    db.add(session)
    db.commit()

    return [schemas.FieldEvidenceOut(**vars(get_field_evidence(db, session_id, field))) for field in TRACKED_EVIDENCE_FIELDS]
