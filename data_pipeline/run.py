"""CLI entry point: run one or more source adapters, clean/dedupe/partition
the results, and write data_pipeline/output/listings.csv for
backend/scripts/load_listings.py to load.

Usage:
    python run.py --source manual_csv --csv templates/manual_listings.csv
    python run.py --source arabam_cekici   # once the adapter is filled in
"""

import argparse
import sys
from pathlib import Path

from .partition import apply_partitions
from .pipeline import clean_and_dedupe, write_csv
from .sources.csv_source import CSVSourceAdapter

OUTPUT_PATH = Path(__file__).parent / "output" / "listings.csv"


def _build_adapter(args: argparse.Namespace):
    if args.source == "manual_csv":
        if not args.csv:
            sys.exit("--csv is required for --source manual_csv")
        return CSVSourceAdapter(args.csv)
    if args.source == "arabam_cekici":
        from .sources.arabam_adapter import ArabamCekiciAdapter

        return ArabamCekiciAdapter()
    sys.exit(f"Unknown source: {args.source}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=["manual_csv", "arabam_cekici"])
    parser.add_argument("--csv", help="Path to a CSV (required for --source manual_csv)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    args = parser.parse_args()

    adapter = _build_adapter(args)
    raw = adapter.fetch(limit=args.limit)
    cleaned = list(clean_and_dedupe(raw))
    apply_partitions(cleaned)
    count = write_csv(cleaned, args.out)

    by_partition = {}
    for r in cleaned:
        by_partition[r.partition] = by_partition.get(r.partition, 0) + 1

    print(f"Wrote {count} usable listings to {args.out}")
    print(f"Partitions: {by_partition}")
    if count < 50:
        print(
            "WARNING: fewer than 50 usable listings — below the plan's hour-4 "
            "checkpoint target. Widen the model families or add another source."
        )


if __name__ == "__main__":
    main()
