"""RED regressions for the audited evidence-boundary defects.

Every test here encodes behaviour the system MUST have. They are written
against the real parser, resolver, and HTTP handlers — not against mocks of
our own logic — so they fail on the implementation that shipped and pass
only once the boundary is actually fixed.

Scripted model output is used deliberately: these are deterministic
regressions about how the software *treats* a proposal. They prove nothing
about whether a real model reads a real badge correctly, and must never be
cited as visual accuracy.

Audit case numbers from docs/audits/2026-09-12-system-audit.md section 4.
"""

import pytest

from app.services.canonical import canonicalize
from app.services.evidence import values_agree

from .helpers import make_image, patch_vision_adapter, truck_result, upload_media


# --- Case 3: numeric coercion -------------------------------------------------


def test_float_mileage_is_never_multiplied_by_ten():
    """`365000.0` must not become 3,650,000.

    The shipped parser stripped `.` to handle thousands separators, turning
    a float into a ten-times-larger integer — an error that lands squarely
    in the number used to select the comparable population.
    """
    from app.services.admission import _parse_strict_int

    assert _parse_strict_int("365000.0") != 3650000
    # Ambiguous separator: abstain rather than guess.
    assert _parse_strict_int("365000.0") is None
    # Unambiguous thousands grouping is still accepted.
    assert _parse_strict_int("365.000") == 365000
    assert _parse_strict_int("365000") == 365000


def test_float_year_is_never_corrupted():
    from app.services.admission import _parse_strict_int

    assert _parse_strict_int("2019.0") != 20190


# --- Case 3: unknown visibility must not become "clear" -----------------------


def test_missing_finding_visibility_is_a_schema_error_not_clear():
    """The most confident value must not be the one reachable by omission."""
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(findings=[{"component": "chassis", "observation": "rail looks bent"}])
    with pytest.raises(ProposalSchemaError, match="visibility"):
        parse_proposal(payload)


def test_unknown_view_visibility_is_rejected():
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(views={"front": {"visibility": "probably", "usable": True}})
    with pytest.raises(ProposalSchemaError):
        parse_proposal(payload)


def test_missing_required_view_key_is_a_schema_error():
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(drop_views=["odometer"])
    with pytest.raises(ProposalSchemaError, match="odometer"):
        parse_proposal(payload)


def test_unknown_top_level_field_is_rejected():
    """A model that invents `estimated_price` must not slip it through."""
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(extra={"estimated_price": "2500000"})
    with pytest.raises(ProposalSchemaError, match="unknown field"):
        parse_proposal(payload)


# --- Case 4: badge cannot promote an unrelated family -------------------------


def test_scania_badge_cannot_admit_observed_f_max():
    """Reproduced defect: any non-empty badge text upgraded the model guess.

    A readable SCANIA badge with an F-MAX guess must not produce observed
    F-MAX — that selects an entirely wrong price population.
    """
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        readings=[
            {"kind": "badge", "raw_text": "SCANIA", "readability": "readable", "supporting_view": "badge"}
        ],
        candidates=[{"field": "model_family", "value": "F-MAX", "basis": "appearance"}],
        views={"badge": {"visibility": "visible", "usable": True}},
    )
    result = admit(parse_proposal(payload))

    family = result.claim_for("model_family")
    assert family is None or family.provenance != "observed_from_photo", (
        "a SCANIA badge must never support an observed F-MAX identity"
    )
    assert any("contradicts" in r.reason for r in result.rejected)


def test_appearance_only_identity_stays_a_candidate():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(candidates=[{"field": "model_family", "value": "F-MAX", "basis": "appearance"}])
    result = admit(parse_proposal(payload))
    family = result.claim_for("model_family")
    assert family is not None
    assert family.provenance == "inferred_candidate"


# --- Case 2/3: mileage needs a readable total odometer ------------------------


def test_mileage_without_visible_odometer_is_rejected():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"odometer": {"visibility": "absent", "usable": False}},
        readings=[
            {
                "kind": "total_odometer",
                "raw_text": "365000",
                "readability": "readable",
                "unit": "km",
                "is_total": True,
            }
        ],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("mileage_km") is None
    assert any("odometer view is not visible" in r.reason for r in result.rejected)


def test_mileage_without_unit_is_rejected():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"odometer": {"visibility": "visible", "usable": True}},
        readings=[
            {"kind": "total_odometer", "raw_text": "365000", "readability": "readable", "is_total": True}
        ],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("mileage_km") is None
    assert any("no unit" in r.reason for r in result.rejected)


def test_trip_meter_is_not_admitted_as_total_mileage():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"odometer": {"visibility": "visible", "usable": True}},
        readings=[
            {
                "kind": "total_odometer",
                "raw_text": "412",
                "readability": "readable",
                "unit": "km",
                "is_total": False,
            }
        ],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("mileage_km") is None


