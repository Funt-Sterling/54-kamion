"""How real vision output lands in the evidence model (Steps 3, 4, 6).

Complements test_e2e_flow.py: these drive the same real endpoints, but
focus on *where facts come from* rather than on the gate's branching —
that every visual fact is recorded as observed_from_photo, that a
seller's contradicting claim is preserved as a conflict rather than
overwritten, and that a failed model call never becomes silent evidence.

The vision call is scripted (see tests/helpers.py) so these are
deterministic; the fixtures used here are copied from the shape of a real
Claude response to a real F-MAX photo.
"""

from app.db import SessionLocal
from app.models import EvidenceRecord, Listing

from .helpers import make_image, patch_vision_adapter, truck_result, upload_media

# Shape of the real response to the real front-exterior F-MAX photo: the
# badge is legible, but a frontal shot shows no odometer, plate year, or
# axle count — so extracted_specs is genuinely empty.
REAL_FRONT_PHOTO_RESULT = dict(
    vehicle_category_guess="tractor_unit",
    make_guess="Ford",
    model_guess="F-MAX",
    visible_badge_text="F-MAX Ford",
    extracted_specs={},
)


def _evidence(session_id: str, field: str) -> list[EvidenceRecord]:
    db = SessionLocal()
    try:
        return (
            db.query(EvidenceRecord)
            .filter(EvidenceRecord.session_id == session_id, EvidenceRecord.field == field)
            .all()
        )
    finally:
        db.close()


def _field_state(client, session_id: str, field: str) -> dict:
    session = client.get(f"/sessions/{session_id}").json()
    return next(e for e in session["evidence"] if e["field"] == field)


# --- provenance ----------------------------------------------------------------------


def test_visual_facts_are_stored_as_observed_from_photo(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(**{**REAL_FRONT_PHOTO_RESULT, "extracted_specs": {"mileage_km": "480000"}}),
    )
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    for field in ("mileage_km", "vehicle_category"):
        records = _evidence(session_id, field)
        assert records, f"no evidence recorded for {field}"
        assert all(r.provenance == "observed_from_photo" for r in records)
        assert all(r.media_id is not None for r in records), "photo-derived evidence must cite its photo"


def test_legible_badge_is_observed_not_merely_inferred(client, monkeypatch, tmp_path):
    """A badge Claude can actually read is stronger evidence than a visual
    hunch, and the two must stay distinguishable."""
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, lambda path, hint: truck_result(**REAL_FRONT_PHOTO_RESULT))
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    assert _field_state(client, session_id, "model_family")["status"] == "observed_from_photo"


def test_guess_without_a_legible_badge_is_only_a_candidate(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(**{**REAL_FRONT_PHOTO_RESULT, "visible_badge_text": None}),
    )
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    assert _field_state(client, session_id, "model_family")["status"] == "inferred_candidate"


def test_seller_claim_conflicting_with_the_dashboard_is_preserved(client, monkeypatch, tmp_path):
    """The spec's worked example: seller says 250,000 km, the dashboard
    photo reads 650,000 km. Both survive; neither is chosen."""
    session_id = client.post("/sessions").json()["id"]
    client.patch(f"/sessions/{session_id}/details", json={"field": "mileage_km", "value": "250000"})
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(**{**REAL_FRONT_PHOTO_RESULT, "extracted_specs": {"mileage_km": "650000"}}),
    )
    upload_media(client, session_id, make_image(tmp_path / "dash.jpg"), component_hint="dashboard_odometer")

    state = _field_state(client, session_id, "mileage_km")
    assert state["status"] == "conflicting"
    assert state["value"] is None
    assert state["seller_declared"] == "250000"
    assert state["observed_from_photo"] == "650000"

    provenances = {r.provenance for r in _evidence(session_id, "mileage_km")}
    assert provenances == {"seller_declared", "observed_from_photo"}

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "needs_evidence"
    assert appraisal["price_mid"] is None


# --- unresolved fields stay unresolved ------------------------------------------------


def test_front_photo_alone_leaves_pricing_inputs_unknown(client, monkeypatch, tmp_path):
    """A frontal shot genuinely cannot show mileage, model year, or axle
    count — those must stay unknown rather than being filled in."""
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, lambda path, hint: truck_result(**REAL_FRONT_PHOTO_RESULT))
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    for field in ("mileage_km", "year", "axle_config"):
        assert _field_state(client, session_id, field)["status"] == "unknown"


def test_failed_vision_call_records_no_evidence_at_all(client, monkeypatch, tmp_path):
    """A failed analysis must not be mistaken for a photo that showed
    nothing — no evidence rows, and the media marked failed."""
    def _boom(path, hint):
        raise RuntimeError("simulated API error")

    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, _boom)
    response = upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    assert response.json()["vision_status"] == "failed"
    db = SessionLocal()
    try:
        assert db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id).count() == 0
    finally:
        db.close()


# --- the model cannot inject a price --------------------------------------------------


def test_vision_cannot_introduce_a_price_field_into_evidence(client, monkeypatch, tmp_path):
    """Even if a model returned price-shaped specs, they must not reach the
    evidence model — the adapter whitelist drops them upstream."""
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(
            **{**REAL_FRONT_PHOTO_RESULT, "extracted_specs": {"mileage_km": "480000"}}
        ),
    )
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    db = SessionLocal()
    try:
        fields = {r.field for r in db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id).all()}
    finally:
        db.close()
    assert not any("price" in f.lower() or "value" in f.lower() for f in fields), fields


def test_badge_case_does_not_break_comparable_lookup(client, monkeypatch, tmp_path):
    """Claude reads the badge as "F-MAX"; the market dataset keys it
    "f-max". The lookup has to canonicalize or every real photo silently
    returns insufficient_market_data."""
    db = SessionLocal()
    try:
        for i in range(8):
            db.add(
                Listing(
                    source_url=f"https://example.test/{i}",
                    location="İstanbul, TR",
                    country="TR",
                    make="FORD",
                    model="F-MAX",
                    model_family="f-max",
                    category="tractor_unit",
                    year=2021,
                    mileage_km=350_000 + i * 10_000,
                    axle_config="4x2",
                    price=2_400_000 + i * 25_000,
                    currency="TRY",
                    vat_basis="unknown",
                    partition="dev",
                )
            )
        db.commit()
    finally:
        db.close()

    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(
            **{**REAL_FRONT_PHOTO_RESULT, "extracted_specs": {"year": "2021", "mileage_km": "400000", "axle_config": "4X2"}}
        ),
    )
    upload_media(client, session_id, make_image(tmp_path / "front.jpg"), component_hint="front_exterior")

    appraisal = client.post(f"/sessions/{session_id}/appraisals").json()
    assert appraisal["status"] == "priced", appraisal
    assert appraisal["comparable_count"] >= 5
    # Stored evidence keeps what was actually read, uppercase and all.
    assert _field_state(client, session_id, "model_family")["value"] == "F-MAX"
