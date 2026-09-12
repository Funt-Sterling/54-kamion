import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..constants import REQUIRED_COMPONENTS
from ..db import get_db
from ..services import quality
from ..services.vision import get_vision_adapter

router = APIRouter(prefix="/sessions", tags=["media"])

ANALYZED_KINDS = {"photo", "frame"}


def _get_session_or_404(db: Session, session_id: str) -> models.InspectionSession:
    session = db.get(models.InspectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/media", response_model=schemas.MediaOut, status_code=201)
def upload_media(
    session_id: str,
    file: UploadFile = File(...),
    kind: str = Form(...),
    source: str = Form(...),
    component_hint: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if kind not in ("photo", "video", "frame"):
        raise HTTPException(status_code=422, detail="kind must be photo, video, or frame")
    if source not in ("captured", "imported"):
        raise HTTPException(status_code=422, detail="source must be captured or imported")

    session = _get_session_or_404(db, session_id)
    settings = get_settings()

    session_dir = Path(settings.media_dir) / session.id
    session_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload").suffix or ".jpg"
    dest = session_dir / f"{uuid.uuid4().hex[:12]}{suffix}"

    contents = file.file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb}MB limit")
    dest.write_bytes(contents)

    accepted = True
    reject_reason: str | None = None
    quality_notes: list[str] = []
    component_tag = component_hint if component_hint in REQUIRED_COMPONENTS else component_hint

    if kind in ANALYZED_KINDS:
        try:
            result = quality.assess_image(dest)
        except Exception:
            accepted, reject_reason = False, "unreadable_file"
            result = None

        if result is not None:
            quality_notes = result.issues
            existing_hashes = [
                m.perceptual_hash
                for m in db.scalars(
                    select(models.MediaItem).where(
                        models.MediaItem.session_id == session.id,
                        models.MediaItem.accepted.is_(True),
                    )
                ).all()
                if m.perceptual_hash
            ]
            if quality.is_duplicate(result.phash, existing_hashes):
                accepted, reject_reason = False, "duplicate_of_existing_photo"
            elif not result.ok:
                accepted, reject_reason = False, result.issues[0]

    media = models.MediaItem(
        session_id=session.id,
        kind=kind,
        source=source,
        file_path=str(dest),
        component_tag=component_tag,
        accepted=accepted,
        reject_reason=reject_reason,
        quality_notes=quality_notes,
        perceptual_hash=result.phash if kind in ANALYZED_KINDS and result is not None else None,
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    # Only send accepted stills/frames to the (slower, paid) vision model.
    # Video frame sampling/extraction is out of scope for this scaffold —
    # see docs/plan.md's capture-and-feedback section for the intended design.
    if accepted and kind in ANALYZED_KINDS:
        _analyze_and_record(db, session, media, component_hint)

    return media


def _analyze_and_record(
    db: Session,
    session: models.InspectionSession,
    media: models.MediaItem,
    component_hint: str | None,
) -> None:
    adapter = get_vision_adapter()
    vision_result = adapter.analyze_image(media.file_path, component_hint)

    if not vision_result.is_vehicle:
        media.accepted = False
        media.reject_reason = "no_vehicle_detected"
    elif not vision_result.is_truck_tractor_unit:
        media.accepted = False
        media.reject_reason = f"not_a_tractor_unit:{vision_result.vehicle_category_guess or 'unknown'}"
    else:
        if vision_result.vehicle_category_guess and not media.component_tag:
            media.component_tag = component_hint
        for field, value in vision_result.extracted_specs.items():
            db.add(models.DeclaredDetail(session_id=session.id, field=field, value=value, source="extracted"))
        for obs in vision_result.component_observations:
            db.add(
                models.Finding(
                    session_id=session.id,
                    media_id=media.id,
                    component=obs.component,
                    observation=obs.observation,
                    visibility=obs.visibility,
                    recommended_action=obs.recommended_action,
                )
            )

    db.add(media)
    db.commit()
