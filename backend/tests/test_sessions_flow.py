"""End-to-end smoke test of the core loop: create a session, declare specs,
seed a few compatible market listings directly into the DB (standing in for
data_pipeline output), and confirm the appraisal endpoint prices it from
comparables rather than inventing a number."""

from app.db import SessionLocal
from app.models import Listing


def _seed_listings(n=6, **overrides):
    db = SessionLocal()
    try:
        for i in range(n):
            defaults = dict(
                source_url=f"https://example.test/listing-{i}",
                location="Istanbul, TR",
                country="TR",
                make="Mercedes-Benz",
                model="Actros",
                model_family="actros",
                category="tractor_unit",
                year=2018 + (i % 3),
                mileage_km=400_000 + i * 10_000,
                axle_config="4x2",
                price=900_000 + i * 15_000,
                currency="TRY",
                vat_basis="vat_excluded",
                partition="dev",
            )
            defaults.update(overrides)
            db.add(Listing(**defaults))
        db.commit()
    finally:
        db.close()


def _declare(client, session_id, field, value):
    resp = client.patch(f"/sessions/{session_id}/details", json={"field": field, "value": value})
    assert resp.status_code == 200, resp.text


def test_appraisal_needs_evidence_when_details_missing(client):
    session_id = client.post("/sessions").json()["id"]
    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["price_mid"] is None


def test_appraisal_insufficient_market_data_without_listings(client):
    session_id = client.post("/sessions").json()["id"]
    for field, value in [
        ("model_family", "actros"),
        ("axle_config", "4x2"),
        ("year", "2019"),
        ("mileage_km", "500000"),
    ]:
        _declare(client, session_id, field, value)

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["price_mid"] is None


def test_appraisal_prices_from_seeded_comparables(client):
    _seed_listings(n=6)
    session_id = client.post("/sessions").json()["id"]
    for field, value in [
        ("model_family", "actros"),
        ("axle_config", "4x2"),
        ("year", "2019"),
        ("mileage_km", "480000"),
        ("vat_basis", "vat_excluded"),
    ]:
        _declare(client, session_id, field, value)

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "priced"
    assert appraisal["price_mid"] is not None
    assert appraisal["price_low"] < appraisal["price_mid"] < appraisal["price_high"]
    assert appraisal["comparable_count"] >= 5
    for comparable in appraisal["comparables"]:
        assert comparable["listing_id"].startswith("lst_")


def test_appraisal_rejects_wrong_category(client):
    session_id = client.post("/sessions").json()["id"]
    _declare(client, session_id, "category", "motorcycle")

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "unsupported"


def test_incompatible_vat_basis_excluded_from_comparables(client):
    """Listings with a different VAT basis must never enter the same
    comparable pool — see docs/plan.md's data collection rules."""
    _seed_listings(n=6, vat_basis="vat_included")
    session_id = client.post("/sessions").json()["id"]
    for field, value in [
        ("model_family", "actros"),
        ("axle_config", "4x2"),
        ("year", "2019"),
        ("mileage_km", "480000"),
        ("vat_basis", "vat_excluded"),
    ]:
        _declare(client, session_id, field, value)

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "insufficient_market_data"
