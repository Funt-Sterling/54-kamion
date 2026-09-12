"""Per-component capture coverage. The "what's missing, what's next"
decision now lives in app.services.gate — this module only answers
"has this component been usably photographed yet," which the gate and
GET /sessions/{id} both need."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import COVERAGE_ATTENTION, COVERAGE_CAPTURED, COVERAGE_MISSING, REQUIRED_COMPONENTS
from ..models import MediaItem

COMPONENT_LABELS = {
    "front_exterior": "front of the truck",
    "rear_exterior": "rear of the truck",
    "side_exterior": "both sides of the truck",
    "tire": "tire tread and sidewall, close up",
    "chassis_suspension": "chassis, suspension, and fifth wheel",
    "dashboard_odometer": "dashboard and odometer",
    "cab_interior": "cab interior",
}


def coverage_only(db: Session, session_id: str) -> dict[str, str]:
    media = db.scalars(select(MediaItem).where(MediaItem.session_id == session_id)).all()

    coverage = {c: COVERAGE_MISSING for c in REQUIRED_COMPONENTS}
    for item in media:
        if item.component_tag not in coverage:
            continue
        if not item.accepted:
            continue
        if item.vision_status != "ok":
            # Uploaded and quality-checked, but not yet (or not successfully)
            # analyzed — don't claim coverage for evidence we don't have.
            coverage[item.component_tag] = COVERAGE_ATTENTION
            continue
        if item.quality_notes:
            coverage[item.component_tag] = COVERAGE_ATTENTION
        elif coverage[item.component_tag] != COVERAGE_ATTENTION:
            coverage[item.component_tag] = COVERAGE_CAPTURED

    return coverage
