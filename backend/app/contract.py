"""The frozen evidence contract (v1).

Single source of truth for the vocabulary shared by the vision adapter,
the admission validator, the resolver, coverage, the gate and the API.
Nothing here depends on other application modules, so every layer can
import it without cycles.

The organising rule, from the audit: **a request is not an observation, a
parse is not a fact, and a model proposal is not evidence.** Each boundary
below exists to stop one of those confusions.
"""

from __future__ import annotations

# Bump when the proposal shape changes in a way a stored run must be read
# back against. Persisted with every InferenceRun.
VISION_SCHEMA_VERSION = "v1"
PROMPT_VERSION = "v1.1-hint-blind"

# --- Views -----------------------------------------------------------------
# The physical things a photo can show. Coverage is expressed only in these
# terms, and only an admitted observation can fill one.

VIEW_FRONT = "front"
VIEW_REAR = "rear"
VIEW_SIDE = "side"
VIEW_TIRE = "tire"
VIEW_DASHBOARD = "dashboard"
VIEW_ODOMETER = "odometer"
VIEW_CAB = "cab"
VIEW_CHASSIS = "chassis"
VIEW_BADGE = "badge"

ALL_VIEWS: tuple[str, ...] = (
    VIEW_FRONT,
    VIEW_REAR,
    VIEW_SIDE,
    VIEW_TIRE,
    VIEW_DASHBOARD,
    VIEW_ODOMETER,
    VIEW_CAB,
    VIEW_CHASSIS,
    VIEW_BADGE,
)

# Every view key is REQUIRED in a proposal. A missing key is a schema error,
# never an implied "absent" and never an implied "clear".
VISIBILITY_VISIBLE = "visible"
VISIBILITY_ABSENT = "absent"
VISIBILITY_UNCLEAR = "unclear"
ALL_VISIBILITIES: tuple[str, ...] = (VISIBILITY_VISIBLE, VISIBILITY_ABSENT, VISIBILITY_UNCLEAR)

# --- Subject ---------------------------------------------------------------

EXTENT_WHOLE = "whole"
EXTENT_PARTIAL = "partial"
EXTENT_NONE = "none"
EXTENT_UNCLEAR = "unclear"
ALL_EXTENTS: tuple[str, ...] = (EXTENT_WHOLE, EXTENT_PARTIAL, EXTENT_NONE, EXTENT_UNCLEAR)

CATEGORY_TRACTOR_UNIT = "tractor_unit"
CATEGORY_OTHER_VEHICLE = "other_vehicle"
CATEGORY_NONVEHICLE = "nonvehicle"
CATEGORY_UNKNOWN = "unknown"
ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_TRACTOR_UNIT,
    CATEGORY_OTHER_VEHICLE,
    CATEGORY_NONVEHICLE,
    CATEGORY_UNKNOWN,
)

# --- Readings --------------------------------------------------------------

READING_BADGE = "badge"
READING_TOTAL_ODOMETER = "total_odometer"
READING_SPEC_PLATE = "spec_plate"
READING_YEAR_PLATE = "year_plate"
ALL_READING_KINDS: tuple[str, ...] = (
    READING_BADGE,
    READING_TOTAL_ODOMETER,
    READING_SPEC_PLATE,
    READING_YEAR_PLATE,
)

# A trip meter is explicitly NOT a total odometer. The model is asked to
# distinguish them, and anything not clearly a total is rejected rather
# than guessed at.
ODOMETER_UNITS: tuple[str, ...] = ("km", "mi")

READABILITY_READABLE = "readable"
READABILITY_PARTIAL = "partial"
READABILITY_UNREADABLE = "unreadable"
ALL_READABILITY: tuple[str, ...] = (
    READABILITY_READABLE,
    READABILITY_PARTIAL,
    READABILITY_UNREADABLE,
)

# --- Provenance ------------------------------------------------------------
# Assigned by the SERVER only. A client can never choose one of these; the
# public details endpoint accepts declarations and the server stamps them.

