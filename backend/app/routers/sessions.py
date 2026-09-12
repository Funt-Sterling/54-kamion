from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..services.session_state import coverage_and_instruction, latest_details

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


@router.get("/{session_id}", response_model=schemas.SessionDetailOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    coverage, next_instruction = coverage_and_instruction(db, session_id)
    details = latest_details(db, session_id)
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
        next_instruction=next_instruction,
        declared_details=[schemas.DetailOut.model_validate(d) for d in details.values()],
        findings=[schemas.FindingOut.model_validate(f) for f in findings],
        latest_appraisal_id=last_appraisal.id if last_appraisal else None,
    )


@router.patch("/{session_id}/details", response_model=list[schemas.DetailOut])
def set_detail(session_id: str, body: schemas.DetailIn, db: Session = Depends(get_db)):
    _get_session_or_404(db, session_id)
    db.add(models.DeclaredDetail(session_id=session_id, field=body.field, value=body.value, source=body.source))
    db.commit()

    details = latest_details(db, session_id)
    return [schemas.DetailOut.model_validate(d) for d in details.values()]
