"""Adapter for truckmarket.com.tr — Ford Trucks Türkiye's own certified
used-vehicle portal.

Chosen as the first real source after a head-to-head check against
truck1.com.tr (see data_pipeline/README.md for the bake-off numbers). The
short version: truckmarket's inventory is Turkey-located by construction,
every listing carries an explicit `Araç Tipi` (vehicle type), `Şehir`
(city) and `Çekiş Tipi` (drive configuration) field, and its listing ids
are stable integers — so tractor units can be selected on the site's own
label instead of being inferred from a title string.

Collection etiquette: one request at a time, rate-limited, honest
User-Agent, no anti-bot evasion of any kind. If this site ever starts
challenging requests, stop and renegotiate rather than working around it.

Note on VAT: truckmarket does not publish a VAT/tax basis per listing, so
every record from here is `vat_basis="unknown"`. That is deliberate — the
pricing engine only ever compares listings that share a basis, so unknown
matches unknown and never silently mixes with a VAT-inclusive listing from
some future source. Do not "fill in" a basis this site does not state.
"""

from __future__ import annotations

import html as html_lib
import re
import time
from collections.abc import Iterator

import requests

from ..pipeline import is_excluded_listing_text
from ..schema import ListingRecord
from .base import SourceAdapter

BASE_URL = "https://www.truckmarket.com.tr"
LIST_PATH = "/arac-listesi"
DETAIL_PATH = "/arac-detay/{listing_id}"

USER_AGENT = "TIRageHackathonBot/0.1 (used-truck appraisal research; contact: hack@kamion.co)"

# The site labels tractor units "Çekici". Anything else (Kamyon, Kamyonet,
# Otobüs, Römork...) is out of scope for this appraiser.
TRACTOR_UNIT_LABEL = "çekici"

# Some listings leave the model unspecified and fall back to a brand- or
# category-level word. Those can't be assigned to a comparable family — a
# "Ford / TRUCKS" record could be an F-MAX or an old Cargo — so they are
# dropped rather than pooled into a meaningless family.
GENERIC_MODEL_VALUES = {"trucks", "truck", "kamyon", "çekici", "cekici", "diğer", "diger", "other", "-"}

# "2.050.000 ₺" -> 2050000.0 ; Turkish formatting uses "." as the thousands
# separator, so it is stripped rather than treated as a decimal point.
_PRICE_RE = re.compile(r"([\d.]+)\s*₺")
_KM_RE = re.compile(r"([\d.]+)\s*Km", re.IGNORECASE)
_DETAIL_LINK_RE = re.compile(r"/arac-detay/(\d+)")


def _clean_text(raw_html: str) -> list[str]:
    """Strip tags/scripts and return the visible text as trimmed lines."""
    without_scripts = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw_html, flags=re.S)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", without_scripts))
    return [line.strip() for line in text.split("\n") if line.strip()]


def _parse_turkish_number(value: str) -> float | None:
    digits = value.replace(".", "").replace(" ", "").strip()
    if not digits.isdigit():
        return None
    return float(digits)


def _labelled_fields(lines: list[str]) -> dict[str, str]:
    """truckmarket's spec block renders as alternating value/label pairs:

        '2021', 'Model Yılı', '344045', 'Km', 'İstanbul', 'Şehir', '4x2', 'Çekiş Tipi'

    so each label is read by looking at the line *before* it.

    Scanning starts at the "Genel Bakış" section header on purpose: the
    search sidebar higher up the page reuses the very same label words
    ("Marka", "Araç Tipi") as filter captions, and reading those would
    silently pick up filter UI text instead of this vehicle's specs.
    """
    wanted = {
        "Marka": "make",
        "Model": "model",
        "Araç Tipi": "vehicle_type",
        "Model Yılı": "year",
        "Km": "mileage_km",
        "Şehir": "city",
        "Çekiş Tipi": "axle_config",
        "Vites Tipi": "transmission",
        "Renk": "color",
    }
    # "İlan Numarası" is deliberately not read here: it sits in the
    # "Stok Bilgileri" block, which renders label-then-value (the inverse of
    # "Genel Bakış"), so the pair-walking above would grab the wrong line.
    # The id in the URL is authoritative and already known to the caller.

    start = 0
    for index, line in enumerate(lines):
        if line.strip().lower() == "genel bakış":
            start = index
            break
    spec_lines = lines[start:]

    found: dict[str, str] = {}
    for index, line in enumerate(spec_lines):
        key = wanted.get(line)
        if not key or index == 0 or key in found:
            continue
        value = spec_lines[index - 1]
        # An empty value collapses during tag-stripping, which would leave
        # the *previous label* sitting where the value should be (e.g. city
        # reading as "Renk"). Treat a label-looking value as absent instead
        # of letting it through as data.
        if value in wanted:
            continue
        found[key] = value
    return found


