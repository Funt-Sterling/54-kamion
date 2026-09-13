from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SessionOut(BaseModel):
    id: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceIn(BaseModel):
    """A seller declaration or user correction.

    Note what is NOT here: provenance. It was previously client-supplied
    and stored verbatim, which let any caller POST
    `provenance="observed_from_photo"` and mint photo-grade evidence with
    no photo. The server now assigns provenance, and `field` is checked
    against contract.DECLARABLE_FIELDS at the route boundary.
    """

    field: str
    value: str
    #: Optional, and restricted to what a human may assert about their own
    #: vehicle. Visual provenance is unreachable from here by construction.
    intent: Literal["seller_declared", "user_corrected"] = "seller_declared"


class EvidenceOut(BaseModel):
    field: str
    value: str
    provenance: str
    media_id: str | None

    model_config = ConfigDict(from_attributes=True)


class EvidenceParticipantOut(BaseModel):
    """One active source's claim about a field.

    Conflicts are displayed from this list, so every participant is
    visible — including the inferred candidates the previous UI dropped,
    which is why a field could read "Conflicting" with two dashes and no
    explanation of what was actually disagreeing.
    """

    provenance: str
    raw_value: str
    canonical_value: str | None = None
    display_value: str
    media_id: str | None = None
    state: str = "active"
    state_reason: str | None = None


class FieldEvidenceOut(BaseModel):
    """Resolved view of one field's evidence — see app.services.evidence.
    `status` is either a provenance name (exactly one source has a value),
    "confirmed" (multiple sources agree), "conflicting" (they disagree —
    `value` is deliberately null), or "unknown" (nothing recorded)."""

    field: str
    status: str
    value: str | None
    seller_declared: str | None
    observed_from_photo: str | None
    inferred_candidates: list[str]
    supporting_media_id: str | None

    #: Every active source for this field, so the UI can show all actual
    #: participants in a conflict rather than only the seller/photo pair.
    participants: list[EvidenceParticipantOut] = []
    #: Canonical key used for market lookup (e.g. "f-max"), distinct from
    #: the raw reading shown to the user (e.g. "F-MAX").
    canonical_value: str | None = None
    #: Superseded/disputed history, retained rather than deleted.
    superseded: list[EvidenceParticipantOut] = []


class NextPhotoOut(BaseModel):
    requested_view: str
    reason: str
    resolves: str


class ObservedViewOut(BaseModel):
    """What the photo was actually found to show."""

    view: str
    visibility: str
    usable: bool
    limitation: str | None = None


class MediaOut(BaseModel):
    id: str
    session_id: str
    kind: str
    accepted: bool
    reject_reason: str | None
    quality_notes: list
    vision_status: str

    #: What the client ASKED for. Request metadata — never a detected view.
    requested_view: str | None = None
    #: What was actually admitted from this image. Empty is meaningful: the
    #: photo was analyzed and supported nothing.
    observed_views: list[ObservedViewOut] = []
    #: camera | gallery | unknown. Reported origin, not an authenticity claim.
    capture_origin: str = "unknown"
    #: Set when the client asked for one view and a different one arrived,
    #: so the UI can say so without pretending the request was satisfied.
    requested_view_satisfied: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class FindingOut(BaseModel):
    component: str
    observation: str
    media_id: str | None
    visibility: str
    recommended_action: str

    model_config = ConfigDict(from_attributes=True)


class ConcernOut(BaseModel):
    id: str
    component: str
    severity: str  # info | clarify | structural
    description: str
    status: str  # open | resolved | review_required

    model_config = ConfigDict(from_attributes=True)


class SessionDetailOut(BaseModel):
    id: str
    status: str
    coverage: dict[str, str]
    evidence: list[FieldEvidenceOut]
    next_photo: NextPhotoOut | None
    findings: list[FindingOut]
    latest_appraisal_id: str | None

    #: The gate's own verdict. The UI previously inferred readiness from
    #: `next_photo == null`, which is a different question — the gate can
    #: be blocked while having no photo that would help, and can be ready
    #: while still suggesting an optional view.
    gate_status: str = "needs_evidence"
    gate_reasons: list[str] = []
    #: Bumped on every material evidence change; an appraisal computed at a
    #: lower revision is stale.
    evidence_revision: int = 1
    #: Open questions with their own lifecycle, kept apart from findings.
    concerns: list[ConcernOut] = []
    #: Things that could NOT be assessed. Never merged into findings.
    limitations: list[str] = []


class ComparableOut(BaseModel):
    listing_id: str
    similarity_weight: float
    make: str
    model: str
    year: int
    mileage_km: int
    axle_config: str
    price: float
    currency: str
    source_url: str


class AppraisalOut(BaseModel):
    id: str
    session_id: str
    version: int
    status: str
    price_low: float | None
    price_mid: float | None
    price_high: float | None
    currency: str
    comparable_count: int
    comparables: list[ComparableOut]
    matched_attributes: dict[str, str]
    findings: list[FindingOut]
    reasons: list[str]
    next_photo: NextPhotoOut | None

    #: Revision this range was computed from. The client compares it with
    #: the session's current revision; if they differ the range is stale
    #: and must not be shown as the current answer.
    evidence_revision: int = 0
    #: How the range was produced and what it does NOT mean. Always
    #: populated for a priced result.
    method_note: str = ""
    #: e.g. "source asking prices; VAT treatment not stated".
    tax_note: str = ""
    #: Number of distinct vehicle groups behind the range, after
    #: deduplicating reposts — not a raw row count.
    distinct_vehicle_groups: int = 0
