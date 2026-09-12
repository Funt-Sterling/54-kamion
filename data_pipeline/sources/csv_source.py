"""Adapter for hand-collected listings recorded in a CSV template.

This is the always-available fallback: it needs no network access and no
scraper to be finished, so it's the fastest way to hit the plan's "hour-4
checkpoint: one working source adapter and at least 50 clean records" if a
scraper isn't ready in time. See ../templates/manual_listings.csv for the
column format (same shape as ListingRecord).
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from ..schema import ListingRecord
from .base import SourceAdapter


class CSVSourceAdapter(SourceAdapter):
    name = "manual_csv"

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)

    def fetch(self, limit: int | None = None) -> Iterator[ListingRecord]:
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if limit is not None and i >= limit:
                    return
                yield ListingRecord(
                    source_url=row["source_url"],
                    location=row["location"],
                    country=row.get("country") or "TR",
                    make=row["make"],
                    model=row["model"],
                    model_family=row["model_family"],
                    category=row.get("category") or "tractor_unit",
                    year=int(row["year"]),
                    mileage_km=int(row["mileage_km"]),
                    axle_config=row["axle_config"],
                    price=float(row["price"]),
                    currency=row.get("currency") or "TRY",
                    vat_basis=row.get("vat_basis") or "unknown",
                    listing_date=row.get("listing_date") or None,
                    image_refs=[u for u in (row.get("image_refs") or "").split("|") if u],
                )
