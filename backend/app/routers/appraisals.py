from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..db import get_db
from ..services import pricing
from ..services.session_state import latest_details

router = APIRouter(prefix="/sessions", tags=["appraisals"])

REQUIRED_PRICING_FIELDS = ["model_family", "axle_config", "year", "mileage_km"]


def _get_session_or_404(db: Session, session_id: str) -> models.InspectionSession:
    session = db.get(models.InspectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _save(db: Session, session: models.InspectionSession, version: int, status: str, price_low, price_mid, price_high, comparables_json: list, reasons: list[str]) -> models.Appraisal:
    appraisal = models.Appraisal(
        session_id=session.id,
        version=version,
        status=status,
        price_low=price_low,
        price_mid=price_mid,
        price_high=price_high,
        comparable_count=len(comparables_json),
        comparables=comparables_json,
        reasons=reasons,
    )
    db.add(appraisal)
    db.commit()
    db.refresh(appraisal)
    return appraisal


def _to_out(db: Session, appraisal: models.Appraisal) -> schemas.AppraisalOut:
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
        findings=[schemas.FindingOut.model_validate(f) for f in findings],
        reasons=appraisal.reasons,
    )


@router.post("/{session_id}/appraisals", response_model=schemas.AppraisalOut, status_code=201)
def create_appraisal(session_id: str, db: Session = Depends(get_db)):
    session = _get_session_or_404(db, session_id)
    settings = get_settings()
    details = latest_details(db, session_id)

    prior_versions = db.scalars(select(models.Appraisal).where(models.Appraisal.session_id == session_id)).all()
    version = len(prior_versions) + 1

    category = details["category"].value if "category" in details else "tractor_unit"
    if category not in ("tractor_unit", "unknown"):
        appraisal = _save(
            db, session, version, "unsupported", None, None, None, [],
            [f"Detected vehicle category '{category}' is outside this system's supported category (tractor units only)."],
        )
        return _to_out(db, appraisal)

    missing = [f for f in REQUIRED_PRICING_FIELDS if f not in details]
    if missing:
        appraisal = _save(
            db, session, version, "needs_evidence", None, None, None, [],
            [f"Missing required detail(s) for pricing: {', '.join(missing)}."],
        )
        return _to_out(db, appraisal)

    try:
        spec = pricing.VehicleSpec(
            category="tractor_unit",
            model_family=details["model_family"].value,
            axle_config=details["axle_config"].value,
            year=int(details["year"].value),
            mileage_km=int(details["mileage_km"].value),
            vat_basis=details["vat_basis"].value if "vat_basis" in details else "vat_excluded",
        )
    except ValueError as exc:
        appraisal = _save(
            db, session, version, "needs_evidence", None, None, None, [],
            [f"Could not parse pricing details: {exc}"],
        )
        return _to_out(db, appraisal)

    scored = pricing.find_comparables(db, spec, max_comparables=settings.max_comparables)
    if len(scored) < settings.min_comparables:
        result = pricing.price_from_comparables(scored, settings.min_comparables, None, quantile_is_provisional=False)
        appraisal = _save(db, session, version, result.status, None, None, None, [], result.reasons)
        return _to_out(db, appraisal)

    quantile = pricing.estimate_quantile_from_comparables(scored, settings.conformal_alpha)
    result = pricing.price_from_comparables(scored, settings.min_comparables, quantile, quantile_is_provisional=True)

    comparables_json = [{"listing_id": c.listing_id, "similarity_weight": round(c.weight, 4)} for c in result.comparables]
    appraisal = _save(
        db, session, version, result.status, result.price_low, result.price_mid, result.price_high,
        comparables_json, result.reasons,
    )
    return _to_out(db, appraisal)
