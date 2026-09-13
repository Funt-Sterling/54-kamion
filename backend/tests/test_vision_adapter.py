"""Regression tests for the vision response validator.

These mock the external call on purpose — they exist to pin down how the
backend behaves when a model returns something wrong, which is precisely
what you cannot arrange reliably against the live API. The live-API proof
is a separate, manual end-to-end run (see the report); nothing here claims
to substitute for it.

The load-bearing guarantee: a vision model contributes *observations* and
nothing else. It cannot set a price, cannot invent a spec field, and a
failed call can never be mistaken for a successful one that saw nothing.
"""

import pytest

from app.services.vision import (
    ALLOWED_SPEC_FIELDS,
    AnthropicVisionAdapter,
    VisionResponseError,
)

MINIMAL = '{"is_vehicle": true, "is_truck_tractor_unit": true}'


def parse(raw: str):
    return AnthropicVisionAdapter._parse(raw)


# --- malformed / unusable responses -------------------------------------------------


def test_malformed_json_raises_vision_response_error():
    with pytest.raises(VisionResponseError):
        parse("{not json at all")


def test_empty_response_raises():
    with pytest.raises(VisionResponseError):
        parse("   ")


def test_non_object_json_raises():
    with pytest.raises(VisionResponseError):
        parse("[1, 2, 3]")


def test_prose_instead_of_json_raises():
    with pytest.raises(VisionResponseError):
        parse("I'm sorry, I can't analyze this image.")


def test_missing_required_booleans_raises():
    """A response without the vehicle booleans is incomplete — it must fail
    loudly rather than default to "not a vehicle", which would silently
    reject a perfectly good photo."""
    with pytest.raises(VisionResponseError):
        parse('{"vehicle_category_guess": "tractor_unit"}')


def test_fenced_json_is_accepted():
    result = parse('```json\n{"is_vehicle": true, "is_truck_tractor_unit": true}\n```')
    assert result.is_truck_tractor_unit is True


# --- the model may never contribute a price -----------------------------------------


def test_price_fields_are_stripped_from_extracted_specs():
    """The one guarantee the whole design rests on: nothing the vision model
    says can become a price. Prices come only from comparable retrieval."""
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "extracted_specs": {"price": "2500000", "asking_price": "2500000",
                                  "estimated_value": "2.5M TRY", "mileage_km": "480000"}}"""
    specs = parse(raw).extracted_specs
    assert "price" not in specs
    assert "asking_price" not in specs
    assert "estimated_value" not in specs
    assert specs == {"mileage_km": "480000"}


def test_only_whitelisted_spec_fields_survive():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "extracted_specs": {"mileage_km": "480000", "year": "2021",
                                  "axle_config": "4x2", "colour": "white",
                                  "owner_name": "someone"}}"""
    specs = parse(raw).extracted_specs
    assert set(specs) <= ALLOWED_SPEC_FIELDS
    assert set(specs) == {"mileage_km", "year", "axle_config"}


def test_unparseable_numeric_spec_is_dropped_not_guessed():
    """"about 400k" is not a mileage reading. Dropping it leaves the field
    unknown, which the gate will then ask a photo for."""
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "extracted_specs": {"mileage_km": "about 400k", "year": "2021"}}"""
    specs = parse(raw).extracted_specs
    assert "mileage_km" not in specs
    assert specs["year"] == "2021"


def test_numeric_spec_separators_are_normalized():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "extracted_specs": {"mileage_km": "480.000"}}"""
    assert parse(raw).extracted_specs["mileage_km"] == "480000"


def test_structured_spec_values_are_rejected():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "extracted_specs": {"mileage_km": {"value": 480000}}}"""
    assert parse(raw).extracted_specs == {}


# --- missing / unusable fields stay unknown rather than becoming junk ----------------


def test_missing_optional_fields_become_none_not_invented():
    result = parse(MINIMAL)
    assert result.make_guess is None
    assert result.model_guess is None
    assert result.visible_badge_text is None
    assert result.extracted_specs == {}
    assert result.component_observations == []
    assert result.quality_issues == []


def test_placeholder_strings_are_treated_as_absent():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "make_guess": "unknown", "model_guess": "n/a", "visible_badge_text": ""}"""
    result = parse(raw)
    assert result.make_guess is None
    assert result.model_guess is None
    assert result.visible_badge_text is None


def test_damage_flags_require_explicit_true():
    """Anything other than a literal true leaves the flag off — a vague
    response must not trip an inspection_required gate."""
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "structural_damage_suspected": "maybe", "tire_concern_noted": 1}"""
    result = parse(raw)
    assert result.structural_damage_suspected is False
    assert result.tire_concern_noted is False


def test_malformed_observations_are_skipped_not_fatal():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "component_observations": ["just a string", {"component": "tire"},
                                         {"component": "grille", "observation": "clean",
                                          "visibility": "wildly-invalid"}]}"""
    observations = parse(raw).component_observations
    assert len(observations) == 1
    assert observations[0].component == "grille"
    assert observations[0].visibility == "clear"  # invalid value clamped


def test_quality_issues_must_be_a_list_of_strings():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": true,
              "quality_issues": "too_dark"}"""
    assert parse(raw).quality_issues == []


# --- unsupported vehicle --------------------------------------------------------------


def test_unsupported_vehicle_is_reported_faithfully():
    raw = """{"is_vehicle": true, "is_truck_tractor_unit": false,
              "vehicle_category_guess": "motorcycle"}"""
    result = parse(raw)
    assert result.is_vehicle is True
    assert result.is_truck_tractor_unit is False
    assert result.vehicle_category_guess == "motorcycle"
