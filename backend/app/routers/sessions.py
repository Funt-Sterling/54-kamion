from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..constants import TRACKED_EVIDENCE_FIELDS
from ..db import get_db
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


@router.get("/{session_id}", response_model=schemas.SessionDetailOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    coverage = coverage_only(db, session_id)
    evidence = [get_field_evidence(db, session_id, field) for field in TRACKED_EVIDENCE_FIELDS]
    next_photo = gate.next_best_photo(db, session_id)
    findings = db.scalars(select(models.Finding).where(models.Finding.session_id == session_id)).all()
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
    )


@router.patch("/{session_id}/details", response_model=list[schemas.FieldEvidenceOut])
def declare_detail(session_id: str, body: schemas.EvidenceIn, db: Session = Depends(get_db)):
    """Record a seller-declared (or otherwise provenanced) fact. Never
    overwrites prior evidence — if this conflicts with something already
    observed from a photo, GET /sessions/{id} (and the field returned here)
    will show status="conflicting" rather than silently picking a value."""
    _get_session_or_404(db, session_id)
    db.add(
        models.EvidenceRecord(session_id=session_id, field=body.field, value=body.value, provenance=body.provenance)
    )
    db.commit()

    return [schemas.FieldEvidenceOut(**vars(get_field_evidence(db, session_id, field))) for field in TRACKED_EVIDENCE_FIELDS]
