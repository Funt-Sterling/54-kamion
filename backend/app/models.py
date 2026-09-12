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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    media_items: Mapped[list["MediaItem"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    findings: Mapped[list["Finding"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    appraisals: Mapped[list["Appraisal"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("media"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))

    kind: Mapped[str] = mapped_column(String)  # photo | video | frame
    source: Mapped[str] = mapped_column(String)  # captured | imported
    file_path: Mapped[str] = mapped_column(String)
    component_tag: Mapped[str | None] = mapped_column(String, nullable=True)

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
    value: Mapped[str] = mapped_column(String)
    provenance: Mapped[str] = mapped_column(String)  # seller_declared | observed_from_photo | inferred_candidate
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_items.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="evidence_records")


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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[InspectionSession] = relationship(back_populates="appraisals")
