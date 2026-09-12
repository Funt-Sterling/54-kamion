"""ListingRecord: the one row shape every source adapter must produce.

Mirrors backend/app/models.py's `Listing` table, plus nothing else — keeping
the two in sync by hand (rather than importing across the package boundary)
is a deliberate hackathon-scale simplification; see backend/scripts/load_listings.py
for the CSV -> DB loader that bridges them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Listing text containing any of these should be dropped before pricing —
# see docs/plan.md: "Exclude deposits, lease payments, 'price on request',
# new vehicles, and salvage listings from the standard used-truck baseline."
EXCLUDED_LISTING_MARKERS = (
    "fiyat sorunuz",  # "ask for price" (TR)
    "price on request",
    "poa",
    "kapora",  # deposit (TR)
    "deposit",
    "kiralık",  # for rent (TR)
    "lease",
    "hasar kayıtlı",  # salvage/damage-recorded (TR)
    "salvage",
    "sıfır",  # new/zero-km (TR)
)

VALID_VAT_BASES = ("vat_included", "vat_excluded", "vat_exempt", "unknown")


@dataclass
class ListingRecord:
    source_url: str
    location: str
    make: str
    model: str
    model_family: str
    year: int
    mileage_km: int
    axle_config: str
    price: float
    currency: str
    vat_basis: str  # one of VALID_VAT_BASES
    category: str = "tractor_unit"
    country: str = "TR"
    # Which adapter produced this row, and the source site's own listing id —
    # kept so any record can be traced back to the exact page it came from
    # and so reposts of the same listing id are detectable.
    source: str = "unknown"
    listing_id: str | None = None
    # The raw vehicle-type label as the source site wrote it (e.g. "Çekici"),
    # kept alongside the normalized `category` rather than replaced by it.
    vehicle_type: str | None = None
    listing_date: str | None = None
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    image_refs: list[str] = field(default_factory=list)
    partition: str = "dev"

    def validate(self) -> list[str]:
        """Return problems found (empty list = usable). Never raises —
        callers decide whether to drop a bad record or just flag it."""
        problems = []
        if self.country != "TR":
            problems.append(f"non-Turkish location: {self.country}")
        if self.price <= 0:
            problems.append("non-positive price")
        current_year = datetime.now().year
        if self.year < 1990 or self.year > current_year + 1:
            problems.append(f"implausible year: {self.year}")
        if self.mileage_km < 0 or self.mileage_km > 3_000_000:
            problems.append(f"implausible mileage: {self.mileage_km}")
        if self.vat_basis not in VALID_VAT_BASES:
            problems.append(f"unrecognized vat_basis: {self.vat_basis}")
        if not self.axle_config:
            problems.append("missing axle_config")
        return problems

    def as_dict(self) -> dict:
        return asdict(self)
