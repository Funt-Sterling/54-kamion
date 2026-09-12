from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionOut(BaseModel):
    id: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DetailIn(BaseModel):
    field: str
    value: str
    source: str = "seller_declared"


class DetailOut(BaseModel):
    field: str
    value: str
    source: str

    model_config = ConfigDict(from_attributes=True)


class MediaOut(BaseModel):
    id: str
    session_id: str
    kind: str
    accepted: bool
    reject_reason: str | None
    component_tag: str | None
    quality_notes: list

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
    next_instruction: str | None
    declared_details: list[DetailOut]
    findings: list[FindingOut]
    latest_appraisal_id: str | None


class ComparableOut(BaseModel):
    listing_id: str
    similarity_weight: float


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
    findings: list[FindingOut]
    reasons: list[str]

    model_config = ConfigDict(from_attributes=True)
