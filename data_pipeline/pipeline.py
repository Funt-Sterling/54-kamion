"""Cleaning pass shared by every adapter's output: drops invalid/excluded
records and deduplicates reposts, so no adapter has to reimplement the
rules in docs/plan.md's "Important collection rules"."""

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

from .schema import EXCLUDED_LISTING_MARKERS, ListingRecord


def is_excluded_listing_text(raw_text: str) -> bool:
    """Check a listing's raw title/price text for the markers that should
    exclude it (deposit, price-on-request, new, salvage, ...)."""
    lowered = raw_text.lower()
    return any(marker in lowered for marker in EXCLUDED_LISTING_MARKERS)


def _dedupe_key(record: ListingRecord) -> tuple:
    """Near-duplicate key for reposts: same make/model/year within a coarse
    mileage/price bucket. Coarse on purpose — a repost is rarely byte-for-byte
    identical (price gets nudged, mileage updates), but a genuinely different
    truck of the same make/model/year essentially never lands in the same
    1,000 km AND 1,000-currency-unit bucket."""
    mileage_bucket = round(record.mileage_km, -3)
    price_bucket = round(record.price, -3)
    return (record.make.strip().lower(), record.model.strip().lower(), record.year, mileage_bucket, price_bucket)


def clean_and_dedupe(records: Iterable[ListingRecord]) -> Iterator[ListingRecord]:
    seen = set()
    for record in records:
        if record.validate():
            continue
        key = _dedupe_key(record)
        if key in seen:
            continue
        seen.add(key)
        yield record


def write_csv(records: Iterable[ListingRecord], out_path: str | Path) -> int:
    records = list(records)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        out_path.write_text("")
        return 0
    fieldnames = list(records[0].as_dict().keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            row = r.as_dict()
            row["image_refs"] = "|".join(row["image_refs"])
            writer.writerow(row)
    return len(records)