def test_readable_odometer_credits_odometer_but_not_dashboard():
    """Case 2: a tight crop must not invent wide-dashboard coverage."""
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={
            "odometer": {"visibility": "visible", "usable": True},
            "dashboard": {"visibility": "unclear", "usable": False},
        },
        readings=[
            {
                "kind": "total_odometer",
                "raw_text": "365000",
                "readability": "readable",
                "unit": "km",
                "is_total": True,
                "supporting_view": "odometer",
                "region": {"x": 40, "y": 80, "width": 220, "height": 60},
            }
        ],
    )
    result = admit(parse_proposal(payload))
    credited = {view.view for view in result.views}

    assert "odometer" in credited
    assert "dashboard" not in credited
    mileage = result.claim_for("mileage_km")
    assert mileage is not None and mileage.canonical_value == "365000"


def test_mileage_without_image_region_is_rejected():
    """The contract requires a locatable reading so a person can check it."""
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"odometer": {"visibility": "visible", "usable": True}},
        readings=[
            {
                "kind": "total_odometer",
                "raw_text": "365000",
                "readability": "readable",
                "unit": "km",
                "is_total": True,
                "supporting_view": "odometer",
            }
        ],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("mileage_km") is None
    assert any("no image region" in r.reason for r in result.rejected)


def test_badge_read_off_an_invisible_view_is_not_identity_evidence():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"front": {"visibility": "visible", "usable": True}},
        readings=[{"kind": "badge", "raw_text": "F-MAX", "readability": "readable", "supporting_view": "badge"}],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("model_family") is None


def test_missing_finding_severity_is_a_schema_error_not_info():
    from app.services.proposal import ProposalSchemaError, parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        findings=[{"component": "chassis", "observation": "rail looks bent", "visibility": "visible"}]
    )
    with pytest.raises(ProposalSchemaError, match="severity"):
        parse_proposal(payload)


# --- Case 5: geometry supports a count, not a layout --------------------------


def test_frontal_badge_photo_cannot_establish_axle_layout():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"front": {"visibility": "visible", "usable": True}, "badge": {"visibility": "visible", "usable": True}},
        axle_geometry={"visible_axle_count": 2, "whole_relevant_geometry_visible": False},
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("visible_axle_count") is None
    assert result.claim_for("axle_config") is None


def test_three_visible_axles_do_not_prove_6x2_or_6x4():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        views={"side": {"visibility": "visible", "usable": True}},
        axle_geometry={
            "visible_axle_count": 3,
            "whole_relevant_geometry_visible": True,
            "supporting_view": "side",
        },
    )
    result = admit(parse_proposal(payload))

    count = result.claim_for("visible_axle_count")
    assert count is not None and count.canonical_value == "3"
    # A count is not a configuration.
    assert result.claim_for("axle_config") is None


# --- Case 1: a tire photo credits tires only ----------------------------------


def test_tire_proposal_credits_tire_not_front():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    payload = proposal_json(
        extent="partial",
        views={"tire": {"visibility": "visible", "usable": True}},
    )
    result = admit(parse_proposal(payload))
    credited = {view.view for view in result.views}

    assert credited == {"tire"}
    # A closeup cannot establish that this is a tractor unit at all.
    assert result.claim_for("vehicle_category") is None


# --- Case 6: aliases ----------------------------------------------------------


@pytest.mark.parametrize("alias", ["F-MAX", "f-max", "F Max", "FMAX", "f_max"])
def test_all_f_max_aliases_canonicalize(alias):
    assert canonicalize("model_family", alias).canonical == "f-max"


def test_distinct_families_never_merge():
    assert canonicalize("model_family", "Actros").canonical == "actros"
    assert canonicalize("model_family", "R-series").canonical == "r-series"
    assert canonicalize("model_family", "actros").canonical != canonicalize("model_family", "R Series").canonical


def test_unknown_family_stays_unresolved():
    assert canonicalize("model_family", "Definitely Not A Truck").canonical is None


# --- Case 10: mileage discrepancy is not agreement ----------------------------


def test_850k_versus_1m_km_is_a_discrepancy_not_agreement():
    """The shipped 15% relative tolerance called these equal — at 1,000,000
    km that band is ±150,000 km, which is most of a truck's useful life."""
    assert values_agree("mileage_km", "850000", "1000000") is False


def test_identical_mileage_still_agrees():
    assert values_agree("mileage_km", "365000", "365000") is True


# --- Case 8: the public API cannot forge visual provenance --------------------


