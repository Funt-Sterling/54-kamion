"""Turning an admitted proposal into persisted evidence.

Parent-owned integration boundary: the vision adapter produces a proposal,
`admission.admit` decides what it supports, and this module is the only
place those results become rows. Keeping it in one function makes the
trust transition auditable — there is exactly one path from "a model said
something" to "the database believes something".
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..contract import (
    SEVERITY_STRUCTURAL,
    STATE_ACTIVE,
    VIEW_BADGE,
    VIEW_CAB,
    VIEW_CHASSIS,
    VIEW_DASHBOARD,
    VIEW_FRONT,
    VIEW_ODOMETER,
    VIEW_REAR,
    VIEW_SIDE,
    VIEW_TIRE,
)
from .admission import AdmissionResult
from .evidence import supersede_weaker

#: Which admitted views answer each UI request slot. Used only for
#: after-the-fact mismatch feedback ("you were asked for X").
COMPONENT_TO_VIEWS: dict[str, set[str]] = {
    "front_exterior": {VIEW_FRONT},
    "rear_exterior": {VIEW_REAR},
    "side_exterior": {VIEW_SIDE},
    "tire": {VIEW_TIRE},
    "chassis_suspension": {VIEW_CHASSIS},
    "dashboard_odometer": {VIEW_DASHBOARD, VIEW_ODOMETER},
    "cab_interior": {VIEW_CAB},
    # Gate request targets that are not checklist rows.
    "vehicle_identity": {VIEW_FRONT, VIEW_REAR, VIEW_SIDE},
    "model_family": {VIEW_BADGE},
    "axle_config": {VIEW_SIDE},
    "mileage_km": {VIEW_ODOMETER},
    "year": {VIEW_BADGE},
}
# A clarify-concern request resolves a contract view by name ("chassis").
COMPONENT_TO_VIEWS.update({view: {view} for view in (VIEW_FRONT, VIEW_REAR, VIEW_SIDE, VIEW_DASHBOARD, VIEW_ODOMETER, VIEW_CAB, VIEW_CHASSIS, VIEW_BADGE)})

_ALL_VIEWS = {VIEW_FRONT, VIEW_REAR, VIEW_SIDE, VIEW_TIRE, VIEW_DASHBOARD, VIEW_ODOMETER, VIEW_CAB, VIEW_CHASSIS, VIEW_BADGE}


def sha256_of(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bump_revision(db: Session, session: models.InspectionSession) -> int:
    """Any material evidence change invalidates a previously computed range."""
    session.evidence_revision = (session.evidence_revision or 1) + 1
    db.add(session)
    return session.evidence_revision


def persist_admission(
    db: Session,
    session: models.InspectionSession,
    media: models.MediaItem,
    result: AdmissionResult,
    run: models.InferenceRun | None,
) -> None:
    """Write observed views, claims, findings and concerns for one image.

    Nothing derived from the client's requested view is written. If the
    admission result credits nothing, this creates nothing, and the photo
    correctly leaves the session no better off.
    """
    run_id = run.id if run is not None else None
    changed = False

    for view in result.views:
        db.add(
            models.ObservedView(
                session_id=session.id,
                media_id=media.id,
                run_id=run_id,
                view=view.view,
                visibility="visible",
                usable=view.usable,
                limitation=view.limitation,
                state=STATE_ACTIVE,
            )
        )
        changed = True

    new_records: list[models.EvidenceRecord] = []
    for claim in result.claims:
        record = models.EvidenceRecord(
            session_id=session.id,
            field=claim.field,
            value=claim.raw_value,
            canonical_value=claim.canonical_value,
            display_value=claim.display_value,
            provenance=claim.provenance,
            media_id=media.id,
            run_id=run_id,
            state=STATE_ACTIVE,
            basis=claim.basis,
        )
        db.add(record)
        new_records.append(record)
        changed = True

    # Ids are needed before the supersession policy can link records.
    db.flush()
    for record in new_records:
        supersede_weaker(db, session.id, record.field, record)

    for finding in result.findings:
        db.add(
            models.Finding(
                session_id=session.id,
                media_id=media.id,
                component=finding["component"],
                observation=finding["observation"],
                visibility=finding["visibility"],
                recommended_action=finding.get("recommended_action", "none"),
            )
        )
        changed = True

    for concern in result.concerns:
        db.add(
            models.Concern(
                session_id=session.id,
                media_id=media.id,
                component=concern["component"],
                severity=concern["severity"],
                description=concern["description"],
                status="open",
            )
        )
        changed = True

    if run is not None:
        run.limitations = list(result.limitations)
        db.add(run)

    if changed:
        bump_revision(db, session)


def requested_view_satisfied(requested: str | None, admitted_views: set[str]) -> bool | None:
    """Did the photo answer what the server asked for?

    None when nothing specific was requested. Deterministic mismatch
    feedback computed AFTER perception — the request never influenced
    what was admitted.
    """
    if not requested:
        return None
    wanted = COMPONENT_TO_VIEWS.get(requested)
    if not wanted:
        return None
    return bool(wanted & admitted_views)


def resolve_concerns_for(db: Session, session: models.InspectionSession, result: AdmissionResult, media_id: str) -> None:
    """A clear retake closes only the concern it actually addresses.

    Scoped by contract view. A clean photo of the cab never touches a
    chassis concern. A structural concern is never auto-resolved by any
    photo: a same-view photo moves it to `review_required`, which still
    blocks an individual price until a person decides.
    """
    admitted = {view.view for view in result.views if view.usable}
    if not admitted:
        return

    earlier = db.scalars(
        select(models.Concern).where(
            models.Concern.session_id == session.id,
            models.Concern.status == "open",
            models.Concern.media_id != media_id,
        )
    ).all()

    changed = False
    for concern in earlier:
        if concern.component not in _ALL_VIEWS or concern.component not in admitted:
            continue
        if concern.severity == SEVERITY_STRUCTURAL:
            concern.status = "review_required"
        else:
            concern.status = "resolved"
            concern.resolved_by_media_id = media_id
        db.add(concern)
        changed = True

    if changed:
        bump_revision(db, session)
