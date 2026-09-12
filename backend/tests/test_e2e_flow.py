"""Milestone 6 end-to-end tests, driven through the real HTTP API exactly
as a client would call it: unseen photos -> quality/object check ->
structured evidence -> evidence gate -> needs_evidence-with-a-specific-
request, or comparable retrieval -> price range -> findings -> unknowns.

The vision "model" is replaced with tests/helpers.ScriptedVisionAdapter so
these are deterministic and need no network access or API key — but the
gate, evidence resolution, quality checks, and pricing engine underneath
are all the real production code, exercised through the real endpoints.
"""

from app.db import SessionLocal
from app.models import Listing

from .helpers import make_image, patch_vision_adapter, truck_result, upload_media


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
                # Real collected listings (truckmarket.com.tr) don't publish a
                # VAT basis, so "unknown" is the realistic default here — and
                # a session that declares no basis matches only these.
                vat_basis="unknown",
                partition="dev",
            )
            defaults.update(overrides)
            db.add(Listing(**defaults))
        db.commit()
    finally:
        db.close()


def _resolve_identity_and_mileage(client, monkeypatch, tmp_path, session_id, *, mileage="480000", axle_config="4x2", year="2019"):
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(extracted_specs={"axle_config": axle_config, "year": year, "mileage_km": mileage}),
    )
    image = make_image(tmp_path / "front.jpg")
    resp = upload_media(client, session_id, image, component_hint="front_exterior")
    assert resp.status_code == 201, resp.text
    return resp


# A. blurry truck image -> needs_evidence -------------------------------------------------


def test_a_blurry_image_rejected_and_session_needs_evidence(client, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    blurry = make_image(tmp_path / "blurry.jpg", blurry=True)

    resp = upload_media(client, session_id, blurry, component_hint="front_exterior")
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is False
    assert body["reject_reason"] == "too_blurry"

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["price_mid"] is None


# B. motorcycle / non-truck -> unsupported -------------------------------------------------


def test_b_non_truck_vehicle_is_unsupported(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(is_truck_tractor_unit=False, vehicle_category_guess="motorcycle"),
    )
    image = make_image(tmp_path / "moto.jpg")

    resp = upload_media(client, session_id, image, component_hint="front_exterior")
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is False
    assert body["reject_reason"] == "not_a_tractor_unit:motorcycle"

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "unsupported"


# C. usable truck but missing mileage -> dashboard request ---------------------------------


def test_c_missing_mileage_requests_dashboard_photo(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(extracted_specs={"axle_config": "4x2", "year": "2019"}),
    )
    image = make_image(tmp_path / "front.jpg")
    resp = upload_media(client, session_id, image, component_hint="front_exterior")
    assert resp.status_code == 201
    assert resp.json()["accepted"] is True

    session = client.get(f"/sessions/{session_id}").json()
    mileage_evidence = next(e for e in session["evidence"] if e["field"] == "mileage_km")
    assert mileage_evidence["status"] == "unknown"

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "mileage_km"
    assert "odometer" in appraisal["next_photo"]["requested_view"].lower()


# D. seller mileage conflicts with observed mileage -> discrepancy preserved ----------------


def test_d_seller_mileage_conflicts_with_observed_mileage(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    resp = client.patch(f"/sessions/{session_id}/details", json={"field": "mileage_km", "value": "250000"})
    assert resp.status_code == 200

    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(extracted_specs={"axle_config": "4x2", "year": "2019", "mileage_km": "650000"}),
    )
    image = make_image(tmp_path / "front.jpg")
    upload_media(client, session_id, image, component_hint="front_exterior")

    session = client.get(f"/sessions/{session_id}").json()
    mileage_evidence = next(e for e in session["evidence"] if e["field"] == "mileage_km")
    assert mileage_evidence["status"] == "conflicting"
    assert mileage_evidence["value"] is None
    assert mileage_evidence["seller_declared"] == "250000"
    assert mileage_evidence["observed_from_photo"] == "650000"

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "mileage_km"
    assert any("250000" in r and "650000" in r for r in appraisal["reasons"])


# E. sufficient evidence + comparables -> price range returned -----------------------------


def test_e_sufficient_evidence_and_comparables_returns_priced_range(client, monkeypatch, tmp_path):
    _seed_listings(n=6)
    session_id = client.post("/sessions").json()["id"]
    _resolve_identity_and_mileage(client, monkeypatch, tmp_path, session_id)

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "priced"
    assert appraisal["price_low"] < appraisal["price_mid"] < appraisal["price_high"]
    assert appraisal["comparable_count"] >= 5
    assert appraisal["matched_attributes"]["model_family"] == "actros"
    for comparable in appraisal["comparables"]:
        assert comparable["listing_id"].startswith("lst_")
        assert comparable["make"] == "Mercedes-Benz"
    for finding_or_reason in appraisal["reasons"]:
        assert isinstance(finding_or_reason, str)


# F. insufficient comparables -> insufficient_market_data -----------------------------------


def test_f_insufficient_comparables(client, monkeypatch, tmp_path):
    _seed_listings(n=2)  # below min_comparables (5)
    session_id = client.post("/sessions").json()["id"]
    _resolve_identity_and_mileage(client, monkeypatch, tmp_path, session_id)

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["price_mid"] is None


def test_f2_sparse_model_family_is_not_priced_off_other_families(client, monkeypatch, tmp_path):
    """A dense pool of one family must not be borrowed to price a different
    family — the comparable filter is a hard filter, not a preference."""
    _seed_listings(n=20, model_family="f-max", make="FORD", model="F-MAX")
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(
            model_guess="actros",  # a family with no listings at all
            extracted_specs={"axle_config": "4x2", "year": "2019", "mileage_km": "480000"},
        ),
    )
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["price_mid"] is None


