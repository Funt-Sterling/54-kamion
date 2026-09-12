from data_pipeline.partition import assign_partition
from data_pipeline.pipeline import clean_and_dedupe, is_excluded_listing_text
from data_pipeline.schema import ListingRecord


def _record(**overrides):
    defaults = dict(
        source_url="https://example.test/1",
        location="Istanbul, TR",
        make="Mercedes-Benz",
        model="Actros 1845",
        model_family="actros",
        year=2019,
        mileage_km=480_000,
        axle_config="4x2",
        price=1_250_000,
        currency="TRY",
        vat_basis="vat_excluded",
    )
    defaults.update(overrides)
    return ListingRecord(**defaults)


def test_valid_record_has_no_problems():
    assert _record().validate() == []


def test_non_turkish_location_is_flagged():
    problems = _record(country="DE").validate()
    assert any("Turkish" in p for p in problems)


def test_implausible_mileage_is_flagged():
    problems = _record(mileage_km=50_000_000).validate()
    assert any("mileage" in p for p in problems)


def test_dedupe_drops_near_identical_reposts():
    a = _record(source_url="https://example.test/1")
    b = _record(source_url="https://example.test/1-reposted", price=1_250_500)  # same bucket
    cleaned = list(clean_and_dedupe([a, b]))
    assert len(cleaned) == 1


def test_dedupe_keeps_genuinely_different_vehicles():
    a = _record(mileage_km=480_000)
    b = _record(mileage_km=680_000)  # different bucket
    cleaned = list(clean_and_dedupe([a, b]))
    assert len(cleaned) == 2


def test_invalid_records_are_dropped_not_raised():
    bad = _record(price=-1)
    assert list(clean_and_dedupe([bad])) == []


def test_partition_assignment_is_deterministic():
    record = _record()
    assert assign_partition(record) == assign_partition(record)


def test_partition_keeps_same_vehicle_together_across_listings():
    # Two listings of "the same vehicle" (e.g. a repost with a nudged price)
    # must land in the same partition even though dedupe already drops one —
    # this checks the partition key itself, independent of dedupe.
    a = _record(source_url="https://example.test/1", price=1_250_000)
    b = _record(source_url="https://example.test/2", price=1_251_000)
    assert assign_partition(a) == assign_partition(b)


def test_excluded_listing_text_detected_in_turkish_and_english():
    assert is_excluded_listing_text("Fiyat Sorunuz") is True
    assert is_excluded_listing_text("Price on request") is True
    assert is_excluded_listing_text("Kapora alınır") is True
    assert is_excluded_listing_text("2019 Mercedes Actros, temiz") is False
