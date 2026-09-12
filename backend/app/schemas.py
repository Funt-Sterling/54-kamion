from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionOut(BaseModel):
    id: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceIn(BaseModel):
    field: str
    value: str
    provenance: str = "seller_declared"


class EvidenceOut(BaseModel):
    field: str
    value: str
    provenance: str
    media_id: str | None

    model_config = ConfigDict(from_attributes=True)


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


class NextPhotoOut(BaseModel):
    requested_view: str
    reason: str
    resolves: str


class MediaOut(BaseModel):
    id: str
    session_id: str
    kind: str
    accepted: bool
    reject_reason: str | None
    component_tag: str | None
    quality_notes: list
    vision_status: str

    model_config = ConfigDict(from_attributes=True)


class FindingOut(BaseModel):
    component: str
    observation: str
    media_id: str | None
    visibility: str
    recommended_action: str

    model_config = ConfigDict(from_attributes=True)


class SessionDetailOut(BaseModel):
    id: str
    status: str
    coverage: dict[str, str]
    evidence: list[FieldEvidenceOut]
    next_photo: NextPhotoOut | None
    findings: list[FindingOut]
    latest_appraisal_id: str | None


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
