"""Derives the "what do we know, what's missing" view of a session that both
`GET /sessions/{id}` and the appraisal endpoint need: latest declared/
extracted details, per-component coverage, and the single next capture
instruction to show the user."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import COVERAGE_ATTENTION, COVERAGE_CAPTURED, COVERAGE_MISSING, REQUIRED_COMPONENTS
from ..models import DeclaredDetail, MediaItem

COMPONENT_LABELS = {
    "front_exterior": "front of the truck",
    "rear_exterior": "rear of the truck",
    "side_exterior": "both sides of the truck",
    "tire": "tire tread and sidewall, close up",
    "chassis_suspension": "chassis, suspension, and fifth wheel",
    "dashboard_odometer": "dashboard and odometer",
    "cab_interior": "cab interior",
}


def latest_details(db: Session, session_id: str) -> dict[str, DeclaredDetail]:
    rows = db.scalars(
        select(DeclaredDetail).where(DeclaredDetail.session_id == session_id).order_by(DeclaredDetail.created_at)
    ).all()
    latest: dict[str, DeclaredDetail] = {}
    for row in rows:
        latest[row.field] = row  # later rows overwrite earlier ones for the same field
    return latest


def coverage_and_instruction(db: Session, session_id: str) -> tuple[dict[str, str], str | None]:
    media = db.scalars(select(MediaItem).where(MediaItem.session_id == session_id)).all()

    coverage = {c: COVERAGE_MISSING for c in REQUIRED_COMPONENTS}
    for item in media:
        if item.component_tag not in coverage:
            continue
        if not item.accepted:
            continue
        if item.quality_notes:
            coverage[item.component_tag] = COVERAGE_ATTENTION
        elif coverage[item.component_tag] != COVERAGE_ATTENTION:
            coverage[item.component_tag] = COVERAGE_CAPTURED

    for component in REQUIRED_COMPONENTS:
        if coverage[component] == COVERAGE_MISSING:
            label = COMPONENT_LABELS[component]
            return coverage, f"Capture a clear, well-lit photo of the {label}."

    for component in REQUIRED_COMPONENTS:
        if coverage[component] == COVERAGE_ATTENTION:
            label = COMPONENT_LABELS[component]
            return coverage, f"The {label} photo needs a retake — move closer or improve lighting."

    return coverage, None
