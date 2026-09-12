"""Shared vocabulary for capture coverage and vehicle taxonomy.

Kept as plain constants (not an enum) so the vision adapter's free-text
component tags can be validated against this list without an import cycle.
"""

# Component checklist from docs/plan.md section 2 ("What to photograph"),
# itself based on CVSA inspection guidance used as a technical reference.
REQUIRED_COMPONENTS = [
    "front_exterior",
    "rear_exterior",
    "side_exterior",
    "tire",
    "chassis_suspension",
    "dashboard_odometer",
    "cab_interior",
]

COVERAGE_MISSING = "missing"
COVERAGE_CAPTURED = "captured"
COVERAGE_ATTENTION = "attention"  # captured but flagged (poor quality / suspected damage / analysis failed)

APPRAISAL_STATUSES = [
    "priced",
    "needs_evidence",
    "unsupported",
    "insufficient_market_data",
    "inspection_required",
]

# Evidence provenance — see app/services/evidence.py. Every fact about the
# vehicle is tagged with exactly one of these when it's recorded.
PROVENANCE_SELLER_DECLARED = "seller_declared"
PROVENANCE_OBSERVED_FROM_PHOTO = "observed_from_photo"
PROVENANCE_INFERRED_CANDIDATE = "inferred_candidate"

# Fields the evidence gate resolves and exposes on every session — see
# GET /sessions/{id}'s `evidence` list.
TRACKED_EVIDENCE_FIELDS = [
    "vehicle_category",
    "model_family",
    "axle_config",
    "year",
    "mileage_km",
    "vat_basis",
]