def test_details_endpoint_cannot_forge_observed_provenance(client):
    session_id = client.post("/sessions").json()["id"]
    response = client.patch(
        f"/sessions/{session_id}/details",
        json={"field": "mileage_km", "value": "365000", "provenance": "observed_from_photo"},
    )
    assert response.status_code in (200, 422)

    detail = client.get(f"/sessions/{session_id}").json()
    mileage = next(e for e in detail["evidence"] if e["field"] == "mileage_km")
    assert mileage["observed_from_photo"] is None, "a caller must not be able to mint photo evidence"


def test_details_endpoint_rejects_fields_outside_the_allowlist(client):
    session_id = client.post("/sessions").json()["id"]
    response = client.patch(
        f"/sessions/{session_id}/details",
        json={"field": "vehicle_category", "value": "tractor_unit"},
    )
    assert response.status_code == 422


# --- Case 1 (HTTP): requested view must not become coverage -------------------


def test_front_request_answered_with_a_tire_does_not_credit_front(client, monkeypatch, tmp_path):
    """End to end: ask for the front, receive a tire, and the front must
    still be missing while tires become covered."""
    session_id = client.post("/sessions").json()["id"]
    patch_vision_adapter(
        monkeypatch,
        lambda path, hint: truck_result(
            subject_extent="partial",
            visible_views={"tire"},
        ),
    )
    upload_media(client, session_id, make_image(tmp_path / "tire.jpg"), component_hint="front_exterior")

    coverage = client.get(f"/sessions/{session_id}").json()["coverage"]
    assert coverage["tire"] == "captured"
    assert coverage["front_exterior"] == "missing"


# --- Review follow-ups ------------------------------------------------------------


def test_two_different_total_readings_in_one_image_admit_neither():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    region = {"x": 1, "y": 1, "width": 50, "height": 20}
    reading = {"kind": "total_odometer", "readability": "readable", "unit": "km", "is_total": True, "supporting_view": "odometer", "region": region}
    payload = proposal_json(
        views={"odometer": {"visibility": "visible", "usable": True}},
        readings=[{**reading, "raw_text": "365000"}, {**reading, "raw_text": "465000"}],
    )
    result = admit(parse_proposal(payload))
    assert result.claim_for("mileage_km") is None
    assert any("different readings" in r.reason for r in result.rejected)


def test_user_correction_does_not_retire_a_seller_declaration():
    from app.models import EvidenceRecord
    from app.services.evidence import _supersedes

    seller = EvidenceRecord(id="e1", field="year", value="2019", provenance="seller_declared")
    correction = EvidenceRecord(id="e2", field="year", value="2020", provenance="user_corrected")
    later_seller = EvidenceRecord(id="e3", field="year", value="2021", provenance="seller_declared")
    assert _supersedes(correction, seller) is None
    assert _supersedes(later_seller, seller) is not None


def test_appearance_make_is_only_a_candidate():
    from app.services.admission import admit
    from app.services.proposal import parse_proposal

    from .helpers import proposal_json

    result = admit(parse_proposal(proposal_json(candidates=[{"field": "make", "value": "Ford", "basis": "appearance"}])))
    make = result.claim_for("make")
    assert make is not None and make.provenance == "inferred_candidate"


def test_migration_dry_run_previews_backfill_and_demotion_on_a_pre_contract_db(tmp_path, capsys):
    import sqlite3

    from scripts.migrate_evidence_contract import migrate

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (id VARCHAR PRIMARY KEY);
        CREATE TABLE media_items (id VARCHAR PRIMARY KEY, session_id VARCHAR, component_tag VARCHAR);
        CREATE TABLE evidence_records (id VARCHAR PRIMARY KEY, session_id VARCHAR, field VARCHAR, value VARCHAR, provenance VARCHAR, media_id VARCHAR);
        CREATE TABLE appraisals (id VARCHAR PRIMARY KEY);
        INSERT INTO sessions VALUES ('s1');
        INSERT INTO media_items VALUES ('m1', 's1', 'front_exterior');
        INSERT INTO evidence_records VALUES ('e1', 's1', 'model_family', 'actros', 'observed_from_photo', 'm1');
        INSERT INTO evidence_records VALUES ('e2', 's1', 'year', '2019', 'seller_declared', NULL);
        """
    )
    conn.close()
    before = db.read_bytes()

    migrate(db, dry_run=True)
    preview = capsys.readouterr().out
    assert db.read_bytes() == before
    assert "client_hint = component_tag" in preview and "(1 row(s))" in preview
    assert "demote 1 pre-contract" in preview

    migrate(db, dry_run=False)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT client_hint FROM media_items").fetchone()[0] == "front_exterior"
    states = dict(conn.execute("SELECT id, state FROM evidence_records").fetchall())
    assert states == {"e1": "legacy_unverified", "e2": "active"}
    assert conn.execute("SELECT COUNT(*) FROM observed_views").fetchone()[0] == 0
    conn.close()
