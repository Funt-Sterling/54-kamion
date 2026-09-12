"""Load data_pipeline's output CSV into the backend's `listings` table.

Usage (from backend/, with the venv active):
    python scripts/load_listings.py ../data_pipeline/output/listings.csv

Idempotent-ish: re-running with the same CSV inserts duplicate rows unless
--replace-all is passed (wipes and reloads every Listing row) — fine for a
hackathon's iterate-and-reload workflow, not meant for production use.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Listing  # noqa: E402


def load(csv_path: Path, replace_all: bool) -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if replace_all:
            db.query(Listing).delete()

        count = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                db.add(
                    Listing(
                        source_url=row["source_url"],
                        collected_at=datetime.fromisoformat(row["collected_at"]),
                        listing_date=row.get("listing_date") or None,
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
                        image_refs=[u for u in (row.get("image_refs") or "").split("|") if u],
                        partition=row.get("partition") or "dev",
                    )
                )
                count += 1
        db.commit()
        return count
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--replace-all", action="store_true", help="Wipe all existing listings first")
    args = parser.parse_args()

    if not args.csv_path.exists():
        sys.exit(f"CSV not found: {args.csv_path}")

    count = load(args.csv_path, args.replace_all)
    print(f"Loaded {count} listings into the database.")


if __name__ == "__main__":
    main()