PROV_OBSERVED = "observed_from_photo"
PROV_SELLER = "seller_declared"
PROV_CANDIDATE = "inferred_candidate"
PROV_USER_CORRECTED = "user_corrected"
ALL_PROVENANCES: tuple[str, ...] = (
    PROV_OBSERVED,
    PROV_SELLER,
    PROV_CANDIDATE,
    PROV_USER_CORRECTED,
)

#: Provenances a client is permitted to cause via the public API. Visual
#: provenance is deliberately absent — only the admission pipeline mints it.
CLIENT_DECLARABLE_PROVENANCES: frozenset[str] = frozenset({PROV_SELLER, PROV_USER_CORRECTED})

#: Fields a seller may declare, with the validator each value must pass.
#: Anything outside this allowlist is rejected at the HTTP boundary.
DECLARABLE_FIELDS: tuple[str, ...] = (
    "make",
    "model_family",
    "year",
    "mileage_km",
    "axle_config",
    "vat_basis",
)

# --- Evidence lifecycle ----------------------------------------------------

STATE_ACTIVE = "active"
STATE_SUPERSEDED = "superseded"
STATE_DISPUTED = "disputed"
#: Visual evidence created before the v1 admission contract existed. It was
#: never checked for readable support, so it is kept for history but no
#: longer participates in resolution. Reanalysis creates fresh evidence.
STATE_LEGACY_UNVERIFIED = "legacy_unverified"
ALL_EVIDENCE_STATES: tuple[str, ...] = (STATE_ACTIVE, STATE_SUPERSEDED, STATE_DISPUTED, STATE_LEGACY_UNVERIFIED)

#: A failing image is retried on the same media row, but not forever.
MAX_ANALYSIS_ATTEMPTS_PER_MEDIA = 3
#: Hard ceilings on paid model use and stored uploads. No auth exists for the
#: demo, so without these a loop (buggy client or hostile script) drives
#: unbounded vision calls and disk writes.
MAX_MEDIA_PER_SESSION = 60
MAX_ANALYSES_PER_MINUTE = 30

# --- Concerns --------------------------------------------------------------

CONCERN_OPEN = "open"
CONCERN_RESOLVED = "resolved"
CONCERN_REVIEW_REQUIRED = "review_required"

SEVERITY_INFO = "info"
SEVERITY_CLARIFY = "clarify"
SEVERITY_STRUCTURAL = "structural"

# --- Resolved field status (what the resolver reports) ---------------------

STATUS_UNKNOWN = "unknown"
STATUS_CONFLICTING = "conflicting"
STATUS_CONFIRMED = "confirmed"
STATUS_AGREE_SOURCES = "sources_agree"

# --- Gate outcomes ---------------------------------------------------------

GATE_READY = "ready_to_price"
GATE_NEEDS_EVIDENCE = "needs_evidence"
GATE_UNSUPPORTED = "unsupported"
GATE_INSPECTION_REQUIRED = "inspection_required"
GATE_INSUFFICIENT_MARKET = "insufficient_market_data"

# --- Capture origin --------------------------------------------------------
# Reports where the bytes came from. Neither value is authenticity proof and
# the UI must not present them as such.

ORIGIN_CAMERA = "camera"
ORIGIN_GALLERY = "gallery"
ORIGIN_UNKNOWN = "unknown"
ALL_ORIGINS: tuple[str, ...] = (ORIGIN_CAMERA, ORIGIN_GALLERY, ORIGIN_UNKNOWN)

# --- Pricing policy --------------------------------------------------------

MIN_DISTINCT_VEHICLE_GROUPS = 5
#: Fixed from development data, never widened per query to reach the minimum.
MAX_COMPARABLE_YEAR_DISTANCE = 4
MAX_COMPARABLE_LOG_MILEAGE_DISTANCE = 0.75
#: Listings collected more than this long ago stop supporting a current range.
MAX_LISTING_AGE_DAYS = 180

SUPPORTED_COUNTRY = "TR"
SUPPORTED_CURRENCY = "TRY"
SUPPORTED_CATEGORY = "tractor_unit"

VAT_UNSTATED_LABEL = "source asking prices; VAT treatment not stated"