def _model_family(model: str) -> str:
    """Normalize to the slug the pricing engine groups comparables by.
    'F-MAX' -> 'f-max'; keeps distinct model lines genuinely distinct."""
    return model.strip().lower()


class TruckMarketAdapter(SourceAdapter):
    name = "truckmarket_com_tr"

    def __init__(self, request_delay_seconds: float = 1.5, timeout_seconds: float = 25.0):
        self.request_delay_seconds = request_delay_seconds
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def _get(self, url: str) -> str:
        response = self._session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    def listing_ids(self) -> list[str]:
        """Every listing id on the index page, in page order, deduplicated."""
        index_html = self._get(f"{BASE_URL}{LIST_PATH}")
        seen: list[str] = []
        for listing_id in _DETAIL_LINK_RE.findall(index_html):
            if listing_id not in seen:
                seen.append(listing_id)
        return seen

    def fetch(self, limit: int | None = None) -> Iterator[ListingRecord]:
        ids = self.listing_ids()
        if limit is not None:
            ids = ids[:limit]

        for position, listing_id in enumerate(ids):
            if position:
                time.sleep(self.request_delay_seconds)
            url = f"{BASE_URL}{DETAIL_PATH.format(listing_id=listing_id)}"
            try:
                detail_html = self._get(url)
            except requests.RequestException:
                # A single unreachable listing must not abort the crawl; it
                # is simply absent from the dataset rather than half-parsed.
                continue
            record = self.parse_detail(detail_html, url, listing_id)
            if record is not None:
                yield record

    def parse_detail(self, detail_html: str, url: str, listing_id: str) -> ListingRecord | None:
        """Turn one detail page into a ListingRecord, or None if this
        listing should not enter the dataset. Separated from fetch() so it
        can be tested against saved HTML without network access."""
        lines = _clean_text(detail_html)
        fields = _labelled_fields(lines)

        vehicle_type = fields.get("vehicle_type", "")
        if TRACTOR_UNIT_LABEL not in vehicle_type.strip().lower():
            return None  # not a tractor unit

        make = fields.get("make", "").strip()
        model = fields.get("model", "").strip()
        city = fields.get("city", "").strip()
        # Not every listing publishes a drive configuration. When it's
        # absent the record is dropped rather than guessed: the pricing
        # engine hard-filters on axle config, and "probably 4x2 because
        # that's the common F-MAX spec" is exactly the kind of invented
        # attribute this project refuses to put behind a price.
        axle_config = fields.get("axle_config", "").strip()
        if not (make and model and city and axle_config):
            return None
        if model.lower() in GENERIC_MODEL_VALUES or model.lower() == make.lower():
            return None

        year_raw = fields.get("year", "")
        mileage_raw = fields.get("mileage_km", "")
        if not year_raw.strip().isdigit():
            return None
        mileage = _parse_turkish_number(mileage_raw)
        if mileage is None:
            return None

        price = self._extract_price(detail_html, lines)
        if price is None:
            return None

        return ListingRecord(
            source=self.name,
            source_url=url,
            listing_id=listing_id,
            location=f"{city}, TR",
            country="TR",
            make=make,
            model=model,
            model_family=_model_family(model),
            vehicle_type=vehicle_type.strip(),
            category="tractor_unit",
            year=int(year_raw.strip()),
            mileage_km=int(mileage),
            axle_config=axle_config,
            price=price,
            currency="TRY",
            # Not published per listing by this source — never invented.
            vat_basis="unknown",
        )

    @staticmethod
    def _extract_price(detail_html: str, lines: list[str]) -> float | None:
        """First ₺ amount on the page is the asking price. Listings that ask
        the buyer to call, or quote a deposit/instalment instead of a price,
        are dropped rather than parsed into a misleading number."""
        for line in lines[:200]:
            if is_excluded_listing_text(line):
                return None
        match = _PRICE_RE.search(detail_html)
        if not match:
            return None
        price = _parse_turkish_number(match.group(1))
        if price is None or price <= 0:
            return None
        return price
