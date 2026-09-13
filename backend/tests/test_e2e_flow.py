"""End-to-end tests through the real HTTP API, exactly as a client calls it:
photo -> quality check -> proposal -> admission -> evidence -> gate ->
needs_evidence with a specific request, or comparables -> provisional range.

The vision "model" is scripted (tests/helpers.ScriptedVisionAdapter), so
these are deterministic and need no network or API key. Everything beneath
it — parser, admission, resolver, gate, pricing — is production code.
Scripted proposals prove how the software treats a proposal; they are not
evidence that any real model reads real photos correctly.
"""

import math
from datetime import datetime, timedelta, timezone

from app.contract import MIN_DISTINCT_VEHICLE_GROUPS, VAT_UNSTATED_LABEL
from app.db import SessionLocal
from app.models import Listing

from .helpers import (
    appearance_only_proposal,
    closeup_proposal,
    front_badge_proposal,
    make_image,
    odometer_proposal,
    patch_vision_adapter,
    side_proposal,
    truck_result,
    upload_media,
    upload_scripted,
)


def _seed_listings(n=8, **overrides):
    db = SessionLocal()
    try:
        for i in range(n):
            row = dict(
                source_url=f"https://example.test/listing-{i}",
                location="Istanbul, TR",
                country="TR",
                make="FORD",
                model="F-MAX 500",
                model_family="f-max",
                category="tractor_unit",
                year=2020 + (i % 3),
                mileage_km=300_000 + i * 17_000,
                axle_config="4x2",
                price=3_000_000 + i * 90_000,
                currency="TRY",
                # truckmarket.com.tr publishes no VAT basis; "unknown" is the
                # realistic default, and an undeclared session matches only it.
                vat_basis="unknown",
                partition="dev",
            )
            row.update(overrides)
            db.add(Listing(**row))
        db.commit()
    finally:
        db.close()


def _declare(client, session_id, field, value):
    response = client.patch(f"/sessions/{session_id}/details", json={"field": field, "value": value})
    assert response.status_code == 200, response.text


def _ready_session(client, monkeypatch, tmp_path, *, odometer="365000", badge="FORD TRUCKS F-MAX"):
    """Whole-truck badge + side profile + odometer, declared 4x2 and year."""
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(badge), name="front.jpg", seed=1)
    upload_scripted(client, monkeypatch, tmp_path, session_id, side_proposal(2), name="side.jpg", seed=2)
    _declare(client, session_id, "axle_config", "4x2")
    _declare(client, session_id, "year", "2021")
    upload_scripted(client, monkeypatch, tmp_path, session_id, odometer_proposal(odometer), name="odo.jpg", seed=3)
    return session_id


def _session(client, session_id):
    return client.get(f"/sessions/{session_id}").json()


def _field(session, name):
    return next(e for e in session["evidence"] if e["field"] == name)


def _appraise(client, session_id):
    response = client.post(f"/sessions/{session_id}/appraisals")
    assert response.status_code == 201, response.text
    return response.json()


# A. blurry image ------------------------------------------------------------------------


