"""Unit tests for the evidence-resolution logic (Milestone 1): given a set
of provenanced facts about one field, does resolve_field() correctly land
on unknown / a single source / confirmed / conflicting? These are pure
functions — no DB, no HTTP — so they're fast and isolate the logic from the
gate/router wiring tested in test_e2e_flow.py."""

from datetime import datetime, timedelta, timezone

from app.models import EvidenceRecord
from app.services.evidence import resolve_field, values_agree

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(field, value, provenance, media_id=None, offset_seconds=0):
    return EvidenceRecord(
        session_id="sess_test",
        field=field,
        value=value,
        provenance=provenance,
        media_id=media_id,
        created_at=BASE_TIME + timedelta(seconds=offset_seconds),
    )


def test_no_records_is_unknown():
    result = resolve_field("mileage_km", [])
    assert result.status == "unknown"
    assert result.value is None


def test_single_seller_declared_value():
    records = [_record("mileage_km", "250000", "seller_declared")]
    result = resolve_field("mileage_km", records)
    assert result.status == "seller_declared"
    assert result.value == "250000"


def test_single_observed_from_photo_value():
    records = [_record("mileage_km", "480000", "observed_from_photo", media_id="media_1")]
    result = resolve_field("mileage_km", records)
    assert result.status == "observed_from_photo"
    assert result.value == "480000"
    assert result.supporting_media_id == "media_1"


def test_seller_and_photo_with_identical_readings_is_source_agreement():
    """Agreement is reported as "sources_agree" — corroboration between a
    seller and a photo, never "verified" lifetime mileage."""
    records = [
        _record("mileage_km", "480000", "seller_declared"),
        _record("mileage_km", "480 000", "observed_from_photo", media_id="media_1"),
    ]
    result = resolve_field("mileage_km", records)
    assert result.status == "sources_agree"
    assert result.value == "480 000"  # photo evidence preferred as the display value
    assert result.canonical_value is not None


def test_seller_mileage_conflicts_with_observed_mileage():
    """The exact scenario from the spec: seller says 250,000 km, the
    dashboard photo reads 650,000 km — must not be silently resolved."""
    records = [
        _record("mileage_km", "250000", "seller_declared", offset_seconds=0),
        _record("mileage_km", "650000", "observed_from_photo", media_id="media_odo", offset_seconds=1),
    ]
    result = resolve_field("mileage_km", records)
    assert result.status == "conflicting"
    assert result.value is None  # never silently pick one
    assert result.seller_declared == "250000"
    assert result.observed_from_photo == "650000"
    assert result.supporting_media_id == "media_odo"


def test_small_mileage_difference_is_a_discrepancy_not_agreement():
    """The old 15% band called these the same fact. They are not."""
    records = [
        _record("mileage_km", "480000", "seller_declared"),
        _record("mileage_km", "490000", "observed_from_photo", media_id="media_1"),
    ]
    result = resolve_field("mileage_km", records)
    assert result.status == "conflicting"
    assert {p["raw_value"] for p in result.participants} == {"480000", "490000"}


def test_year_requires_exact_match():
    records = [
        _record("year", "2019", "seller_declared"),
        _record("year", "2020", "observed_from_photo"),
    ]
    result = resolve_field("year", records)
    assert result.status == "conflicting"


def test_single_inferred_candidate_is_usable_but_labeled():
    records = [_record("model_family", "actros", "inferred_candidate", media_id="media_1")]
    result = resolve_field("model_family", records)
    assert result.status == "inferred_candidate"
    assert result.value == "actros"


def test_multiple_disagreeing_inferred_candidates_is_conflicting():
    records = [
        _record("model_family", "actros", "inferred_candidate", media_id="media_1", offset_seconds=0),
        _record("model_family", "tgx", "inferred_candidate", media_id="media_2", offset_seconds=1),
    ]
    result = resolve_field("model_family", records)
    assert result.status == "conflicting"
    assert set(result.inferred_candidates) == {"actros", "tgx"}


def test_string_field_agreement_is_case_insensitive():
    assert values_agree("model_family", "Actros", "actros") is True
    assert values_agree("model_family", "Actros", "TGX") is False


def test_mileage_has_no_tolerance_band():
    assert values_agree("mileage_km", "100", "114") is False
    assert values_agree("mileage_km", "850000", "1000000") is False
    assert values_agree("mileage_km", "365 000", "365000") is True


def test_repeated_analysis_of_one_image_is_not_independent_agreement():
    records = [
        _record("model_family", "F-MAX", "observed_from_photo", media_id="media_1", offset_seconds=0),
        _record("model_family", "FMAX", "observed_from_photo", media_id="media_1", offset_seconds=1),
    ]
    result = resolve_field("model_family", records)
    assert result.status == "observed_from_photo"


def test_superseded_records_are_history_not_participants():
    old = _record("model_family", "r-series", "inferred_candidate", media_id="media_1")
    old.state = "superseded"
    old.state_reason = "superseded by a photo-supported observation of the same vehicle"
    new = _record("model_family", "F-MAX", "observed_from_photo", media_id="media_2", offset_seconds=1)
    result = resolve_field("model_family", [old, new])
    assert result.status == "observed_from_photo"
    assert [p["raw_value"] for p in result.participants] == ["F-MAX"]
    assert [h["raw_value"] for h in result.superseded] == ["r-series"]
