"""CLI entry point: run one or more source adapters, clean/dedupe/partition
the results, and write data_pipeline/output/listings.csv for
backend/scripts/load_listings.py to load.

Usage:
    python -m data_pipeline.run --source truckmarket
    python -m data_pipeline.run --source manual_csv --csv templates/manual_listings.csv
    python -m data_pipeline.run --source arabam_cekici   # once the adapter is filled in
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from .partition import apply_partitions
from .pipeline import clean_and_dedupe, write_csv
from .sources.csv_source import CSVSourceAdapter

OUTPUT_PATH = Path(__file__).parent / "output" / "listings.csv"
SOURCES = ["truckmarket", "manual_csv", "arabam_cekici"]


def _build_adapter(args: argparse.Namespace):
    if args.source == "truckmarket":
        from .sources.truckmarket_adapter import TruckMarketAdapter

        return TruckMarketAdapter(request_delay_seconds=args.delay)
    if args.source == "manual_csv":
        if not args.csv:
            sys.exit("--csv is required for --source manual_csv")
        return CSVSourceAdapter(args.csv)
    if args.source == "arabam_cekici":
        from .sources.arabam_adapter import ArabamCekiciAdapter

        return ArabamCekiciAdapter()
    sys.exit(f"Unknown source: {args.source}")


def _report_density(records: list) -> None:
    """Dense support in a few families beats thin coverage across many —
    the pricing engine needs >= 5 compatible comparables before it will
    return a range at all, so that's what gets reported here."""
    families = Counter(r.model_family for r in records)
    print("\nRecords by model_family (pricing needs >= 5 to produce a range):")
    for family, count in families.most_common():
        marker = "OK " if count >= 5 else "-- "
        print(f"  {marker} {family:20} {count}")

    years = [r.year for r in records]
    mileages = [r.mileage_km for r in records]
    prices = [r.price for r in records]
    currencies = Counter(r.currency for r in records)
    cities = Counter(r.location for r in records)
    if records:
        print(f"\nYear range:     {min(years)}-{max(years)}")
        print(f"Mileage range:  {min(mileages):,}-{max(mileages):,} km")
        print(f"Price range:    {min(prices):,.0f}-{max(prices):,.0f}")
        print(f"Currencies:     {dict(currencies)}")
        print(f"Top cities:     {dict(cities.most_common(5))}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument("--csv", help="Path to a CSV (required for --source manual_csv)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests (be polite)")
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    args = parser.parse_args()

    adapter = _build_adapter(args)
    raw = list(adapter.fetch(limit=args.limit))
    cleaned = list(clean_and_dedupe(raw))
    apply_partitions(cleaned)
    count = write_csv(cleaned, args.out)

    by_partition = {}
    for r in cleaned:
        by_partition[r.partition] = by_partition.get(r.partition, 0) + 1

    print(f"Fetched {len(raw)} raw listings; {count} survived cleaning/dedup -> {args.out}")
    print(f"Partitions: {by_partition}")
    _report_density(cleaned)


if __name__ == "__main__":
    main()