def test_a_blurry_image_rejected_and_session_needs_evidence(client, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    response = upload_media(client, session_id, make_image(tmp_path / "blurry.jpg", blurry=True), component_hint="front_exterior")
    body = response.json()
    assert body["accepted"] is False
    assert body["reject_reason"] == "too_blurry"

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["price_mid"] is None


# B / case 9. motorcycle ------------------------------------------------------------------


def test_b_motorcycle_is_unsupported_and_credits_no_truck_coverage(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    motorcycle = truck_result(subject_category="other_vehicle", visible_views={"front", "tire"})
    body = upload_scripted(client, monkeypatch, tmp_path, session_id, motorcycle, name="moto.jpg", seed=4, hint="front_exterior")

    assert body["accepted"] is False
    assert body["reject_reason"] == "not_a_tractor_unit:other_vehicle"
    assert body["observed_views"] == []

    session = _session(client, session_id)
    assert session["coverage"]["tire"] == "missing"
    assert session["coverage"]["front_exterior"] == "missing"
    assert session["gate_status"] == "unsupported"
    assert _appraise(client, session_id)["status"] == "unsupported"


# C. missing mileage ------------------------------------------------------------------------


def test_c_missing_odometer_requests_odometer_photo(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(), name="front.jpg", seed=1)
    upload_scripted(client, monkeypatch, tmp_path, session_id, side_proposal(2), name="side.jpg", seed=2)
    _declare(client, session_id, "axle_config", "4x2")

    session = _session(client, session_id)
    assert _field(session, "mileage_km")["status"] == "unknown"
    assert session["gate_status"] == "needs_evidence"

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "mileage_km"
    assert "odometer" in appraisal["next_photo"]["requested_view"].lower()


# D / case 10. seller mileage vs odometer ----------------------------------------------------


def test_d_seller_mileage_conflicts_with_observed_odometer(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path, odometer="1000000")
    _declare(client, session_id, "mileage_km", "850000")

    mileage = _field(_session(client, session_id), "mileage_km")
    assert mileage["status"] == "conflicting"
    assert mileage["value"] is None
    assert {p["provenance"] for p in mileage["participants"]} == {"seller_declared", "observed_from_photo"}

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "mileage_km"
    assert any("850000" in r and "1.000.000" in r for r in appraisal["reasons"]), appraisal["reasons"]


def test_matching_seller_mileage_is_source_agreement_not_verification(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    _declare(client, session_id, "mileage_km", "365 000")

    mileage = _field(_session(client, session_id), "mileage_km")
    assert mileage["status"] == "sources_agree"
    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "priced"
    assert any("not verified lifetime mileage" in r for r in appraisal["reasons"])


# E. priced provisional range ---------------------------------------------------------------


def test_e_supported_evidence_and_distinct_comparables_return_provisional_range(client, monkeypatch, tmp_path):
    _seed_listings(n=8)
    session_id = _ready_session(client, monkeypatch, tmp_path)

    session = _session(client, session_id)
    assert session["gate_status"] == "ready_to_price", session["gate_reasons"]
    family = _field(session, "model_family")
    assert family["canonical_value"] == "f-max"
    assert family["status"] == "observed_from_photo"

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "priced", appraisal["reasons"]
    low, mid, high = appraisal["price_low"], appraisal["price_mid"], appraisal["price_high"]
    assert all(math.isfinite(v) for v in (low, mid, high))
    assert low < mid < high
    assert appraisal["distinct_vehicle_groups"] >= MIN_DISTINCT_VEHICLE_GROUPS
    assert appraisal["tax_note"] == VAT_UNSTATED_LABEL
    assert "not a calibrated" in appraisal["method_note"]
    assert appraisal["evidence_revision"] == session["evidence_revision"]
    assert appraisal["matched_attributes"]["model_family"] == "f-max"
    assert appraisal["matched_attributes"]["make"] == "ford"
    assert any("assuming seller-declared 4x2" in r for r in appraisal["reasons"])
    for comparable in appraisal["comparables"]:
        assert comparable["source_url"].startswith("https://")


# F / case 12. market sufficiency --------------------------------------------------------------


def test_f_too_few_comparables_is_insufficient_without_a_photo_request(client, monkeypatch, tmp_path):
    _seed_listings(n=2)
    session_id = _ready_session(client, monkeypatch, tmp_path)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["price_mid"] is None
    assert appraisal["next_photo"] is None


def test_f2_sparse_family_is_not_priced_off_another_family(client, monkeypatch, tmp_path):
    _seed_listings(n=20, make="Mercedes-Benz", model="Actros", model_family="actros")
    session_id = _ready_session(client, monkeypatch, tmp_path)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["price_mid"] is None
    assert appraisal["next_photo"] is None


def test_f3_incompatible_vat_basis_never_mixes_into_the_pool(client, monkeypatch, tmp_path):
    _seed_listings(n=20, vat_basis="vat_included")
    session_id = _ready_session(client, monkeypatch, tmp_path)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "insufficient_market_data"
    assert appraisal["comparable_count"] == 0


def test_f4_wrong_country_currency_make_and_configuration_cannot_fill_the_pool(client, monkeypatch, tmp_path):
    _seed_listings(n=4)  # four good vehicles: one short of the minimum
    _seed_listings(n=3, country="DE", source_url="https://example.test/de")
    _seed_listings(n=3, currency="EUR", price=90_000)
    _seed_listings(n=3, make="SCANIA", model="R 450")
    _seed_listings(n=3, axle_config="6x2")
    session_id = _ready_session(client, monkeypatch, tmp_path)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "insufficient_market_data", appraisal
    assert appraisal["distinct_vehicle_groups"] == 4


def test_f5_reposts_of_one_vehicle_are_not_independent_comparables(client, monkeypatch, tmp_path):
    _seed_listings(n=4)
    # Four more rows that are the SAME truck re-advertised under new URLs.
    for i in range(4):
        _seed_listings(n=1, source_url=f"https://example.test/repost-{i}", year=2021, mileage_km=333_000, price=3_333_000)
    session_id = _ready_session(client, monkeypatch, tmp_path)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "priced"
    assert appraisal["distinct_vehicle_groups"] == 5
    urls = [c["source_url"] for c in appraisal["comparables"]]
    assert sum("repost" in url for url in urls) == 1


def test_f6_stale_listings_do_not_count(client, monkeypatch, tmp_path):
    _seed_listings(n=8, collected_at=datetime.now(timezone.utc) - timedelta(days=400))
    session_id = _ready_session(client, monkeypatch, tmp_path)
    assert _appraise(client, session_id)["status"] == "insufficient_market_data"


# G. missing view stays unknown --------------------------------------------------------------


def test_g_unphotographed_views_remain_missing_after_pricing(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    assert _appraise(client, session_id)["status"] == "priced"

    session = _session(client, session_id)
    assert session["coverage"]["rear_exterior"] == "missing"
    assert session["coverage"]["cab_interior"] == "missing"


# H / case 11. duplicate success adds no support --------------------------------------------------


def test_h_duplicate_successful_photo_adds_no_independent_evidence(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, lambda path, hint: front_badge_proposal())
    image = make_image(tmp_path / "front.jpg", seed=9)

    first = upload_media(client, session_id, image).json()
    assert first["accepted"] is True
    revision = _session(client, session_id)["evidence_revision"]

    second = upload_media(client, session_id, image).json()
    assert second["accepted"] is False
    assert second["reject_reason"] == "duplicate_of_existing_photo"

    session = _session(client, session_id)
    assert session["evidence_revision"] == revision
    family = _field(session, "model_family")
    assert family["status"] == "observed_from_photo"  # not "sources_agree"
    assert len(family["participants"]) == 1


# I / case 11 + 13. failures and idempotent retry --------------------------------------------------


def test_i_timeout_invents_nothing_and_same_bytes_retry_on_the_same_media(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    image = make_image(tmp_path / "front.jpg", seed=11)

    def _timeout(path, hint):
        raise TimeoutError("simulated network timeout")

    patch_vision_adapter(monkeypatch, _timeout)
    failed = upload_media(client, session_id, image).json()
    assert failed["vision_status"] == "failed"
    assert failed["accepted"] is True
    assert "vision_analysis_failed:timeout" in failed["quality_notes"]
    session = _session(client, session_id)
    assert all(e["status"] == "unknown" for e in session["evidence"])
    assert session["gate_status"] == "needs_evidence"

    patch_vision_adapter(monkeypatch, lambda path, hint: front_badge_proposal())
    retried = upload_media(client, session_id, image).json()
    assert retried["id"] == failed["id"]
    assert retried["vision_status"] == "ok"
    assert retried["accepted"] is True
    assert not any(n.startswith("vision_analysis_failed") for n in retried["quality_notes"])
    assert _field(_session(client, session_id), "model_family")["canonical_value"] == "f-max"


def test_i2_malformed_response_admits_nothing(client, monkeypatch, tmp_path):
    from app.services.proposal import ProposalSchemaError

    session_id = client.post("/sessions").json()["id"]

    def _malformed(path, hint):
        raise ProposalSchemaError("findings[0].visibility: missing")

    patch_vision_adapter(monkeypatch, _malformed)
    body = upload_media(client, session_id, make_image(tmp_path / "front.jpg", seed=12)).json()
    assert body["vision_status"] == "failed"
    assert "vision_analysis_failed:schema" in body["quality_notes"]
    assert body["observed_views"] == []
    session = _session(client, session_id)
    assert all(state == "missing" for state in session["coverage"].values())


def test_i3_retry_is_bounded(client, monkeypatch, tmp_path):
    from app.contract import MAX_ANALYSIS_ATTEMPTS_PER_MEDIA

    session_id = client.post("/sessions").json()["id"]
    image = make_image(tmp_path / "front.jpg", seed=13)
    calls = []

    def _boom(path, hint):
        calls.append(path)
        raise RuntimeError("upstream unavailable")

    patch_vision_adapter(monkeypatch, _boom)
    for _ in range(MAX_ANALYSIS_ATTEMPTS_PER_MEDIA + 2):
        body = upload_media(client, session_id, image).json()

    assert len(calls) == MAX_ANALYSIS_ATTEMPTS_PER_MEDIA
    assert "vision_analysis_failed:retry_limit" in body["quality_notes"]


# Case 9. weak evidence cannot price ----------------------------------------------------------------


def test_candidate_only_identity_cannot_price(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, appearance_only_proposal("F-MAX"), name="front.jpg", seed=1)
    upload_scripted(client, monkeypatch, tmp_path, session_id, side_proposal(2), name="side.jpg", seed=2)
    upload_scripted(client, monkeypatch, tmp_path, session_id, odometer_proposal(), name="odo.jpg", seed=3)
    _declare(client, session_id, "axle_config", "4x2")
    _declare(client, session_id, "year", "2021")

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "model_family"


def test_declared_only_mileage_cannot_price(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(), name="front.jpg", seed=1)
    upload_scripted(client, monkeypatch, tmp_path, session_id, side_proposal(2), name="side.jpg", seed=2)
    for field, value in (("axle_config", "4x2"), ("year", "2021"), ("mileage_km", "365000")):
        _declare(client, session_id, field, value)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "mileage_km"
    assert _field(_session(client, session_id), "mileage_km")["status"] == "seller_declared"


def test_tire_only_evidence_cannot_price(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("tire", category="tractor_unit"), name="tire.jpg", seed=5)
    for field, value in (("model_family", "F-MAX"), ("axle_config", "4x2"), ("year", "2021"), ("mileage_km", "365000")):
        _declare(client, session_id, field, value)

    session = _session(client, session_id)
    assert session["coverage"]["tire"] == "captured"
    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "vehicle_identity"


def test_declared_configuration_without_compatible_axle_count_requests_side_profile(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(), name="front.jpg", seed=1)
    _declare(client, session_id, "axle_config", "4x2")
    appraisal = _appraise(client, session_id)
    assert appraisal["next_photo"]["resolves"] == "axle_config"

    upload_scripted(client, monkeypatch, tmp_path, session_id, side_proposal(3), name="side.jpg", seed=2)
    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert any("3 axles are visible" in r for r in appraisal["reasons"])


# Case 6 + 7. identity conflicts and supersession -----------------------------------------------------


def test_two_observed_model_identities_conflict_and_both_stay_visible(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal("F-MAX"), name="a.jpg", seed=1)
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal("ACTROS"), name="b.jpg", seed=2)

    family = _field(_session(client, session_id), "model_family")
    assert family["status"] == "conflicting"
    assert family["value"] is None
    assert {p["canonical_value"] for p in family["participants"]} == {"f-max", "actros"}
    assert _appraise(client, session_id)["next_photo"]["resolves"] == "model_family"


def test_r_series_candidate_is_the_visible_conflict_then_superseded_by_readable_badge(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    _declare(client, session_id, "model_family", "F-MAX")
    upload_scripted(client, monkeypatch, tmp_path, session_id, appearance_only_proposal("R-series"), name="a.jpg", seed=1)

    family = _field(_session(client, session_id), "model_family")
    assert family["status"] == "conflicting"
    assert {(p["provenance"], p["canonical_value"]) for p in family["participants"]} == {
        ("seller_declared", "f-max"),
        ("inferred_candidate", "r-series"),
    }

    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal("F MAX"), name="b.jpg", seed=2)
    family = _field(_session(client, session_id), "model_family")
    assert family["status"] == "sources_agree"
    assert family["canonical_value"] == "f-max"
    history = family["superseded"]
    assert [(h["canonical_value"], h["state"]) for h in history] == [("r-series", "superseded")]
    assert "photo-supported observation" in history[0]["state_reason"]


# Case 11. concerns --------------------------------------------------------------------------------


_BENT_RAIL = {
    "component": "chassis frame rail",
    "observation": "left rail appears bent behind the cab",
    "visibility": "visible",
    "severity": "structural",
    "recommended_action": "in-person inspection",
}
_TREAD = {
    "component": "tire tread",
    "observation": "front-left tread looks uneven; closer view needed",
    "visibility": "visible",
    "severity": "clarify",
    "recommended_action": "closer photo",
}


def test_structural_concern_withholds_price_but_keeps_market_context(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("chassis", findings=[_BENT_RAIL]), name="c.jpg", seed=6)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "inspection_required"
    assert appraisal["price_mid"] is None and appraisal["price_low"] is None
    assert appraisal["comparable_count"] >= MIN_DISTINCT_VEHICLE_GROUPS
    assert appraisal["next_photo"] is None


def test_unrelated_retake_cannot_dismiss_structural_concern(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("chassis", findings=[_BENT_RAIL]), name="c.jpg", seed=6)
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("cab"), name="cab.jpg", seed=7)
    concern = _session(client, session_id)["concerns"][0]
    assert concern["status"] == "open"

    # Even a clean photo of the SAME view only moves it to human review.
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("chassis"), name="c2.jpg", seed=8)
    concern = _session(client, session_id)["concerns"][0]
    assert concern["status"] == "review_required"
    assert _appraise(client, session_id)["status"] == "inspection_required"


def test_clear_retake_resolves_the_clarify_concern_it_addresses(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("tire", findings=[_TREAD]), name="t.jpg", seed=6)

    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["next_photo"]["resolves"] == "tire"

    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("cab"), name="cab.jpg", seed=7)
    assert _appraise(client, session_id)["status"] == "needs_evidence"

    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("tire"), name="t2.jpg", seed=8)
    session = _session(client, session_id)
    assert session["concerns"][0]["status"] == "resolved"
    assert session["coverage"]["tire"] == "captured"
    assert _appraise(client, session_id)["status"] == "priced"