def test_f3_incompatible_vat_basis_never_mixes_into_the_pool(client, monkeypatch, tmp_path):
    """Listings whose tax basis differs from the vehicle being priced must
    never enter the same comparable pool, even when everything else matches
    and the pool would otherwise be dense enough to price."""
    _seed_listings(n=20, vat_basis="vat_included")
    session_id = client.post("/sessions").json()["id"]
    _resolve_identity_and_mileage(client, monkeypatch, tmp_path, session_id)
    # Session declares nothing about VAT -> resolves to "unknown", which must
    # not match the 20 seeded "vat_included" listings.
    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["comparable_count"] == 0


# G. missing tire image remains unknown ------------------------------------------------------


def test_g_missing_tire_image_remains_missing_coverage(client, monkeypatch, tmp_path):
    _seed_listings(n=6)
    session_id = client.post("/sessions").json()["id"]
    _resolve_identity_and_mileage(client, monkeypatch, tmp_path, session_id)

    client.post(f"/sessions/{session_id}/appraisals")  # priced, but no tire photo was ever uploaded
    session = client.get(f"/sessions/{session_id}").json()
    assert session["coverage"]["tire"] == "missing"


# H. repeated image does not falsely increase coverage ----------------------------------------


def test_h_duplicate_photo_does_not_increase_coverage(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, lambda path, hint: truck_result())
    image = make_image(tmp_path / "front.jpg")

    first = upload_media(client, session_id, image, component_hint="front_exterior")
    assert first.json()["accepted"] is True

    second = upload_media(client, session_id, image, component_hint="front_exterior")
    assert second.json()["accepted"] is False
    assert second.json()["reject_reason"] == "duplicate_of_existing_photo"

    session = client.get(f"/sessions/{session_id}").json()
    assert session["coverage"]["front_exterior"] == "captured"


# I. model/API failure produces a recoverable error, never a crash ----------------------------


def test_i_vision_api_failure_is_recoverable_not_a_crash(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]

    def _boom(path, hint):
        raise RuntimeError("simulated API timeout")

    patch_vision_adapter(monkeypatch, _boom)
    image = make_image(tmp_path / "front.jpg")

    resp = upload_media(client, session_id, image, component_hint="front_exterior")
    assert resp.status_code == 201  # never a 500 — a failed analysis is still a valid response
    body = resp.json()
    assert body["vision_status"] == "failed"
    assert body["accepted"] is True  # the photo itself passed quality checks; only analysis failed

    # No evidence was fabricated from the failed call, so the session still
    # correctly reports it has no confirmed truck evidence yet.
    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"


# Extra: structural damage withholds an individual price but keeps market context ------------


def test_structural_damage_withholds_price_but_keeps_comparables(client, monkeypatch, tmp_path):
    _seed_listings(n=6)
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(
            extracted_specs={"axle_config": "4x2", "year": "2019", "mileage_km": "480000"},
            structural_damage_suspected=True,
        ),
    )
    image = make_image(tmp_path / "front.jpg")
    upload_media(client, session_id, image, component_hint="front_exterior")

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "inspection_required"
    assert appraisal["price_mid"] is None
    assert appraisal["comparable_count"] >= 5  # market context is still shown
    assert appraisal["next_photo"]["resolves"] == "chassis_suspension"
