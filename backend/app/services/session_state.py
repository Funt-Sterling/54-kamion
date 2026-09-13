"""Per-component capture coverage.

Coverage answers exactly one question: *has this component actually been
photographed usefully yet?* The audit's first finding (section B.1) was
that it answered a different one. `MediaItem.component_tag` holds what the
**client asked for**; crediting it meant a request for the front answered
with a tire close-up left the front "captured" and the tires "missing".

So the only input here is `ObservedView` — rows the admission validator
minted after looking at the image. A row counts when it is

    state == "active", visibility == "visible", usable is True

and nothing else does. Not an accepted upload, not a successful vision
call, not a user's tag, not the server's own request.

View -> UI component mapping
----------------------------
The evidence model has nine views; the UI checklist has seven components.
The mapping is explicit and one-directional:

    front     -> front_exterior
    rear      -> rear_exterior
    side      -> side_exterior
    tire      -> tire
    chassis   -> chassis_suspension
    cab       -> cab_interior
    dashboard -> dashboard_odometer      (credits it)
    odometer  -> dashboard_odometer      (ATTENTION only — see below)
    badge     -> (no component)

`dashboard` and `odometer` are distinct observations and are kept
distinct. A tight odometer crop is excellent odometer evidence and says
nothing about the wider dashboard, so it may never mark
`dashboard_odometer` captured. It raises a missing component to
`attention` — enough to stop the capture flow demanding the same photo
forever, never enough to claim a dashboard view we do not have — and it
never downgrades a dashboard view that was already credited.

`badge` maps to no checklist component on purpose: a badge crop is
identity evidence, and crediting it as "front exterior" is the same
category error the audit flagged.

Sticky-attention fix (audit B.6): component state is decided by the
*latest* credited observation, in `created_at` order. A clear retake
restores `captured`; previously an early flagged photo outranked every
later good one and the session could never recover.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import COVERAGE_ATTENTION, COVERAGE_CAPTURED, COVERAGE_MISSING, REQUIRED_COMPONENTS
from ..contract import (
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
    VISIBILITY_VISIBLE,
)
from ..models import ObservedView

COMPONENT_LABELS = {
    "front_exterior": "front of the truck",
    "rear_exterior": "rear of the truck",
    "side_exterior": "both sides of the truck",
    "tire": "tire tread and sidewall, close up",
    "chassis_suspension": "chassis, suspension, and fifth wheel",
    "dashboard_odometer": "dashboard and odometer",
    "cab_interior": "cab interior",
}

#: contract view -> UI checklist component. None means the view is real
#: evidence that intentionally credits no checklist row.
VIEW_TO_COMPONENT: dict[str, str | None] = {
    VIEW_FRONT: "front_exterior",
    VIEW_REAR: "rear_exterior",
    VIEW_SIDE: "side_exterior",
    VIEW_TIRE: "tire",
    VIEW_CHASSIS: "chassis_suspension",
    VIEW_CAB: "cab_interior",
    VIEW_DASHBOARD: "dashboard_odometer",
    VIEW_ODOMETER: "dashboard_odometer",
    VIEW_BADGE: None,
}

#: Views that support a component without being a full view of it.
PARTIAL_CREDIT_VIEWS = frozenset({VIEW_ODOMETER})


def _credited_views(db: Session, session_id: str) -> list[ObservedView]:
    """Active, visible, usable observations, oldest first."""
    rows = db.scalars(select(ObservedView).where(ObservedView.session_id == session_id)).all()
    credited = [
        row
        for row in rows
        if (row.state or STATE_ACTIVE) == STATE_ACTIVE
        and row.visibility == VISIBILITY_VISIBLE
        and bool(row.usable)
    ]
    return sorted(credited, key=lambda row: (row.created_at is not None, row.created_at, row.id or ""))


def coverage_only(db: Session, session_id: str) -> dict[str, str]:
    coverage = {component: COVERAGE_MISSING for component in REQUIRED_COMPONENTS}

    for row in _credited_views(db, session_id):
        component = VIEW_TO_COMPONENT.get(row.view)
        if component is None or component not in coverage:
            continue

        if row.view in PARTIAL_CREDIT_VIEWS:
            # Supports the component without being a full view of it, and
            # must never pull an already-credited component back down.
            if coverage[component] == COVERAGE_MISSING:
                coverage[component] = COVERAGE_ATTENTION
            continue

        # Latest credited observation decides — a clear retake heals an
        # earlier flagged one instead of being outranked by it.
        coverage[component] = COVERAGE_ATTENTION if row.limitation else COVERAGE_CAPTURED

    return coverage


def observed_view_states(db: Session, session_id: str) -> dict[str, bool]:
    """Per-contract-view truth, before the UI's coarser checklist folds
    `dashboard` and `odometer` together. The gate uses this when it needs
    to know which of the two it actually has."""
    return {row.view: True for row in _credited_views(db, session_id)}


def has_credited_view(db: Session, session_id: str, view: str) -> bool:
    return view in observed_view_states(db, session_id)
