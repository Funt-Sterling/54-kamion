"""Where facts come from, through the real endpoints (v1 contract).

Complements test_e2e_flow.py, which covers gate branching. These focus on
provenance: every admitted visual fact cites its photo and inference run,
a readable badge is an observation while a shape guess stays a candidate,
a failed call leaves no evidence, and a price can never enter the ledger.

The fixtures are scripted proposals shaped like the v1 schema. They prove how
proposals are treated, not that a real model reads a real photo correctly.
"""

from app.db import SessionLocal
from app.models import EvidenceRecord, InferenceRun, MediaItem, ObservedView

from .helpers import (
    appearance_only_proposal,
    front_badge_proposal,
    make_image,
    odometer_proposal,
    patch_vision_adapter,
    upload_media,
    upload_scripted,
)


def _records(session_id: str, field: str | None = None) -> list[EvidenceRecord]:
    db = SessionLocal()
    try:
        query = db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id)
        if field:
            query = query.filter(EvidenceRecord.field == field)
        return query.all()
    finally:
        db.close()


def _field_state(client, session_id: str, field: str) -> dict:
    session = client.get(f"/sessions/{session_id}").json()
    return next(e for e in session["evidence"] if e["field"] == field)


def test_admitted_visual_facts_cite_their_photo_and_inference_run(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal("FORD TRUCKS F-MAX"), name="f.jpg", seed=1)

    records = _records(session_id)
    assert {r.field for r in records} == {"vehicle_category", "make", "model_family"}
    assert all(r.provenance == "observed_from_photo" for r in records)
    assert all(r.media_id and r.run_id for r in records)

    family = next(r for r in records if r.field == "model_family")
    assert (family.value, family.canonical_value) == ("FORD TRUCKS F-MAX", "f-max")

    db = SessionLocal()
    try:
        run = db.get(InferenceRun, family.run_id)
        assert run.failure_type is None
        assert run.model_id == "scripted-test-model"
        assert run.image_sha256 and len(run.image_sha256) == 64
        assert "model_family" in run.admitted
    finally:
        db.close()


def test_readable_badge_is_observed_and_shape_guess_is_only_a_candidate(client, monkeypatch, tmp_path):
    observed = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, observed, front_badge_proposal("F-MAX"), name="a.jpg", seed=1)
    assert _field_state(client, observed, "model_family")["status"] == "observed_from_photo"

    guessed = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, guessed, appearance_only_proposal("F-MAX"), name="b.jpg", seed=2)
    assert _field_state(client, guessed, "model_family")["status"] == "inferred_candidate"


def test_front_photo_alone_leaves_pricing_inputs_unknown(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(), name="f.jpg", seed=1)
    for field in ("mileage_km", "year", "axle_config", "visible_axle_count"):
        assert _field_state(client, session_id, field)["status"] == "unknown"


def test_request_and_observation_are_reported_separately(client, monkeypatch, tmp_path):
    """Case 14 (API half): the response names what was asked for and what
    the photo showed, and says whether they match."""
    session_id = client.post("/sessions").json()["id"]
    body = upload_scripted(
        client, monkeypatch, tmp_path, session_id, odometer_proposal(), name="o.jpg", seed=1, hint="front_exterior"
    )
    assert body["requested_view"] == "front_exterior"
    assert [v["view"] for v in body["observed_views"]] == ["odometer"]
    assert body["requested_view_satisfied"] is False
    assert "component_tag" not in body


def test_capture_origin_is_recorded_as_reported_and_validated(client, monkeypatch, tmp_path):
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, lambda path, hint: front_badge_proposal())

    with open(make_image(tmp_path / "g.jpg", seed=3), "rb") as handle:
        body = client.post(
            f"/sessions/{session_id}/media",
            data={"kind": "photo", "source": "imported", "capture_origin": "gallery"},
            files={"file": ("g.jpg", handle, "image/jpeg")},
        ).json()
    assert body["capture_origin"] == "gallery"

    with open(make_image(tmp_path / "h.jpg", seed=4), "rb") as handle:
        bad = client.post(
            f"/sessions/{session_id}/media",
            data={"kind": "photo", "source": "captured", "capture_origin": "verified_camera"},
            files={"file": ("h.jpg", handle, "image/jpeg")},
        )
    assert bad.status_code == 422


def test_failed_vision_call_records_no_evidence_or_views(client, monkeypatch, tmp_path):
    def _boom(path, hint):
        raise RuntimeError("simulated API error")

    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(monkeypatch, _boom)
    response = upload_media(client, session_id, make_image(tmp_path / "front.jpg"))
    assert response.json()["vision_status"] == "failed"

    db = SessionLocal()
    try:
        assert db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id).count() == 0
        assert db.query(ObservedView).filter(ObservedView.session_id == session_id).count() == 0
        runs = db.query(InferenceRun).filter(InferenceRun.session_id == session_id).all()
        assert [r.failure_type for r in runs] == ["transport"]
        # Exception detail stays out of the stored trace beyond its class name.
        assert "simulated API error" not in (runs[0].raw_proposal or "")
    finally:
        db.close()


def test_a_price_cannot_enter_the_evidence_ledger(client, monkeypatch, tmp_path):
    """The v1 schema has nowhere to put a price, and unknown keys are a
    schema error — so price-shaped output fails closed."""
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    for extra in ({"price": 3_000_000}, {"estimated_value_try": "3.000.000"}):
        try:
            parse_proposal(proposal_json(extra=extra))
        except ProposalSchemaError:
            continue
        raise AssertionError(f"{extra} was accepted")

    session_id = client.post("/sessions").json()["id"]
    upload_scripted(client, monkeypatch, tmp_path, session_id, front_badge_proposal(), name="f.jpg", seed=1)
    fields = {r.field for r in _records(session_id)}
    assert not any("price" in f or "value" in f for f in fields), fields


def test_legacy_hint_only_media_does_not_satisfy_coverage_after_migration(client, tmp_path):
    """Case 15: pre-contract rows keep their hint as a hint. The migration
    preserves them, demotes unsupported visual evidence, and invents no
    observed views."""
    from app.config import get_settings
    from scripts.migrate_evidence_contract import migrate, sqlite_path_from_url

    session_id = client.post("/sessions").json()["id"]
    db = SessionLocal()
    try:
        media = MediaItem(
            session_id=session_id, kind="photo", source="captured", file_path=str(tmp_path / "legacy.jpg"),
            component_tag="front_exterior", client_hint=None, accepted=True, vision_status="ok",
        )
        db.add(media)
        db.flush()
        db.add(
            EvidenceRecord(
                session_id=session_id, field="model_family", value="actros",
                provenance="observed_from_photo", media_id=media.id, run_id=None,
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()

    assert migrate(sqlite_path_from_url(get_settings().database_url), dry_run=False) == 0

    db = SessionLocal()
    try:
        assert db.get(MediaItem, media_id).client_hint == "front_exterior"
        record = db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id).one()
        assert record.state == "legacy_unverified"
        assert db.query(ObservedView).filter(ObservedView.session_id == session_id).count() == 0
    finally:
        db.close()

    session = client.get(f"/sessions/{session_id}").json()
    assert session["coverage"]["front_exterior"] == "missing"
    family = next(e for e in session["evidence"] if e["field"] == "model_family")
    assert family["status"] == "unknown"
    assert family["superseded"][0]["state"] == "legacy_unverified"
    assert session["gate_status"] == "needs_evidence"
