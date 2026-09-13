import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id(prefix: str):
    return lambda: f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InspectionSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("sess"))
    status: Mapped[str] = mapped_column(String, default="collecting_evidence")
    #: Bumped on every material evidence change (admitted observation,
    #: declaration, supersession, concern transition). An appraisal records
    #: the revision it was computed from, so a stale range can be detected
    #: instead of being redisplayed as current.
    evidence_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    media_items: Mapped[list["MediaItem"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    findings: Mapped[list["Finding"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    appraisals: Mapped[list["Appraisal"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    observed_views: Mapped[list["ObservedView"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    inference_runs: Mapped[list["InferenceRun"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    concerns: Mapped[list["Concern"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("media"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))

    kind: Mapped[str] = mapped_column(String)  # photo | video | frame
    source: Mapped[str] = mapped_column(String)  # captured | imported
    file_path: Mapped[str] = mapped_column(String)
    #: LEGACY. Historically this held the client's requested view and was
    #: then read back as if it were a detected one. It is retained so old
    #: rows stay readable and the migration is reversible, but nothing may
    #: derive coverage from it — see ObservedView.
    component_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    #: What the client *asked for*. Request metadata only: used after
    #: analysis to explain a mismatch, never to credit a view.
    client_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Where the bytes came from (camera | gallery | unknown). Reported, not
    #: verified — this is not an authenticity claim.
    capture_origin: Mapped[str] = mapped_column(String, default="unknown")
    #: SHA-256 of the image bytes, tying a media row to its inference runs.
    image_sha256: Mapped[str | None] = mapped_column(String, nullable=True)

    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    reject_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_notes: Mapped[list] = mapped_column(JSON, default=list)
    perceptual_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Whether the vision model call for this photo actually completed.
    # "pending" until analysis runs, "ok" once structured evidence was
    # extracted, "failed" on a recoverable API/parsing error (Milestone 3 —
    # a failure must never silently masquerade as a successful analysis).
    vision_status: Mapped[str] = mapped_column(String, default="pending")
    structural_damage_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    tire_concern_noted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="media_items")


class EvidenceRecord(Base):
    """One provenanced fact about the vehicle. Never overwritten or deleted —
    conflicting facts about the same field simply accumulate, and
    app.services.evidence.resolve_field() decides (at read time) whether
    they agree, conflict, or leave the field unknown. This is what lets the
    evidence gate say "seller declared 250,000 km but the dashboard shows
    650,000 km" instead of silently picking one."""

    __tablename__ = "evidence_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("ev"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))

    field: Mapped[str] = mapped_column(String)  # e.g. "mileage_km", "model_family", "axle_config", "year"
    #: Exactly what the source said, preserved verbatim ("F-MAX", "365 000").
    value: Mapped[str] = mapped_column(String)
    #: Comparison/lookup key from the shared registry ("f-max"). Distinct
    #: from `value` so canonicalization never destroys the original reading.
    canonical_value: Mapped[str | None] = mapped_column(String, nullable=True)
    #: What a human should see. Usually the raw reading.
    display_value: Mapped[str | None] = mapped_column(String, nullable=True)
    provenance: Mapped[str] = mapped_column(String)  # see contract.ALL_PROVENANCES
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_items.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("inference_runs.id"), nullable=True)

    #: active | superseded | disputed. Observations are immutable; a newer
    #: stronger observation supersedes rather than overwrites, so the
    #: history of a contradiction survives.
    state: Mapped[str] = mapped_column(String, default="active")
    superseded_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Why this record is in its current state, for display and audit.
    state_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Supporting detail for an admitted reading: unit, readability, region,
    #: which view backed it. Empty for declarations.
    basis: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="evidence_records")


class ObservedView(Base):
    """What a photo was actually found to show.

    The single thing coverage may be computed from. One image can admit
    several views (a side profile showing axles and a tire), and a view is
    only credited when the admission validator accepted it as visible and
    usable — never because an upload succeeded, a user tagged it, or the
    server requested it.
    """

    __tablename__ = "observed_views"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("view"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("inference_runs.id"), nullable=True)

    view: Mapped[str] = mapped_column(String)  # contract.ALL_VIEWS
    visibility: Mapped[str] = mapped_column(String)  # visible | absent | unclear
    usable: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Free-text limitation from the proposal ("lens flare across cab").
    limitation: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="observed_views")


class InferenceRun(Base):
    """Bounded private record of one model call.

    Exists so any admitted claim can be traced back to the exact request
    that produced it. Deliberately stores the returned proposal only — no
    hidden reasoning is requested or retained, and the API key is never
    written here.
    """

    __tablename__ = "inference_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("run"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id"))

    model_id: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)
    image_sha256: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Truncated raw proposal text, for diagnosis.
    raw_proposal: Mapped[str | None] = mapped_column(String, nullable=True)
    admitted: Mapped[list] = mapped_column(JSON, default=list)
    rejected: Mapped[list] = mapped_column(JSON, default=list)  # [{claim, reason}]
    #: Image-level things that could not be assessed ("chassis hidden by mud").
    #: Kept apart from findings so "not seen" never reads as "fine".
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)  # token counts
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: None on success; otherwise timeout | transport | schema | semantic.
    failure_type: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="inference_runs")


class Concern(Base):
    """An open question a photo raised, with its own resolution lifecycle.

    Separate from Finding because a concern must be resolvable by the photo
    that actually addresses it — and must NOT be dismissed by an unrelated
    clear photo elsewhere on the truck.
    """

    __tablename__ = "concerns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("cn"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_items.id"), nullable=True)

    component: Mapped[str] = mapped_column(String)
    #: info | clarify | structural — structural blocks a numeric quote.
    severity: Mapped[str] = mapped_column(String, default="info")
    description: Mapped[str] = mapped_column(String)
    #: open | resolved | review_required
    status: Mapped[str] = mapped_column(String, default="open")
    resolved_by_media_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="concerns")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("find"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_items.id"), nullable=True)

    component: Mapped[str] = mapped_column(String)
    observation: Mapped[str] = mapped_column(String)
    visibility: Mapped[str] = mapped_column(String, default="clear")  # clear | partial | obstructed
    recommended_action: Mapped[str] = mapped_column(String, default="none")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="findings")


