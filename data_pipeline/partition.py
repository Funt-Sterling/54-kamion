"""Assigns dev/calibration/test partitions at roughly 60/20/20, keyed by
vehicle identity (not row order) so re-running the pipeline is stable and,
per docs/plan.md, "all evidence from one vehicle" stays in one partition."""

import hashlib

from .schema import ListingRecord

DEV, CALIBRATION, TEST = "dev", "calibration", "test"


def _vehicle_key(record: ListingRecord) -> str:
    return f"{record.make}|{record.model}|{record.year}|{round(record.mileage_km, -3)}".lower()


def assign_partition(record: ListingRecord) -> str:
    """Deterministic hash bucket, not random.shuffle — so the split doesn't
    reshuffle (and silently leak a vehicle across partitions) between runs."""
    digest = hashlib.sha256(_vehicle_key(record).encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    if bucket < 60:
        return DEV
    if bucket < 80:
        return CALIBRATION
    return TEST


def apply_partitions(records: list[ListingRecord]) -> list[ListingRecord]:
    for r in records:
        r.partition = assign_partition(r)
    return records
