"""Strict parsing of a model response into a `VisionProposal`.

This is a *syntax and schema* boundary only. It answers "is this a
well-formed proposal?" — never "is this true?". Semantic admission is a
separate pass (`admission.py`) so that a malformed envelope can reject
everything while a single unsupported claim only rejects itself.

Three rules the audit's reproductions turned into hard requirements:

1. **No permissive defaulting.** A missing or unrecognised `visibility`
   is a schema error. Previously it collapsed to `"clear"` — the most
   confident value was the one reachable by accident.
2. **No punctuation stripping on numbers.** `365000.0` became `"3650000"`
   because `.` was removed to handle thousands separators. Digits are
   parsed, never repaired.
3. **Unknown fields are rejected**, so a model that invents
   `estimated_price` cannot smuggle it past a whitelist that only filters
   the keys it already knows about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field

from ..contract import (
    ALL_CATEGORIES,
    ALL_EXTENTS,
    ALL_READABILITY,
    ALL_READING_KINDS,
    ALL_VIEWS,
    ALL_VISIBILITIES,
    ODOMETER_UNITS,
    VISION_SCHEMA_VERSION,
)

MAX_TEXT = 600
MAX_LIST = 40


class ProposalSchemaError(ValueError):
    """The envelope is unusable; nothing from this response may be admitted."""


@dataclass(frozen=True)
class Region:
    """Model-proposed location in ORIGINAL image coordinates.

    Helps a human find what was described. It is not proof the content is
    there, and nothing is admitted merely because a region was supplied.
    """

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ViewObservation:
    view: str
    visibility: str
    usable: bool
    limitation: str | None = None
    region: Region | None = None


@dataclass(frozen=True)
class Reading:
    kind: str
    raw_text: str
    readability: str
    unit: str | None = None
    #: True only when the model asserts this is the cumulative total, not a
    #: trip meter. Required before a mileage claim can be admitted.
    is_total: bool | None = None
    supporting_view: str | None = None
    region: Region | None = None


@dataclass(frozen=True)
class AxleGeometry:
    visible_axle_count: int | None
    whole_relevant_geometry_visible: bool
    supporting_view: str | None = None
    region: Region | None = None


@dataclass(frozen=True)
class IdentityCandidate:
    field: str  # make | model_family
    value: str
    basis: str  # readable_text | appearance
    supporting_reading_kind: str | None = None


@dataclass(frozen=True)
class ProposedFinding:
    component: str
    observation: str
    visibility: str
    severity: str = "info"
    recommended_action: str = "none"
    region: Region | None = None


@dataclass(frozen=True)
class VisionProposal:
    schema_version: str
    subject_extent: str
    subject_category: str
    views: dict[str, ViewObservation]
    readings: tuple[Reading, ...] = ()
    axle_geometry: AxleGeometry | None = None
    candidates: tuple[IdentityCandidate, ...] = ()
    findings: tuple[ProposedFinding, ...] = ()
    image_limitations: tuple[str, ...] = ()
    notes: str = ""


_ALLOWED_TOP_LEVEL = {
    "schema_version",
    "subject",
    "views",
    "readings",
    "axle_geometry",
    "candidates",
    "findings",
    "image_limitations",
    "notes",
}
_ALLOWED_SUBJECT = {"extent", "category"}
_ALLOWED_VIEW = {"visibility", "usable", "limitation", "region"}
_ALLOWED_READING = {"kind", "raw_text", "readability", "unit", "is_total", "supporting_view", "region"}
_ALLOWED_AXLE = {"visible_axle_count", "whole_relevant_geometry_visible", "supporting_view", "region"}
_ALLOWED_CANDIDATE = {"field", "value", "basis", "supporting_reading_kind"}
_FINDING_SEVERITIES = ("info", "clarify", "structural")
_ALLOWED_FINDING = {"component", "observation", "visibility", "severity", "recommended_action", "region"}
_ALLOWED_REGION = {"x", "y", "width", "height"}

_ALLOWED_CANDIDATE_FIELDS = {"make", "model_family"}
_ALLOWED_CANDIDATE_BASIS = {"readable_text", "appearance"}


def _reject_unknown(obj: dict, allowed: set[str], where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise ProposalSchemaError(f"{where}: unknown field(s) {sorted(unknown)}")


def _text(value: object, where: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ProposalSchemaError(f"{where}: expected string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise ProposalSchemaError(f"{where}: must not be empty")
        return None
    return cleaned[:MAX_TEXT]


def _enum(value: object, allowed: tuple[str, ...], where: str) -> str:
    text = _text(value, where)
    if text not in allowed:
        raise ProposalSchemaError(f"{where}: {text!r} is not one of {list(allowed)}")
    return text


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ProposalSchemaError(f"{where}: expected boolean, got {type(value).__name__}")
    return value


def _region(value: object, where: str) -> Region | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProposalSchemaError(f"{where}: region must be an object")
    _reject_unknown(value, _ALLOWED_REGION, where)
    try:
        return Region(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProposalSchemaError(f"{where}: invalid region ({exc})") from exc


def extract_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) < 2:
            raise ProposalSchemaError("unterminated code fence")
        text = parts[1]
        if text.startswith("json"):
            text = text[len("json") :]
    text = text.strip()
    if not text:
        raise ProposalSchemaError("empty response")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalSchemaError(f"not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProposalSchemaError(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def parse_proposal(raw: str) -> VisionProposal:
    payload = extract_json_object(raw)
    _reject_unknown(payload, _ALLOWED_TOP_LEVEL, "proposal")

    version = _text(payload.get("schema_version"), "schema_version")
    if version != VISION_SCHEMA_VERSION:
        raise ProposalSchemaError(
            f"schema_version {version!r} != expected {VISION_SCHEMA_VERSION!r}"
        )

    subject = payload.get("subject")
    if not isinstance(subject, dict):
        raise ProposalSchemaError("subject: required object")
    _reject_unknown(subject, _ALLOWED_SUBJECT, "subject")
    extent = _enum(subject.get("extent"), ALL_EXTENTS, "subject.extent")
    category = _enum(subject.get("category"), ALL_CATEGORIES, "subject.category")

    raw_views = payload.get("views")
    if not isinstance(raw_views, dict):
        raise ProposalSchemaError("views: required object")
    _reject_unknown(raw_views, set(ALL_VIEWS), "views")
    views: dict[str, ViewObservation] = {}
    for view in ALL_VIEWS:
        if view not in raw_views:
            # Missing visibility is a schema error, never an assumption.
            raise ProposalSchemaError(f"views.{view}: required and missing")
        entry = raw_views[view]
        if not isinstance(entry, dict):
            raise ProposalSchemaError(f"views.{view}: expected object")
        _reject_unknown(entry, _ALLOWED_VIEW, f"views.{view}")
        visibility = _enum(entry.get("visibility"), ALL_VISIBILITIES, f"views.{view}.visibility")
        usable = _bool(entry.get("usable", False), f"views.{view}.usable")
        if usable and visibility != "visible":
            raise ProposalSchemaError(
                f"views.{view}: usable=true requires visibility='visible', got {visibility!r}"
            )
        views[view] = ViewObservation(
            view=view,
            visibility=visibility,
            usable=usable,
            limitation=_text(entry.get("limitation"), f"views.{view}.limitation", required=False),
            region=_region(entry.get("region"), f"views.{view}.region"),
        )

    readings = tuple(_parse_readings(payload.get("readings")))
    axle = _parse_axle(payload.get("axle_geometry"))
    candidates = tuple(_parse_candidates(payload.get("candidates")))
    findings = tuple(_parse_findings(payload.get("findings")))

    limitations_raw = payload.get("image_limitations") or []
    if not isinstance(limitations_raw, list):
        raise ProposalSchemaError("image_limitations: expected list")
    limitations = tuple(
        text for text in (_text(item, "image_limitations[]", required=False) for item in limitations_raw[:MAX_LIST]) if text
    )

    return VisionProposal(
        schema_version=version,
        subject_extent=extent,
        subject_category=category,
        views=views,
        readings=readings,
        axle_geometry=axle,
        candidates=candidates,
        findings=findings,
        image_limitations=limitations,
        notes=_text(payload.get("notes"), "notes", required=False) or "",
    )


def _parse_readings(value: object):
    if value is None:
        return
    if not isinstance(value, list):
        raise ProposalSchemaError("readings: expected list")
    for index, entry in enumerate(value[:MAX_LIST]):
        where = f"readings[{index}]"
        if not isinstance(entry, dict):
            raise ProposalSchemaError(f"{where}: expected object")
        _reject_unknown(entry, _ALLOWED_READING, where)
        kind = _enum(entry.get("kind"), ALL_READING_KINDS, f"{where}.kind")
        unit = _text(entry.get("unit"), f"{where}.unit", required=False)
        if unit is not None and kind == "total_odometer" and unit not in ODOMETER_UNITS:
            raise ProposalSchemaError(f"{where}.unit: {unit!r} not in {list(ODOMETER_UNITS)}")
        is_total = entry.get("is_total")
        if is_total is not None and not isinstance(is_total, bool):
            raise ProposalSchemaError(f"{where}.is_total: expected boolean")
        yield Reading(
            kind=kind,
            raw_text=_text(entry.get("raw_text"), f"{where}.raw_text"),
            readability=_enum(entry.get("readability"), ALL_READABILITY, f"{where}.readability"),
            unit=unit,
            is_total=is_total,
            supporting_view=_view_ref(entry.get("supporting_view"), f"{where}.supporting_view"),
            region=_region(entry.get("region"), f"{where}.region"),
        )


def _view_ref(value: object, where: str) -> str | None:
    text = _text(value, where, required=False)
    if text is None:
        return None
    if text not in ALL_VIEWS:
        raise ProposalSchemaError(f"{where}: {text!r} is not a known view")
    return text


def _parse_axle(value: object) -> AxleGeometry | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProposalSchemaError("axle_geometry: expected object")
    _reject_unknown(value, _ALLOWED_AXLE, "axle_geometry")

    count = value.get("visible_axle_count")
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int):
            raise ProposalSchemaError("axle_geometry.visible_axle_count: expected integer or null")
        if not 0 <= count <= 10:
            raise ProposalSchemaError(f"axle_geometry.visible_axle_count: {count} out of range")

    return AxleGeometry(
        visible_axle_count=count,
        whole_relevant_geometry_visible=_bool(
            value.get("whole_relevant_geometry_visible", False),
            "axle_geometry.whole_relevant_geometry_visible",
        ),
        supporting_view=_view_ref(value.get("supporting_view"), "axle_geometry.supporting_view"),
        region=_region(value.get("region"), "axle_geometry.region"),
    )


def _parse_candidates(value: object):
    if value is None:
        return
    if not isinstance(value, list):
        raise ProposalSchemaError("candidates: expected list")
    for index, entry in enumerate(value[:MAX_LIST]):
        where = f"candidates[{index}]"
        if not isinstance(entry, dict):
            raise ProposalSchemaError(f"{where}: expected object")
        _reject_unknown(entry, _ALLOWED_CANDIDATE, where)
        field_name = _text(entry.get("field"), f"{where}.field")
        if field_name not in _ALLOWED_CANDIDATE_FIELDS:
            raise ProposalSchemaError(f"{where}.field: {field_name!r} not in {sorted(_ALLOWED_CANDIDATE_FIELDS)}")
        basis = _text(entry.get("basis"), f"{where}.basis")
        if basis not in _ALLOWED_CANDIDATE_BASIS:
            raise ProposalSchemaError(f"{where}.basis: {basis!r} not in {sorted(_ALLOWED_CANDIDATE_BASIS)}")
        kind = _text(entry.get("supporting_reading_kind"), f"{where}.supporting_reading_kind", required=False)
        if kind is not None and kind not in ALL_READING_KINDS:
            raise ProposalSchemaError(f"{where}.supporting_reading_kind: {kind!r} unknown")
        yield IdentityCandidate(
            field=field_name,
            value=_text(entry.get("value"), f"{where}.value"),
            basis=basis,
            supporting_reading_kind=kind,
        )


def _parse_findings(value: object):
    if value is None:
        return
    if not isinstance(value, list):
        raise ProposalSchemaError("findings: expected list")
    for index, entry in enumerate(value[:MAX_LIST]):
        where = f"findings[{index}]"
        if not isinstance(entry, dict):
            raise ProposalSchemaError(f"{where}: expected object")
        _reject_unknown(entry, _ALLOWED_FINDING, where)
        yield ProposedFinding(
            component=_text(entry.get("component"), f"{where}.component"),
            observation=_text(entry.get("observation"), f"{where}.observation"),
            # No default: an unlabelled finding is a schema error, so
            # "clear" can never be reached by omission.
            visibility=_enum(entry.get("visibility"), ALL_VISIBILITIES, f"{where}.visibility"),
            # Closed and required: an omitted severity must not quietly read
            # as "info" and hide a structural concern.
            severity=_enum(entry.get("severity"), _FINDING_SEVERITIES, f"{where}.severity"),
            recommended_action=_text(entry.get("recommended_action"), f"{where}.recommended_action", required=False)
            or "none",
            region=_region(entry.get("region"), f"{where}.region"),
        )
