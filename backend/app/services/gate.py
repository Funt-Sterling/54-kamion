"""Deterministic evidence gate and next-best-photo selection.

Implements the conservative v1 policy in audit section E ("Exact v1
requirements for status=priced"). A `ready_to_price` verdict requires:

1. an admitted whole-truck observation that this is a tractor unit — close-ups
   alone never establish category;
2. a model family grounded in readable text (an observation), not a visual
   candidate and not a seller claim, consistent with any observed make;
3. a valid model year, which may be seller-declared;
4. mileage from a readable TOTAL odometer reading — a declaration alone is
   saved, but does not unlock the primary range;
5. a configuration that is either read from the vehicle, or declared AND
   compatible with an admitted visible axle count (and the dependency is
   disclosed, e.g. "assuming seller-declared 4x2");
6. no unresolved conflict on any of those fields;
7. no open or review-required structural concern (-> inspection_required),
   and no open concern needing clarification (-> needs_evidence).

Market sufficiency (requirement 7 in the audit numbering: five distinct
vehicle groups, hard TR/TRY/make/family/configuration filters) is checked by
the appraisal route after this gate passes, because it depends on the
listing table rather than on the inspection.

Next-photo order is fixed and rule-based: category, identity, configuration,
mileage, year, then an open condition concern. There is no learned or
probabilistic information-gain policy here, and none is implied. When the
blocker is something no photo can fix — an unsupported vehicle, a physical
inspection, a declaration only the seller can make — no photo is requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..constants import REQUIRED_COMPONENTS
from ..contract import (
    CATEGORY_TRACTOR_UNIT,
    GATE_INSPECTION_REQUIRED,
    GATE_NEEDS_EVIDENCE,
    GATE_READY,
    GATE_UNSUPPORTED,
    PROV_OBSERVED,
    SEVERITY_CLARIFY,
    SEVERITY_STRUCTURAL,
)
from .canonical import canonicalize, family_is_consistent_with_make
from .evidence import FieldEvidence, get_field_evidence
from .session_state import COMPONENT_LABELS, coverage_only

# Re-exported names the appraisal route already uses.
STATUS_UNSUPPORTED = GATE_UNSUPPORTED
STATUS_NEEDS_EVIDENCE = GATE_NEEDS_EVIDENCE
STATUS_INSPECTION_REQUIRED = GATE_INSPECTION_REQUIRED
STATUS_READY = GATE_READY

IDENTITY_FIELDS = ["model_family", "axle_config", "year"]
MILEAGE_FIELD = "mileage_km"

#: Axles implied by each supported drive configuration. A visible count can
#: be *compatible* with a declaration; it can never select between 6x2 and
#: 6x4, which share a count.
AXLES_FOR_CONFIGURATION: dict[str, int] = {"4x2": 2, "6x2": 3, "6x4": 3, "8x4": 4}


@dataclass
class NextPhotoRequest:
    requested_view: str
    reason: str
    resolves: str


@dataclass
class GateDecision:
    status: str
    reasons: list[str]
    next_photo: NextPhotoRequest | None
    identity: dict[str, FieldEvidence]
    mileage: FieldEvidence
    #: Every resolved field the decision looked at, for the API.
    fields: dict[str, FieldEvidence] = dc_field(default_factory=dict)
    #: Human-readable dependencies of a ready verdict, shown with the range
    #: (e.g. "assuming seller-declared 4x2 (2 axles visible)").
    assumptions: list[str] = dc_field(default_factory=list)


# --- evidence helpers --------------------------------------------------------


def _observed_values(evidence: FieldEvidence) -> list[dict]:
    return [p for p in evidence.participants if p["provenance"] == PROV_OBSERVED]


def _is_conflicting(evidence: FieldEvidence) -> bool:
    return evidence.is_conflicting


def _describe_participants(evidence: FieldEvidence) -> str:
    """Name every active source, so a conflict explanation shows what is
    actually disagreeing rather than only the seller/photo pair."""
    labels = {
        "observed_from_photo": "photo",
        "seller_declared": "seller",
        "user_corrected": "user correction",
        "inferred_candidate": "visual guess",
    }
    parts = [f"{labels.get(p['provenance'], p['provenance'])}: {p['display_value']}" for p in evidence.participants]
    return "; ".join(parts) if parts else "no sources"


def _label(field: str) -> str:
    return {
        "vehicle_category": "vehicle type",
        "make": "make",
        "model_family": "model family",
        "axle_config": "axle configuration",
        "visible_axle_count": "visible axle count",
        "year": "model year",
        "mileage_km": "mileage",
    }.get(field, field)


def _rejected_categories(db: Session, session_id: str) -> list[str]:
    rows = db.scalars(
        select(models.MediaItem.reject_reason).where(
            models.MediaItem.session_id == session_id,
            models.MediaItem.reject_reason.isnot(None),
        )
    ).all()
    found = []
    for reason in rows:
        if reason == "no_vehicle_detected":
            found.append("non-vehicle")
        elif reason and reason.startswith("not_a_tractor_unit"):
            detail = reason.split(":", 1)[1] if ":" in reason else "other vehicle"
            found.append(detail.replace("_", " "))
    return found


def _open_concerns(db: Session, session_id: str) -> list[models.Concern]:
    return list(
        db.scalars(
            select(models.Concern).where(
                models.Concern.session_id == session_id,
                models.Concern.status.in_(("open", "review_required")),
            )
        ).all()
    )


def _decision(
    status: str,
    reasons: list[str],
    next_photo: NextPhotoRequest | None,
    fields: dict[str, FieldEvidence],
    assumptions: list[str] | None = None,
) -> GateDecision:
    return GateDecision(
        status=status,
        reasons=reasons,
        next_photo=next_photo,
        identity={name: fields[name] for name in IDENTITY_FIELDS},
        mileage=fields[MILEAGE_FIELD],
        fields=fields,
        assumptions=assumptions or [],
    )


# --- photo requests ----------------------------------------------------------

_WHOLE_TRUCK = NextPhotoRequest(
    requested_view="A clear front three-quarter photo showing the whole tractor unit",
    reason="Vehicle type has not been established from a whole-vehicle photo. Close-ups show parts, not what they belong to.",
    resolves="vehicle_identity",
)
_BADGE = NextPhotoRequest(
    requested_view="A close, straight-on photo of the model badge on the grille or cab",
    reason="The model family must be read from the truck, not guessed from its shape; it selects which listings are comparable.",
    resolves="model_family",
)
_SIDE = NextPhotoRequest(
    requested_view="A full side-profile photo showing every axle and wheel",
    reason="The visible axle count is needed to check the stated configuration against the truck.",
    resolves="axle_config",
)
_ODOMETER = NextPhotoRequest(
    requested_view="A sharp, straight-on photo of the odometer with the total distance and its unit fully visible",
    reason="The price range needs a readable total odometer; a trip meter or a typed number is not enough.",
    resolves="mileage_km",
)
_YEAR = NextPhotoRequest(
    requested_view="A photo of the manufacturer plate or registration document showing the model year — or enter the year",
    reason="The model year places the truck among comparable listings of similar age.",
    resolves="year",
)


# --- the gate ----------------------------------------------------------------


def evaluate_gate(db: Session, session_id: str) -> GateDecision:
    names = ["vehicle_category", "make", "model_family", "axle_config", "visible_axle_count", "year", MILEAGE_FIELD]
    fields = {name: get_field_evidence(db, session_id, name) for name in names}

    # 0. Non-current analysis never authorises a quote.
    pending = db.scalar(
        select(func.count())
        .select_from(models.MediaItem)
        .where(
            models.MediaItem.session_id == session_id,
            models.MediaItem.accepted.is_(True),
            models.MediaItem.kind.in_(("photo", "frame")),
            models.MediaItem.vision_status == "pending",
        )
    )
    if pending:
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"{pending} photo(s) are still being analysed; no range is given until the evidence is current."],
            None,
            fields,
        )

    # 1. Vehicle category from a whole-truck observation.
    category = fields["vehicle_category"]
    tractor_observed = any(
        (p["canonical_value"] or p["raw_value"]) == CATEGORY_TRACTOR_UNIT for p in _observed_values(category)
    )
    if not tractor_observed:
        rejected = _rejected_categories(db, session_id)
        if rejected:
            kinds = ", ".join(sorted(set(rejected)))
            return _decision(
                GATE_UNSUPPORTED,
                [f"The uploaded photos show {kinds}, not a tractor unit. This system appraises tractor units only."],
                None,
                fields,
            )
        return _decision(
            GATE_NEEDS_EVIDENCE,
            ["No photo has yet shown the whole vehicle clearly enough to establish that it is a tractor unit."],
            _WHOLE_TRUCK,
            fields,
        )

    # 2. Identity: make and model family.
    make = fields["make"]
    if _is_conflicting(make):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The make is contradicted between sources ({_describe_participants(make)})."],
            _BADGE,
            fields,
        )

    family = fields["model_family"]
    if _is_conflicting(family):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The model family is contradicted between sources ({_describe_participants(family)}). Nothing is chosen automatically."],
            _BADGE,
            fields,
        )
    if not family.has_observation:
        if family.participants:
            return _decision(
                GATE_NEEDS_EVIDENCE,
                [
                    f"The model family is only a {_describe_participants(family)}. "
                    "A readable badge is needed before a price population can be chosen."
                ],
                _BADGE,
                fields,
            )
        return _decision(GATE_NEEDS_EVIDENCE, ["The model family has not been read from the truck."], _BADGE, fields)

    family_key = canonicalize("model_family", family.canonical_value or family.value or "").canonical
    if family_key is None:
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The model family {family.value!r} is not a recognised family, so no comparable population can be chosen."],
            _BADGE,
            fields,
        )
    make_key = canonicalize("make", make.canonical_value or make.value or "").canonical if make.value else None
    if not family_is_consistent_with_make(family_key, make_key):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The make ({make.value}) and model family ({family.value}) belong to different manufacturers."],
            _BADGE,
            fields,
        )

    # 3. Configuration: read, or declared and compatible with a visible count.
    assumptions: list[str] = []
    configuration = fields["axle_config"]
    axle_count = fields["visible_axle_count"]

    if _is_conflicting(axle_count):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"Photos disagree on the number of visible axles ({_describe_participants(axle_count)})."],
            _SIDE,
            fields,
        )
    if _is_conflicting(configuration):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The axle configuration is contradicted between sources ({_describe_participants(configuration)})."],
            None,
            fields,
        )

    config_key = canonicalize("axle_config", configuration.canonical_value or configuration.value or "").canonical
    count_value = axle_count.canonical_value or axle_count.value

    if configuration.value is None:
        if count_value is not None:
            return _decision(
                GATE_NEEDS_EVIDENCE,
                [
                    f"{count_value} axles are visible, which does not determine which axles are driven. "
                    "Enter the configuration (for example 6x2 or 6x4)."
                ],
                None,
                fields,
            )
        return _decision(
            GATE_NEEDS_EVIDENCE,
            ["The axle configuration is not known. A side profile shows the axle count; the configuration itself is entered by the seller."],
            _SIDE,
            fields,
        )
    if config_key is None:
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The configuration {configuration.value!r} is not recognised."],
            None,
            fields,
        )

    if not configuration.has_observation:
        expected = AXLES_FOR_CONFIGURATION.get(config_key)
        if count_value is None:
            return _decision(
                GATE_NEEDS_EVIDENCE,
                [f"The seller-declared {config_key} has not been checked against the truck: no side profile shows its axles yet."],
                _SIDE,
                fields,
            )
        if expected is not None and str(expected) != str(count_value):
            return _decision(
                GATE_NEEDS_EVIDENCE,
                [f"The seller declared {config_key} ({expected} axles), but {count_value} axles are visible."],
                _SIDE,
                fields,
            )
        assumptions.append(f"assuming seller-declared {config_key} ({count_value} axles visible)")

    # 4. Mileage from a readable total odometer.
    mileage = fields[MILEAGE_FIELD]
    if _is_conflicting(mileage):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [
                f"Mileage sources disagree ({_describe_participants(mileage)}). "
                "Both are kept; neither is chosen."
            ],
            _ODOMETER,
            fields,
        )
    if not mileage.has_observation:
        if mileage.participants:
            return _decision(
                GATE_NEEDS_EVIDENCE,
                [
                    "The declared mileage is saved, but the price range needs a readable total odometer photo. "
                    "A typed number is not verified against the truck."
                ],
                _ODOMETER,
                fields,
            )
        return _decision(GATE_NEEDS_EVIDENCE, ["Mileage has not been read from an odometer yet."], _ODOMETER, fields)
    if mileage.status == "sources_agree":
        assumptions.append("seller and odometer photo agree on mileage (displayed reading, not verified lifetime mileage)")

    # 5. Year — may be declared.
    year = fields["year"]
    if _is_conflicting(year):
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"The model year is contradicted between sources ({_describe_participants(year)})."],
            _YEAR,
            fields,
        )
    if year.value is None:
        return _decision(GATE_NEEDS_EVIDENCE, ["The model year is not known."], _YEAR, fields)
    if not year.has_observation:
        assumptions.append(f"model year {year.value} as declared by the seller")

    # 6. Condition concerns, after the inputs a comparable lookup needs.
    concerns = _open_concerns(db, session_id)
    structural = [c for c in concerns if c.severity == SEVERITY_STRUCTURAL]
    if structural:
        components = ", ".join(sorted({c.component for c in structural}))
        return _decision(
            GATE_INSPECTION_REQUIRED,
            [
                f"A possible structural issue was noted ({components}). An individual price is withheld; "
                "comparable listings are shown only as market context. An in-person inspection is recommended."
            ],
            None,
            fields,
            assumptions,
        )
    clarify = [c for c in concerns if c.severity == SEVERITY_CLARIFY and c.status == "open"]
    if clarify:
        concern = clarify[0]
        return _decision(
            GATE_NEEDS_EVIDENCE,
            [f"A visible concern needs a clearer look before pricing: {concern.component} — {concern.description}"],
            NextPhotoRequest(
                requested_view=f"A closer, well-lit photo of the {concern.component}",
                reason="A clearer view shows whether the noted issue is real and how far it extends.",
                resolves=concern.component,
            ),
            fields,
            assumptions,
        )

    return _decision(GATE_READY, [], None, fields, assumptions)


def next_best_photo(db: Session, session_id: str, decision: GateDecision | None = None) -> NextPhotoRequest | None:
    """One highest-value photo, or None.

    A blocking request from the gate wins. When the gate is ready, suggest
    a missing view to complete the report. When the blocker is something a
    photo cannot fix — unsupported vehicle, inspection, a declaration — ask
    for nothing rather than inventing a request.
    """
    decision = decision or evaluate_gate(db, session_id)
    if decision.status != GATE_READY:
        return decision.next_photo

    coverage = coverage_only(db, session_id)
    for component in REQUIRED_COMPONENTS:
        if coverage.get(component) == "missing":
            return NextPhotoRequest(
                requested_view=f"A clear photo of the {COMPONENT_LABELS[component]}",
                reason="Optional: this view has not been photographed, so its condition is still unknown in the report.",
                resolves=component,
            )
    return None
