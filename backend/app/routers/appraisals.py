from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..contract import (
    MIN_DISTINCT_VEHICLE_GROUPS,
    SUPPORTED_CATEGORY,
    SUPPORTED_COUNTRY,
    VAT_UNSTATED_LABEL,
)
from ..db import get_db
from ..services import gate, pricing
from ..services.evidence import get_field_evidence

router = APIRouter(prefix="/sessions", tags=["appraisals"])

#: Rows fetched before repost deduplication. Larger than the number shown so
#: that five reposts of one truck cannot crowd out four other vehicles.
_CANDIDATE_POOL = 200


def _get_session_or_404(db: Session, session_id: str) -> models.InspectionSession:
    session = db.get(models.InspectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _comparable_dict(listing: models.Listing, weight: float) -> dict:
    return {
        "listing_id": listing.id,
        "similarity_weight": round(weight, 4),
        "make": listing.make,
        "model": listing.model,
        "year": listing.year,
        "mileage_km": listing.mileage_km,
        "axle_config": listing.axle_config,
        "price": listing.price,
        "currency": listing.currency,
        "source_url": listing.source_url,
    }


def _digits(value: str | None) -> int:
    """Stored canonical numbers are digit strings; anything else is refused
    rather than coerced (no float parsing, no separator guessing)."""
    compact = "".join((value or "").split())
    if not compact.isdigit():
        raise ValueError(f"{value!r} is not a whole number")
    return int(compact)


def _save(
    db: Session,
    session: models.InspectionSession,
    *,
    status: str,
    reasons: list[str],
    revision: int,
    comparables_json: list[dict] | None = None,
    matched_attributes: dict[str, str] | None = None,
    price: tuple[float | None, float | None, float | None] = (None, None, None),
) -> models.Appraisal:
    version = len(db.scalars(select(models.Appraisal.id).where(models.Appraisal.session_id == session.id)).all()) + 1
    low, mid, high = price
    appraisal = models.Appraisal(
        session_id=session.id,
        version=version,
        status=status,
        price_low=low,
        price_mid=mid,
        price_high=high,
        comparable_count=len(comparables_json or []),
        comparables=comparables_json or [],
        matched_attributes=matched_attributes or {},
        reasons=reasons,
        evidence_revision=revision,
    )
    db.add(appraisal)
    db.commit()
    db.refresh(appraisal)
    return appraisal


def _tax_note(listings: list[models.Listing]) -> str:
    bases = {listing.vat_basis for listing in listings}
    if not bases or bases == {"unknown"}:
        return VAT_UNSTATED_LABEL
    return f"source asking prices; listings state VAT basis: {', '.join(sorted(bases))}"


def _to_out(
    db: Session,
    appraisal: models.Appraisal,
    next_photo: gate.NextPhotoRequest | None,
    *,
    method_note: str = "",
    tax_note: str = "",
    distinct_groups: int = 0,
) -> schemas.AppraisalOut:
    findings = db.scalars(select(models.Finding).where(models.Finding.session_id == appraisal.session_id)).all()
    return schemas.AppraisalOut(
        id=appraisal.id,
        session_id=appraisal.session_id,
        version=appraisal.version,
        status=appraisal.status,
        price_low=appraisal.price_low,
        price_mid=appraisal.price_mid,
        price_high=appraisal.price_high,
        currency=appraisal.currency,
        comparable_count=appraisal.comparable_count,
        comparables=[schemas.ComparableOut(**c) for c in appraisal.comparables],
        matched_attributes=appraisal.matched_attributes,
        findings=[schemas.FindingOut.model_validate(f) for f in findings],
        reasons=appraisal.reasons,
        next_photo=schemas.NextPhotoOut(**vars(next_photo)) if next_photo else None,
        evidence_revision=appraisal.evidence_revision,
        method_note=method_note,
        tax_note=tax_note,
        distinct_vehicle_groups=distinct_groups,
    )


@router.post("/{session_id}/appraisals", response_model=schemas.AppraisalOut, status_code=201)
def create_appraisal(session_id: str, db: Session = Depends(get_db)):
    """The gate decides whether pricing may run at all; the vision model
    never sees or returns a price. Everything below reads ONE evidence
    snapshot, recorded on the appraisal as `evidence_revision`, so the client
    can tell when a later upload or declaration has made this range stale.
    """
    session = _get_session_or_404(db, session_id)
    settings = get_settings()
    revision = session.evidence_revision or 1

    decision = gate.evaluate_gate(db, session_id)
    reasons_with_assumptions = [*decision.reasons, *(f"Assumption: {a}" for a in decision.assumptions)]

    if decision.status in (gate.STATUS_UNSUPPORTED, gate.STATUS_NEEDS_EVIDENCE):
        appraisal = _save(db, session, status=decision.status, reasons=decision.reasons, revision=revision)
        return _to_out(db, appraisal, decision.next_photo)

    fields = decision.fields
    vat = get_field_evidence(db, session_id, "vat_basis")
    try:
        spec = pricing.VehicleSpec(
            category=SUPPORTED_CATEGORY,
            country=SUPPORTED_COUNTRY,
            # Canonical keys, from the shared registry — never the raw text
            # ("F-MAX" is shown to the user, "f-max" queries the market).
            model_family=fields["model_family"].canonical_value,
            axle_config=fields["axle_config"].canonical_value,
            year=_digits(fields["year"].canonical_value or fields["year"].value),
            mileage_km=_digits(fields[gate.MILEAGE_FIELD].canonical_value or fields[gate.MILEAGE_FIELD].value),
            # Undeclared VAT only matches listings whose VAT is equally
            # unstated; the label below says so instead of assuming a basis.
            vat_basis=vat.canonical_value or "unknown",
            make_canonical=fields["make"].canonical_value,
        )
        if not spec.model_family or not spec.axle_config:
            raise ValueError("model family and configuration need canonical values")
    except (TypeError, ValueError) as exc:
        appraisal = _save(
            db, session, status=gate.STATUS_NEEDS_EVIDENCE,
            reasons=[f"Could not form pricing inputs from the current evidence: {exc}"], revision=revision,
        )
        return _to_out(db, appraisal, None)

    matched_attributes = {
        "make": spec.make_canonical or "not read",
        "model_family": spec.model_family,
        "axle_config": spec.axle_config,
        "year": str(spec.year),
        "mileage_km": str(spec.mileage_km),
        "vat_basis": spec.vat_basis,
        "country": spec.country,
        "currency": "TRY",
    }

    scored = pricing.find_comparables(db, spec, max_comparables=_CANDIDATE_POOL)
    distinct = pricing.deduplicate_by_vehicle(scored)[: settings.max_comparables]
    distinct_listings = [listing for listing, _ in distinct]
    tax_note = _tax_note(distinct_listings)

    if decision.status == gate.STATUS_INSPECTION_REQUIRED:
        # Market context only: comparable links, never an individual quote.
        weights = [1.0 / (1.0 + d) for _, d in distinct]
        comparables_json = [_comparable_dict(listing, w) for (listing, _d), w in zip(distinct, weights)]
        appraisal = _save(
            db, session, status=gate.STATUS_INSPECTION_REQUIRED, reasons=reasons_with_assumptions,
            revision=revision, comparables_json=comparables_json, matched_attributes=matched_attributes,
        )
        return _to_out(db, appraisal, None, tax_note=tax_note, distinct_groups=len(distinct))

    result = pricing.price_from_comparables(distinct, MIN_DISTINCT_VEHICLE_GROUPS)

    if result.status != "priced":
        # No photo can create market listings, so none is requested.
        appraisal = _save(
            db, session, status=result.status, reasons=[*result.reasons, *(f"Assumption: {a}" for a in decision.assumptions)],
            revision=revision, matched_attributes=matched_attributes,
            comparables_json=[_comparable_dict(c.listing, c.weight) for c in result.comparables],
        )
        return _to_out(db, appraisal, None, tax_note=tax_note, distinct_groups=len(distinct))

    comparables_json = [_comparable_dict(c.listing, c.weight) for c in result.comparables]
    appraisal = _save(
        db, session, status="priced", reasons=reasons_with_assumptions, revision=revision,
        comparables_json=comparables_json, matched_attributes=matched_attributes,
        price=(result.price_low, result.price_mid, result.price_high),
    )
    return _to_out(
        db, appraisal, gate.next_best_photo(db, session_id, decision),
        method_note=pricing.METHOD_NOTE, tax_note=tax_note, distinct_groups=len(distinct),
    )
