from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..db import get_db
from ..services import gate, pricing
from ..services.evidence import get_field_evidence

router = APIRouter(prefix="/sessions", tags=["appraisals"])


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


def _save(
    db: Session,
    session: models.InspectionSession,
    version: int,
    status: str,
    price_low: float | None,
    price_mid: float | None,
    price_high: float | None,
    comparables_json: list[dict],
    matched_attributes: dict[str, str],
    reasons: list[str],
) -> models.Appraisal:
    appraisal = models.Appraisal(
        session_id=session.id,
        version=version,
        status=status,
        price_low=price_low,
        price_mid=price_mid,
        price_high=price_high,
        comparable_count=len(comparables_json),
        comparables=comparables_json,
        matched_attributes=matched_attributes,
        reasons=reasons,
    )
    db.add(appraisal)
    db.commit()
    db.refresh(appraisal)
    return appraisal


def _to_out(db: Session, appraisal: models.Appraisal, next_photo: gate.NextPhotoRequest | None) -> schemas.AppraisalOut:
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
    )


@router.post("/{session_id}/appraisals", response_model=schemas.AppraisalOut, status_code=201)
def create_appraisal(session_id: str, db: Session = Depends(get_db)):
    """Milestone 2 + 4: the evidence gate decides whether pricing may run at
    all; Claude/the vision model never sees or returns a price — only the
    comparable-based pricing engine in services/pricing.py does."""
    session = _get_session_or_404(db, session_id)
    settings = get_settings()

    prior_versions = db.scalars(select(models.Appraisal).where(models.Appraisal.session_id == session_id)).all()
    version = len(prior_versions) + 1

    decision = gate.evaluate_gate(db, session_id)

    if decision.status in (gate.STATUS_UNSUPPORTED, gate.STATUS_NEEDS_EVIDENCE):
        appraisal = _save(db, session, version, decision.status, None, None, None, [], {}, decision.reasons)
        return _to_out(db, appraisal, decision.next_photo)

    # decision.status is inspection_required or ready_to_price — both need a
    # VehicleSpec to look up comparables (plan: "comparable market context
    # may still be shown" even when an individual price is withheld).
    axle_config = get_field_evidence(db, session_id, "axle_config")
    year = get_field_evidence(db, session_id, "year")
    vat_basis = get_field_evidence(db, session_id, "vat_basis")

    try:
        spec = pricing.VehicleSpec(
            category="tractor_unit",
            model_family=decision.identity["model_family"].value,
            axle_config=axle_config.value,
            year=int(year.value),
            mileage_km=int(decision.mileage.value),
            vat_basis=vat_basis.value or "vat_excluded",
        )
    except (TypeError, ValueError) as exc:
        appraisal = _save(
            db, session, version, "needs_evidence", None, None, None, [], {},
            [f"Could not parse pricing inputs: {exc}"],
        )
        return _to_out(db, appraisal, decision.next_photo)

    matched_attributes = {
        "model_family": spec.model_family,
        "axle_config": spec.axle_config,
        "year": str(spec.year),
        "mileage_km": str(spec.mileage_km),
        "vat_basis": spec.vat_basis,
    }

    scored = pricing.find_comparables(db, spec, max_comparables=settings.max_comparables)

    if decision.status == gate.STATUS_INSPECTION_REQUIRED:
        weights = [1.0 / (1.0 + d) for _, d in scored]
        comparables_json = [_comparable_dict(listing, w) for (listing, _d), w in zip(scored, weights)]
        appraisal = _save(
            db, session, version, gate.STATUS_INSPECTION_REQUIRED, None, None, None,
            comparables_json, matched_attributes, decision.reasons,
        )
        return _to_out(db, appraisal, decision.next_photo)

    if len(scored) < settings.min_comparables:
        result = pricing.price_from_comparables(scored, settings.min_comparables, None, quantile_is_provisional=False)
        appraisal = _save(db, session, version, result.status, None, None, None, [], matched_attributes, result.reasons)
        return _to_out(db, appraisal, gate.next_best_photo(db, session_id))

    quantile = pricing.estimate_quantile_from_comparables(scored, settings.conformal_alpha)
    result = pricing.price_from_comparables(scored, settings.min_comparables, quantile, quantile_is_provisional=True)

    comparables_json = [_comparable_dict(c.listing, c.weight) for c in result.comparables]
    appraisal = _save(
        db, session, version, result.status, result.price_low, result.price_mid, result.price_high,
        comparables_json, matched_attributes, result.reasons,
    )
    return _to_out(db, appraisal, gate.next_best_photo(db, session_id))