# Case 15. revisions ------------------------------------------------------------------------------------


def test_evidence_change_makes_the_previous_appraisal_stale(client, monkeypatch, tmp_path):
    _seed_listings()
    session_id = _ready_session(client, monkeypatch, tmp_path)
    appraisal = _appraise(client, session_id)
    assert appraisal["status"] == "priced"
    assert appraisal["evidence_revision"] == _session(client, session_id)["evidence_revision"]

    _declare(client, session_id, "year", "2022")
    assert _session(client, session_id)["evidence_revision"] > appraisal["evidence_revision"]

    upload_scripted(client, monkeypatch, tmp_path, session_id, closeup_proposal("cab"), name="cab.jpg", seed=7)
    fresh = _appraise(client, session_id)
    assert fresh["evidence_revision"] == _session(client, session_id)["evidence_revision"]
    assert fresh["version"] == appraisal["version"] + 1


# Abuse bounds (security review) ----------------------------------------------------------------


def test_repeating_rejected_bytes_writes_no_new_row(client, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    blurry = make_image(tmp_path / "blurry.jpg", blurry=True)
    first = upload_media(client, session_id, blurry).json()
    second = upload_media(client, session_id, blurry).json()
    assert first["accepted"] is False
    assert second["id"] == first["id"]


def test_uploads_per_session_are_capped(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.routers.media.MAX_MEDIA_PER_SESSION", 2)
    patch_vision_adapter(monkeypatch, lambda path, hint: closeup_proposal("cab"))
    session_id = client.post("/sessions").json()["id"]
    for seed in (1, 2):
        assert upload_media(client, session_id, make_image(tmp_path / f"{seed}.jpg", seed=seed)).status_code == 201
    assert upload_media(client, session_id, make_image(tmp_path / "3.jpg", seed=3)).status_code == 429


def test_model_calls_are_rate_limited_without_spending_an_attempt(client, monkeypatch, tmp_path):
    import app.routers.media as media_router

    monkeypatch.setattr(media_router, "MAX_ANALYSES_PER_MINUTE", 0)
    calls = []
    patch_vision_adapter(monkeypatch, lambda path, hint: calls.append(path) or closeup_proposal("cab"))
    session_id = client.post("/sessions").json()["id"]
    body = upload_media(client, session_id, make_image(tmp_path / "a.jpg", seed=4)).json()
    assert calls == []
    assert body["vision_status"] == "failed"
    assert "vision_analysis_failed:rate_limited" in body["quality_notes"]