class Listing(Base):
    """One market comparable, collected by data_pipeline/ and loaded into this table."""

    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("lst"))

    source_url: Mapped[str] = mapped_column(String)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    listing_date: Mapped[str | None] = mapped_column(String, nullable=True)

    location: Mapped[str] = mapped_column(String)  # e.g. "Istanbul, TR"
    country: Mapped[str] = mapped_column(String, default="TR")

    make: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    model_family: Mapped[str] = mapped_column(String)  # coarser grouping used for comparable filtering
    category: Mapped[str] = mapped_column(String, default="tractor_unit")
    year: Mapped[int] = mapped_column(Integer)
    mileage_km: Mapped[int] = mapped_column(Integer)
    axle_config: Mapped[str] = mapped_column(String)  # e.g. "4x2", "6x2", "6x4"

    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="TRY")
    vat_basis: Mapped[str] = mapped_column(String)  # e.g. "vat_included", "vat_excluded", "vat_exempt"

    image_refs: Mapped[list] = mapped_column(JSON, default=list)
    partition: Mapped[str] = mapped_column(String, default="dev")  # dev | calibration | test


class Appraisal(Base):
    __tablename__ = "appraisals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("appr"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String)

    price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="TRY")

    comparable_count: Mapped[int] = mapped_column(Integer, default=0)
    comparables: Mapped[list] = mapped_column(JSON, default=list)  # enriched comparable records, see routers/appraisals.py
    matched_attributes: Mapped[dict] = mapped_column(JSON, default=dict)  # the VehicleSpec actually used to query
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    #: Session evidence revision this range was computed from. If the
    #: session has moved on, the range is stale and must not be presented
    #: as the current answer.
    evidence_revision: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="appraisals")
