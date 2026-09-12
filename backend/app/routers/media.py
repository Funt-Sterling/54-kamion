import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
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
    component_tag = component_hint
    result = None

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
        # Photos never analyzed (video/frame sampling — out of scope, see
        # docs/plan.md) or rejected before analysis just stay "pending".
        vision_status="pending",
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    # Only send quality-accepted stills/frames to the (slower, paid) vision
    # model. Video frame sampling/extraction is out of scope for this
    # scaffold — see docs/plan.md's capture-and-feedback section.
    if accepted and kind in ANALYZED_KINDS:
        _analyze_and_record(db, session, media, component_hint)

    return media


def _analyze_and_record(
    db: Session,
    session: models.InspectionSession,
    media: models.MediaItem,
    component_hint: str | None,
) -> None:
    """Runs the vision adapter and turns its output into evidence records
    and findings. Milestone 3: a missing credential, a timeout, or a
    malformed response from the model must never crash the request or get
    treated as successful analysis — it's recorded as vision_status="failed"
    so the caller (and the evidence gate) can tell the difference between
    "no evidence yet" and "we don't actually know, ask again."
    """
    try:
        adapter = get_vision_adapter()
        vision_result = adapter.analyze_image(media.file_path, component_hint)
    except Exception as exc:
        media.vision_status = "failed"
        media.quality_notes = [*media.quality_notes, f"vision_analysis_failed: {exc}"]
        db.add(media)
        db.commit()
        return

    media.vision_status = "ok"

    if not vision_result.is_vehicle:
        media.accepted = False
        media.reject_reason = "no_vehicle_detected"
    elif not vision_result.is_truck_tractor_unit:
        media.accepted = False
        media.reject_reason = f"not_a_tractor_unit:{vision_result.vehicle_category_guess or 'unknown'}"
    else:
        media.structural_damage_suspected = vision_result.structural_damage_suspected
        media.tire_concern_noted = vision_result.tire_concern_noted
        if not media.component_tag:
            media.component_tag = component_hint

        db.add(
            models.EvidenceRecord(
                session_id=session.id,
                field="vehicle_category",
                value=vision_result.vehicle_category_guess or "unknown",
                provenance="observed_from_photo",
                media_id=media.id,
            )
        )

        # A legible badge/spec-plate reading is trustworthy observed
        # evidence; a visual guess without one is only a candidate — the
        # evidence gate treats these very differently (see services/gate.py).
        if vision_result.model_guess:
            provenance = "observed_from_photo" if vision_result.visible_badge_text else "inferred_candidate"
            db.add(
                models.EvidenceRecord(
                    session_id=session.id,
                    field="model_family",
                    value=vision_result.model_guess,
                    provenance=provenance,
                    media_id=media.id,
                )
            )

        for spec_field, value in vision_result.extracted_specs.items():
            db.add(
                models.EvidenceRecord(
                    session_id=session.id,
                    field=spec_field,
                    value=value,
                    provenance="observed_from_photo",
                    media_id=media.id,
                )
            )

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
