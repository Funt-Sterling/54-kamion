import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..contract import (
    ALL_ORIGINS,
    CATEGORY_NONVEHICLE,
    CATEGORY_OTHER_VEHICLE,
    MAX_ANALYSES_PER_MINUTE,
    MAX_ANALYSIS_ATTEMPTS_PER_MEDIA,
    MAX_MEDIA_PER_SESSION,
    PROMPT_VERSION,
    VISION_SCHEMA_VERSION,
)
from ..db import get_db
from ..services import ingest, quality
from ..services.admission import admit
from ..services.proposal import ProposalSchemaError
from ..services.vision import get_vision_adapter

router = APIRouter(prefix="/sessions", tags=["media"])

ANALYZED_KINDS = {"photo", "frame"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_VISION_FAILURE_NOTE = "vision_analysis_failed"


_analysis_times: deque[float] = deque()
_analysis_lock = threading.Lock()


def _reserve_analysis_slot() -> bool:
    """Process-wide sliding one-minute window on model calls."""
    now = time.monotonic()
    with _analysis_lock:
        while _analysis_times and now - _analysis_times[0] > 60:
            _analysis_times.popleft()
        if len(_analysis_times) >= MAX_ANALYSES_PER_MINUTE:
            return False
        _analysis_times.append(now)
        return True


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
    #: What the client was ASKED for. Request metadata only — it never
    #: reaches the perception prompt and never credits coverage. Retained
    #: solely so the response can say "you were asked for X, this shows Y".
    component_hint: str | None = Form(default=None),
    capture_origin: str = Form(default="unknown"),
    db: Session = Depends(get_db),
):
    if kind not in ("photo", "video", "frame"):
        raise HTTPException(status_code=422, detail="kind must be photo, video, or frame")
    if source not in ("captured", "imported"):
        raise HTTPException(status_code=422, detail="source must be captured or imported")
    if capture_origin not in ALL_ORIGINS:
        raise HTTPException(status_code=422, detail=f"capture_origin must be one of {list(ALL_ORIGINS)}")

    requested = component_hint if component_hint in ingest.COMPONENT_TO_VIEWS else None

    session = _get_session_or_404(db, session_id)
    settings = get_settings()

    contents = file.file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb}MB limit")
    if not contents:
        raise HTTPException(status_code=422, detail="Empty file")

    digest = ingest.sha256_bytes(contents)

    same_bytes = db.scalars(
        select(models.MediaItem)
        .where(models.MediaItem.session_id == session.id, models.MediaItem.image_sha256 == digest)
        .order_by(models.MediaItem.created_at)
    ).all()
    rejected_before = next((m for m in same_bytes if not m.accepted), None)
    if rejected_before is not None and not any(m.accepted for m in same_bytes):
        # Exactly these bytes were already refused: answer the same way
        # without writing another file or row.
        return _media_out(db, rejected_before, requested)

    media_count = db.scalar(
        select(func.count()).select_from(models.MediaItem).where(models.MediaItem.session_id == session.id)
    )
    if media_count >= MAX_MEDIA_PER_SESSION:
        raise HTTPException(status_code=429, detail=f"This inspection already has {MAX_MEDIA_PER_SESSION} uploads.")

    # Idempotent retry: the SAME bytes whose analysis previously failed are
    # re-analysed on the SAME media row, instead of being rejected as a
    # duplicate (the old behaviour left the session stuck) or piling up new
    # rows. Bounded, so a permanently failing image cannot loop forever.
    if kind in ANALYZED_KINDS:
        failed = db.scalars(
            select(models.MediaItem).where(
                models.MediaItem.session_id == session.id,
                models.MediaItem.image_sha256 == digest,
                models.MediaItem.accepted.is_(True),
                # "pending" covers an analysis interrupted by a crash.
                models.MediaItem.vision_status.in_(("failed", "pending")),
            )
        ).first()
        if failed is not None:
            _analyze_and_record(db, session, failed)
            db.refresh(failed)
            return _media_out(db, failed, requested)

    session_dir = Path(settings.media_dir) / session.id
    session_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".jpg"
    dest = session_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
    dest.write_bytes(contents)

    accepted = True
    reject_reason: str | None = None
    quality_notes: list[str] = []
    result = None

    if kind in ANALYZED_KINDS:
        try:
            result = quality.assess_image(dest)
        except Exception:
            accepted, reject_reason = False, "unreadable_file"
            result = None

        if result is not None:
            quality_notes = result.issues
            # Only media that was actually ANALYSED counts as a duplicate, so
            # a photo whose analysis failed never blocks its own retry.
            existing_hashes = [
                m.perceptual_hash
                for m in db.scalars(
                    select(models.MediaItem).where(
                        models.MediaItem.session_id == session.id,
                        models.MediaItem.accepted.is_(True),
                        models.MediaItem.vision_status == "ok",
                    )
                ).all()
                if m.perceptual_hash
            ]
            if quality.is_duplicate(result.phash, existing_hashes):
                # Already analysed: adds no independent evidence.
                accepted, reject_reason = False, "duplicate_of_existing_photo"
            elif not result.ok:
                accepted, reject_reason = False, result.issues[0]

    media = models.MediaItem(
        session_id=session.id,
        kind=kind,
        source=source,
        file_path=str(dest),
        # Stored as the REQUEST it is. Nothing derives coverage from this.
        client_hint=requested,
        component_tag=requested,  # legacy column, retained for reversibility
        capture_origin=capture_origin,
        image_sha256=digest,
        accepted=accepted,
        reject_reason=reject_reason,
        quality_notes=quality_notes,
        perceptual_hash=result.phash if kind in ANALYZED_KINDS and result is not None else None,
        vision_status="pending",
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    if accepted and kind in ANALYZED_KINDS:
        _analyze_and_record(db, session, media)

    db.refresh(media)
    return _media_out(db, media, requested)


def _media_out(db: Session, media: models.MediaItem, requested: str | None) -> schemas.MediaOut:
    """Report the request and the observations as separate things.

    The old response exposed `component_tag` — the client's own hint —
    which the UI then displayed as though it were a detected view.
    """
    views = db.scalars(
        select(models.ObservedView).where(
            models.ObservedView.media_id == media.id,
            models.ObservedView.state == "active",
        )
    ).all()
    admitted = {view.view for view in views if view.usable}

    return schemas.MediaOut(
        id=media.id,
        session_id=media.session_id,
        kind=media.kind,
        accepted=media.accepted,
        reject_reason=media.reject_reason,
        quality_notes=media.quality_notes or [],
        vision_status=media.vision_status,
        requested_view=requested,
        observed_views=[
            schemas.ObservedViewOut(
                view=view.view,
                visibility=view.visibility,
                usable=view.usable,
                limitation=view.limitation,
            )
            for view in views
        ],
        capture_origin=media.capture_origin or "unknown",
        requested_view_satisfied=(
            ingest.requested_view_satisfied(requested, admitted) if media.vision_status == "ok" else None
        ),
    )


def _configured_model_id() -> str:
    """Model named on a failed run, when no call result carries one."""
    settings = get_settings()
    return settings.vision_model if settings.vision_adapter == "anthropic" else f"adapter:{settings.vision_adapter}"


def _analyze_and_record(db: Session, session: models.InspectionSession, media: models.MediaItem) -> None:
    """Run the adapter, admit what it supports, persist only that.

    Failure classes stay distinct because they mean different things:

    * transport/timeout -> vision_status="failed", retryable, no evidence
    * schema error      -> the envelope is unusable, nothing is admitted
    * admitted-nothing  -> the photo WAS read and supported nothing

    The last is not a failure. It is the honest answer for a close-up of a
    mudflap and must not look like an error.

    Note there is no hint parameter: perception is hint-blind.
    """
    attempts = db.scalar(
        select(func.count()).select_from(models.InferenceRun).where(models.InferenceRun.media_id == media.id)
    )
    if attempts >= MAX_ANALYSIS_ATTEMPTS_PER_MEDIA:
        media.vision_status = "failed"
        notes = [n for n in (media.quality_notes or []) if not n.startswith(_VISION_FAILURE_NOTE)]
        media.quality_notes = [*notes, f"{_VISION_FAILURE_NOTE}:retry_limit"]
        db.add(media)
        db.commit()
        return

    if not _reserve_analysis_slot():
        # Not a model failure and not counted as an attempt: the photo stays
        # pending-retryable and the same bytes can be sent again shortly.
        media.vision_status = "failed"
        notes = [n for n in (media.quality_notes or []) if not n.startswith(_VISION_FAILURE_NOTE)]
        media.quality_notes = [*notes, f"{_VISION_FAILURE_NOTE}:rate_limited"]
        db.add(media)
        db.commit()
        return

    started = time.monotonic()
    call = None
    failure_type: str | None = None
    raw_text: str | None = None

    try:
        adapter = get_vision_adapter()
        call = adapter.analyze_image(media.file_path)
    except ProposalSchemaError as exc:
        failure_type = "schema"
        raw_text = str(exc)
    except TimeoutError as exc:
        failure_type = "timeout"
        raw_text = type(exc).__name__
    except Exception as exc:  # transport, missing credential, SDK timeout
        failure_type = "timeout" if "timeout" in type(exc).__name__.lower() else "transport"
        raw_text = type(exc).__name__

    latency_ms = int((time.monotonic() - started) * 1000)
    # Current state only: history lives in InferenceRun rows.
    base_notes = [n for n in (media.quality_notes or []) if not n.startswith(_VISION_FAILURE_NOTE)]

    if failure_type is not None or call is None:
        media.vision_status = "failed"
        # A short class name only — SDK/exception detail stays server-side.
        media.quality_notes = [*base_notes, f"{_VISION_FAILURE_NOTE}:{failure_type}"]
        db.add(
            models.InferenceRun(
                session_id=session.id,
                media_id=media.id,
                model_id=_configured_model_id(),
                prompt_version=PROMPT_VERSION,
                schema_version=VISION_SCHEMA_VERSION,
                image_sha256=media.image_sha256,
                raw_proposal=(raw_text or "")[:4000],
                failure_type=failure_type or "unknown",
                latency_ms=latency_ms,
            )
        )
        db.add(media)
        db.commit()
        return

    proposal = call.proposal
    result = admit(proposal)

    run = models.InferenceRun(
        session_id=session.id,
        media_id=media.id,
        model_id=call.model_id,
        prompt_version=call.prompt_version,
        schema_version=call.schema_version,
        image_sha256=media.image_sha256,
        raw_proposal=(call.raw_text or "")[:8000],
        admitted=[claim.field for claim in result.claims] + [f"view:{v.view}" for v in result.views],
        rejected=[{"claim": r.claim, "reason": r.reason} for r in result.rejected],
        usage=call.usage or {},
        latency_ms=call.latency_ms or latency_ms,
        failure_type=None,
    )
    db.add(run)
    db.flush()

    media.vision_status = "ok"
    media.quality_notes = base_notes

    if proposal.subject_category in (CATEGORY_NONVEHICLE, CATEGORY_OTHER_VEHICLE):
        # A motorcycle's tyre is not truck tyre coverage. Nothing from this
        # image enters the truck's evidence; the rejection itself is what
        # the gate reads to report an unsupported vehicle.
        media.accepted = False
        media.reject_reason = (
            "no_vehicle_detected"
            if proposal.subject_category == CATEGORY_NONVEHICLE
            else "not_a_tractor_unit:other_vehicle"
        )
        run.limitations = list(result.limitations)
        db.add_all([run, media])
        db.commit()
        return

    ingest.persist_admission(db, session, media, result, run)
    ingest.resolve_concerns_for(db, session, result, media.id)

    db.add(media)
    db.commit()
