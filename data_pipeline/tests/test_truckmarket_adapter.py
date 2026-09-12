"""Tests for the truckmarket.com.tr adapter and the rejection rules that
protect the comparable pool.

Parsing is tested against a saved fixture of the real page markup rather
than the live site, so these stay fast, deterministic, and runnable
offline — and so a future site redesign shows up as a failing test with a
readable diff rather than a silently empty dataset.
"""

from data_pipeline.pipeline import clean_and_dedupe
from data_pipeline.schema import ListingRecord
from data_pipeline.sources.truckmarket_adapter import TruckMarketAdapter

# Trimmed from a real https://www.truckmarket.com.tr/arac-detay/13824 response.
# The leading block reproduces the search sidebar, which reuses the same
# label words ("Marka", "Araç Tipi") as the spec block below it — that
# collision is exactly what the parser has to get right.
DETAIL_FIXTURE = """
<html><body>
  <div class="search-sidebar">
    <p>Araç Ara</p>
    <label>Araç Tipi</label>
    <label>Marka</label>
    <label>Tüm Modeller</label>
  </div>
  <div class="detail">
    <h1>2021 /  FORD / F-MAX</h1>
    <span>2.500.000 ₺</span>
    <span>344045 Km</span>
    <div class="tabs">Genel Bakış</div>
    <ul>
      <li>FORD</li><li>Marka</li>
      <li>F-MAX</li><li>Model</li>
      <li>Çekici</li><li>Araç Tipi</li>
      <li>2021</li><li>Model Yılı</li>
      <li>344045</li><li>Km</li>
      <li>Otomatik</li><li>Vites Tipi</li>
      <li>BEYAZ</li><li>Renk</li>
      <li>İstanbul</li><li>Şehir</li>
      <li>4x2</li><li>Çekiş Tipi</li>
    </ul>
    <div>Stok Bilgileri</div>
    <ul><li>Garanti</li><li>Hayır</li><li>İlan Numarası</li><li>13824</li></ul>
  </div>
</body></html>
"""

URL = "https://www.truckmarket.com.tr/arac-detay/13824"


def _parse(html: str = DETAIL_FIXTURE, listing_id: str = "13824"):
    return TruckMarketAdapter().parse_detail(html, URL, listing_id)


def _record(**overrides) -> ListingRecord:
    defaults = dict(
        source="truckmarket_com_tr",
        source_url=URL,
        listing_id="13824",
        location="İstanbul, TR",
        make="FORD",
        model="F-MAX",
        model_family="f-max",
        vehicle_type="Çekici",
        year=2021,
        mileage_km=344_045,
        axle_config="4x2",
        price=2_500_000.0,
        currency="TRY",
        vat_basis="unknown",
    )
    defaults.update(overrides)
    return ListingRecord(**defaults)


def test_parses_every_pricing_field_from_real_markup():
    record = _parse()
    assert record is not None
    assert record.make == "FORD"
    assert record.model == "F-MAX"
    assert record.model_family == "f-max"
    assert record.year == 2021
    assert record.mileage_km == 344_045
    assert record.axle_config == "4x2"
    assert record.price == 2_500_000.0
    assert record.currency == "TRY"
    assert record.location == "İstanbul, TR"
    assert record.country == "TR"


def test_sidebar_labels_do_not_leak_into_specs():
    """The filter sidebar repeats 'Marka'/'Araç Tipi' above the spec block;
    reading those would yield UI captions instead of vehicle data."""
    record = _parse()
    assert record is not None
    assert record.make != "Araç Ara"
    assert record.vehicle_type == "Çekici"


def test_listing_id_comes_from_the_url_not_the_stock_block():
    """The 'Stok Bilgileri' block renders label-then-value (inverted vs.
    the spec block), so parsing an id out of it grabs 'Hayır'."""
    record = _parse()
    assert record is not None
    assert record.listing_id == "13824"


def test_vat_basis_is_unknown_never_invented():
    """truckmarket does not publish a VAT basis — claiming one would let
    incompatible tax bases silently merge in the comparable pool."""
    record = _parse()
    assert record is not None
    assert record.vat_basis == "unknown"


def test_wrong_vehicle_type_is_rejected():
    html = DETAIL_FIXTURE.replace("<li>Çekici</li>", "<li>Kamyon</li>")
    assert _parse(html) is None


def test_missing_price_is_rejected():
    html = DETAIL_FIXTURE.replace("2.500.000 ₺", "Fiyat Sorunuz")
    assert _parse(html) is None


def test_price_on_request_is_rejected():
    html = DETAIL_FIXTURE.replace("<span>2.500.000 ₺</span>", "<span>Fiyat Sorunuz</span>")
    assert _parse(html) is None


def test_malformed_year_is_rejected():
    html = DETAIL_FIXTURE.replace("<li>2021</li><li>Model Yılı</li>", "<li>n/a</li><li>Model Yılı</li>")
    assert _parse(html) is None


def test_missing_city_is_rejected():
    html = DETAIL_FIXTURE.replace("<li>İstanbul</li><li>Şehir</li>", "<li></li><li>Şehir</li>")
    assert _parse(html) is None


def test_missing_axle_config_is_rejected_not_guessed():
    """~3/4 of live truckmarket listings omit 'Çekiş Tipi'. Those are
    dropped: the pricing engine hard-filters on axle config, so inferring
    "probably 4x2" would put an invented spec behind a real price."""
    html = DETAIL_FIXTURE.replace("<li>4x2</li><li>Çekiş Tipi</li>", "<li>Çekiş Tipi</li>")
    assert _parse(html) is None


def test_generic_placeholder_model_is_rejected():
    """'FORD / TRUCKS' names a brand, not a model line — it could be an
    F-MAX or an old Cargo, so it can't join any comparable family."""
    html = DETAIL_FIXTURE.replace("<li>F-MAX</li><li>Model</li>", "<li>TRUCKS</li><li>Model</li>")
    assert _parse(html) is None


def test_non_turkish_listing_is_rejected_by_validation():
    """Nothing from this adapter should be non-TR, but the guard has to
    hold regardless of which source a record came from."""
    foreign = _record(country="DE", location="Hamburg, DE")
    assert any("Turkish" in problem for problem in foreign.validate())
    assert list(clean_and_dedupe([foreign])) == []


def test_malformed_price_is_rejected_by_validation():
    assert list(clean_and_dedupe([_record(price=0.0)])) == []
    assert list(clean_and_dedupe([_record(price=-5.0)])) == []


def test_same_listing_id_is_deduplicated():
    """A repost under the same source listing id is the same vehicle even
    if the asking price was nudged between crawls."""
    first = _record(price=2_500_000.0)
    repost = _record(price=2_450_000.0)  # same listing_id, different price
    assert len(list(clean_and_dedupe([first, repost]))) == 1


def test_different_listings_both_survive():
    a = _record(listing_id="13824", mileage_km=344_045, price=2_500_000.0)
    b = _record(listing_id="16075", mileage_km=382_917, price=2_050_000.0)
    assert len(list(clean_and_dedupe([a, b]))) == 2
