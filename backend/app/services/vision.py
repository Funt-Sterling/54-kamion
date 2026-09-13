"""Vision adapter: the one place the backend talks to a multimodal model.

The adapter's job is deliberately small. It sends ONE photo, gets back ONE
v1 proposal envelope (`services/proposal.VisionProposal`), and records what
it took to get it. It decides nothing. Whether any claim inside that
envelope is *supported* is `services/admission.admit`'s question, and
whether the session may be priced is the gate's.

Two properties this module exists to guarantee:

**Perception is hint-blind.** The previous prompt interpolated the
uploader's `component_hint` ("The uploader tagged this photo as: tire"),
which is a request, not an observation — and asking a model to look at a
photo it has been told is a tire reliably produces a tire. Coverage built
on that is coverage of what was *asked for*. `analyze_image` therefore
takes no hint, and nothing about the upload's tagging reaches the prompt.

**A failed read is never an empty read.** A truncated, malformed, or
unschematic response raises. It never degrades into a well-formed proposal
that happens to claim nothing, because "we could not see the chassis" and
"the chassis is fine" must not share a representation.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..contract import (
    ALL_CATEGORIES,
    ALL_EXTENTS,
    ALL_READABILITY,
    ALL_READING_KINDS,
    ALL_VIEWS,
    ALL_VISIBILITIES,
    ODOMETER_UNITS,
    PROMPT_VERSION,
    SEVERITY_CLARIFY,
    SEVERITY_INFO,
    SEVERITY_STRUCTURAL,
    VISION_SCHEMA_VERSION,
)
from .proposal import ProposalSchemaError, VisionProposal, parse_proposal

_CANDIDATE_FIELDS: tuple[str, ...] = ("make", "model_family")
_CANDIDATE_BASES: tuple[str, ...] = ("readable_text", "appearance")
_SEVERITIES: tuple[str, ...] = (SEVERITY_INFO, SEVERITY_CLARIFY, SEVERITY_STRUCTURAL)


class VisionResponseError(ProposalSchemaError):
    """The model's response is unusable as an envelope.

    Subclasses `ProposalSchemaError` so a caller that wants "nothing from
    this response may be admitted" can catch one type, whether the envelope
    failed at the transport layer (truncated at max_tokens) or at the schema
    layer (unknown field, missing view).
    """


@dataclass(frozen=True)
class VisionCallResult:
    """One model call, with everything needed to persist an InferenceRun.

    Carries no credential: `model_id` is a public model name and `usage` is
    plain token counts. The API key never enters this object, and so can
    never reach a log line or an API response by way of it.
    """

    proposal: VisionProposal
    raw_text: str
    model_id: str
    prompt_version: str
    schema_version: str
    usage: dict[str, int]
    latency_ms: int
    #: True when the model was constrained by a server-side JSON schema.
    #: False means the call fell back to plain-text JSON; the strict parser
    #: ran in both cases, so this is provenance, not a quality flag.
    structured_output: bool = False
    stop_reason: str | None = None


# --- JSON schema -----------------------------------------------------------
# Built from the contract constants rather than written out, so a new view
# or reading kind cannot be added to the vocabulary while the schema the
# model is held to silently keeps the old one.


def _region_schema() -> dict:
    return {
        "type": ["object", "null"],
        "description": "Bounding box in ORIGINAL image pixel coordinates, or null.",
        "properties": {name: {"type": "number"} for name in ("x", "y", "width", "height")},
        "required": ["x", "y", "width", "height"],
        "additionalProperties": False,
    }


def _enum_or_null(values: tuple[str, ...]) -> dict:
    return {"type": ["string", "null"], "enum": [*values, None]}


def _object(properties: dict, *, description: str | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


def build_proposal_json_schema() -> dict:
    """The v1 envelope as a JSON schema, mirroring `proposal.parse_proposal`.

    Every property is required and every object closed, so a model cannot
    omit a view key or attach an `estimated_price` at the schema layer
    either. Optional values are expressed as explicit nulls rather than
    absent keys — "we did not look" and "we looked and saw nothing" stay
    distinguishable all the way down.
    """
    view = _object(
        {
            "visibility": {
                "type": "string",
                "enum": list(ALL_VISIBILITIES),
                "description": "visible = this view is shown here; absent = not in this photo; unclear = shown but you cannot tell.",
            },
            "usable": {
                "type": "boolean",
                "description": "True only if this view is good enough to judge from. Requires visibility='visible'.",
            },
            "limitation": {"type": ["string", "null"]},
            "region": _region_schema(),
        }
    )

    reading = _object(
        {
            "kind": {"type": "string", "enum": list(ALL_READING_KINDS)},
            "raw_text": {
                "type": "string",
                "description": "The characters exactly as printed. Never reformatted, completed, or converted.",
            },
            "readability": {"type": "string", "enum": list(ALL_READABILITY)},
            "unit": {
                "type": ["string", "null"],
                "enum": [*ODOMETER_UNITS, None],
                "description": "Required for total_odometer; null otherwise.",
            },
            "is_total": {
                "type": ["boolean", "null"],
                "description": "True only for a cumulative total odometer. A trip meter is false.",
            },
            "supporting_view": _enum_or_null(ALL_VIEWS),
            "region": _region_schema(),
        }
    )

    candidate = _object(
        {
            "field": {"type": "string", "enum": list(_CANDIDATE_FIELDS)},
            "value": {"type": "string"},
            "basis": {
                "type": "string",
                "enum": list(_CANDIDATE_BASES),
                "description": "readable_text only when text you actually read says it; otherwise appearance.",
            },
            "supporting_reading_kind": _enum_or_null(ALL_READING_KINDS),
        }
    )

    finding = _object(
        {
            "component": {"type": "string"},
            "observation": {"type": "string"},
            "visibility": {
                "type": "string",
                "enum": list(ALL_VISIBILITIES),
                "description": "How well you could see the thing described. Mandatory; never assume 'visible'.",
            },
            "severity": {"type": "string", "enum": list(_SEVERITIES)},
            "recommended_action": {"type": "string"},
            "region": _region_schema(),
        }
    )

    return _object(
        {
            "schema_version": {"type": "string", "const": VISION_SCHEMA_VERSION},
            "subject": _object(
                {
                    "extent": {"type": "string", "enum": list(ALL_EXTENTS)},
                    "category": {"type": "string", "enum": list(ALL_CATEGORIES)},
                }
            ),
            "views": _object({name: view for name in ALL_VIEWS}),
            "readings": {"type": "array", "items": reading},
            "axle_geometry": _object(
                {
                    "visible_axle_count": {"type": ["integer", "null"]},
                    "whole_relevant_geometry_visible": {"type": "boolean"},
                    "supporting_view": _enum_or_null(ALL_VIEWS),
                    "region": _region_schema(),
                }
            ),
            "candidates": {"type": "array", "items": candidate},
            "findings": {"type": "array", "items": finding},
            "image_limitations": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        }
    )


PROPOSAL_JSON_SCHEMA: dict = build_proposal_json_schema()


# --- Prompt ----------------------------------------------------------------


def build_prompt() -> str:
    """The hint-blind perception prompt.

    Takes no arguments by design. There is no parameter through which an
    uploader's tag, a requested view, or a gate's outstanding need can reach
    the model, because any of those would make the answer a function of the
    question.
    """
    views = ", ".join(ALL_VIEWS)
    return (
        "You are recording what is visible in ONE photograph submitted for a used "
        "commercial-vehicle inspection in Turkey.\n\n"
        "Report only what you can see in THIS image. Do not infer mechanical condition "
        "from appearance. Do not report what is probably there, what is usually there, "
        "or what a photo like this normally shows. Where you cannot tell, use the "
        "uncertain value the field provides — never the confident one.\n\n"
        "Return ONLY a single JSON object with exactly these keys:\n\n"
        f'- "schema_version": the string "{VISION_SCHEMA_VERSION}".\n'
        '- "subject": {"extent", "category"}.\n'
        f"    extent: one of {list(ALL_EXTENTS)} — how much of one subject this frame contains. "
        '"whole" means the entire vehicle is in frame; a close-up of one part is "partial".\n'
        f"    category: one of {list(ALL_CATEGORIES)}. A tractor unit is the cab-and-engine unit "
        "that pulls a semi-trailer — not a pickup, van, bus, rigid truck, or a trailer on its own. "
        'Use "unknown" unless the frame actually shows you which it is.\n'
        f'- "views": an object with ALL NINE of these keys: {views}.\n'
        '    Each value is {"visibility", "usable", "limitation", "region"}.\n'
        f"    visibility: one of {list(ALL_VISIBILITIES)}. "
        '"visible" = this view is shown in this photo; "absent" = it is not in this photo at all; '
        '"unclear" = something of it is in frame but you cannot make it out.\n'
        "    usable: true only if this view is good enough to judge that part of the vehicle from.\n"
        "    limitation: one short phrase saying what spoils the view (glare, crop, distance, "
        "dirt), or null.\n"
        "    region: {x, y, width, height} in ORIGINAL image pixels, or null.\n"
        f'- "readings": a list of text you actually READ in the image. Each is '
        '{"kind", "raw_text", "readability", "unit", "is_total", "supporting_view", "region"}.\n'
        f"    kind: one of {list(ALL_READING_KINDS)}.\n"
        "    raw_text: the characters exactly as printed.\n"
        f"    readability: one of {list(ALL_READABILITY)}.\n"
        f'    unit: for a total_odometer, one of {list(ODOMETER_UNITS)}; otherwise null.\n'
        "    is_total: true only for a cumulative total odometer; false for a trip meter; null if "
        "the reading is not an odometer.\n"
        "    supporting_view: which of the nine views shows this reading, or null.\n"
        "    region: where the text is, in ORIGINAL image pixels. A total_odometer reading "
        "without a region cannot be checked and is not used.\n"
        '    A "badge" reading is manufacturer or model text on the vehicle (for example the '
        "model name on the grille), exactly as printed.\n"
        "    Emit no readings at all if you read no text.\n"
        '- "axle_geometry": {"visible_axle_count", "whole_relevant_geometry_visible", '
        '"supporting_view", "region"}.\n'
        "    visible_axle_count: axles you can actually count in this frame, or null.\n"
        "    whole_relevant_geometry_visible: true only if the whole axle arrangement is in frame, "
        "so the count cannot be partial. Count axles; do not conclude a drive configuration.\n"
        f'- "candidates": identity guesses. Each is {{"field", "value", "basis", '
        '"supporting_reading_kind"}}.\n'
        f"    field: one of {list(_CANDIDATE_FIELDS)}. value: the name as you would write it.\n"
        f"    basis: {list(_CANDIDATE_BASES)} — use \"readable_text\" ONLY when text you actually "
        'read says this; use "appearance" when you are going by shape or styling.\n'
        '- "findings": localized visible condition. Each is {"component", "observation", '
        '"visibility", "severity", "recommended_action", "region"}.\n'
        f"    visibility is MANDATORY on every finding: one of {list(ALL_VISIBILITIES)}, meaning how "
        "well you could see the thing you are describing.\n"
        f"    severity: one of {list(_SEVERITIES)}. "
        '"structural" only for damage that could affect the frame or chassis — not cosmetic dents, '
        "scratches, or surface rust.\n"
        '    recommended_action: a short phrase, or "none".\n'
        '- "image_limitations": short strings about the photo itself (too dark, motion blur, heavy '
        "glare, subject too far away). Empty list if none.\n"
        '- "notes": one short sentence, or "".\n\n'
        "HARD RULES\n"
        f"1. All nine view keys ({views}) must be present. A view you cannot see is "
        '"absent" or "unclear" — never omitted, and never left out to save space.\n'
        "2. Never guess a visibility. If you are unsure whether something is in frame, that is "
        '"unclear".\n'
        '3. "usable": true requires "visibility": "visible". A view that is absent or unclear is '
        "never usable.\n"
        "4. A trip meter, trip odometer, or resettable counter is NOT the total. Report it with "
        '"is_total": false. Only the cumulative lifetime figure is "is_total": true.\n'
        "5. raw_text is literal. Give the characters as they appear — do not add or remove "
        "separators, do not complete digits hidden by glare, do not convert units.\n"
        "6. Never output a price, valuation, estimate, currency amount, or any monetary figure, in "
        "any field, for any reason. You are not valuing this vehicle.\n"
        "7. Use only the keys listed above. Do not invent keys, and do not add commentary outside "
        "the JSON object.\n"
        "8. Keep it proportionate: at most about 8 readings, 4 candidates and 12 findings — the "
        "most decision-relevant things actually visible, one sentence each."
    )


# --- Adapters --------------------------------------------------------------


class VisionAdapter(ABC):
    @abstractmethod
    def analyze_image(self, image_path: str | Path) -> VisionCallResult:
        """Perceive one photo. Takes no hint: see the module docstring."""
        raise NotImplementedError


def _empty_proposal_payload() -> dict:
    """A schema-valid v1 envelope that asserts nothing about anything."""
    return {
        "schema_version": VISION_SCHEMA_VERSION,
        "subject": {"extent": "unclear", "category": "unknown"},
        "views": {
            name: {"visibility": "absent", "usable": False, "limitation": None, "region": None}
            for name in ALL_VIEWS
        },
        "readings": [],
        "axle_geometry": {
            "visible_axle_count": None,
            "whole_relevant_geometry_visible": False,
            "supporting_view": None,
            "region": None,
        },
        "candidates": [],
        "findings": [],
        "image_limitations": [],
        "notes": (
            "VISION_ADAPTER=mock: no image was analysed. Set VISION_ADAPTER=anthropic "
            "and ANTHROPIC_API_KEY to perceive real photos."
        ),
    }


class MockVisionAdapter(VisionAdapter):
    """Offline stand-in that fabricates no evidence.

    Every view is absent, the category is unknown and there are no readings,
    so admission credits nothing and the gate reports the session as needing
    evidence — which is the truth when no model has looked at the photo.
    The payload is round-tripped through `parse_proposal`, so the mock
    cannot drift out of schema without the tests noticing.
    """

    MODEL_ID = "mock"

    def analyze_image(self, image_path: str | Path) -> VisionCallResult:
        raw_text = json.dumps(_empty_proposal_payload())
        return VisionCallResult(
            proposal=parse_proposal(raw_text),
            raw_text=raw_text,
            model_id=self.MODEL_ID,
            prompt_version=PROMPT_VERSION,
            schema_version=VISION_SCHEMA_VERSION,
            usage={},
            latency_ms=0,
            structured_output=False,
            stop_reason=None,
        )


_SCHEMA_REJECTION_MARKERS: tuple[str, ...] = (
    "output_config",
    "output config",
    "json_schema",
    "json schema",
    "structured output",
    "output format",
)


def _looks_like_schema_rejection(exc: Exception) -> bool:
    """True when a 400 is about the structured-output request itself.

    Narrow on purpose: a 400 about the image, the model or the token budget
    is a real failure and must surface, not be retried into a second
    charge.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _SCHEMA_REJECTION_MARKERS)


class AnthropicVisionAdapter(VisionAdapter):
    """Calls a Claude vision model and returns a parsed v1 proposal.

    Imports the `anthropic` SDK lazily so mock-mode dev and test never need
    it installed or configured.
    """

    def __init__(self):
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot use the anthropic vision adapter.")
        import anthropic

        # An organization-scoped key has to say which workspace to run in;
        # a workspace-scoped key already carries that and needs no header.
        default_headers = (
            {"anthropic-workspace-id": settings.anthropic_workspace_id}
            if settings.anthropic_workspace_id
            else None
        )
        # An unbounded call would hang the upload request; a bounded one
        # surfaces as a recoverable vision_status="failed" instead.
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.vision_timeout_seconds,
            max_retries=settings.vision_max_retries,
            default_headers=default_headers,
        )
        self._bad_request = anthropic.BadRequestError
        self._model = settings.vision_model
        self._max_tokens = settings.vision_max_tokens
        # Runtime capability cache, not configuration: if this deployment's
        # model or account rejects the schema we learn it once rather than
        # paying for a doomed first call on every photo.
        self._structured_output = True

    def analyze_image(self, image_path: str | Path) -> VisionCallResult:
        path = Path(image_path)
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            {"type": "text", "text": build_prompt()},
        ]

        started = time.perf_counter()
        message, structured = self._create(content)
        latency_ms = int((time.perf_counter() - started) * 1000)

        stop_reason = getattr(message, "stop_reason", None)
        # A truncated response is unparseable JSON, but "Unterminated string
        # at line 22" is a misleading way to learn the budget ran out — and
        # a half-read photo must never become half an analysis.
        if stop_reason == "max_tokens":
            raise VisionResponseError(
                f"response hit the {self._max_tokens}-token limit and was cut off before the JSON closed"
            )

        raw_text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        return VisionCallResult(
            proposal=parse_proposal(raw_text),
            raw_text=raw_text,
            model_id=self._model,
            prompt_version=PROMPT_VERSION,
            schema_version=VISION_SCHEMA_VERSION,
            usage=_usage_dict(getattr(message, "usage", None)),
            latency_ms=latency_ms,
            structured_output=structured,
            stop_reason=stop_reason,
        )

    def _create(self, content: list[dict]) -> tuple[object, bool]:
        """Send the message, preferring a server-enforced JSON schema."""
        request = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if self._structured_output:
            try:
                message = self._client.messages.create(
                    **request,
                    output_config={"format": {"type": "json_schema", "schema": PROPOSAL_JSON_SCHEMA}},
                )
                return message, True
            except self._bad_request as exc:
                if not _looks_like_schema_rejection(exc):
                    raise
                self._structured_output = False
        return self._client.messages.create(**request), False


def _usage_dict(usage: object) -> dict[str, int]:
    """Token counts only, defensively — never anything credential-shaped."""
    if usage is None:
        return {}
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    counts: dict[str, int] = {}
    for name in fields:
        value = getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[name] = value
    return counts


def get_vision_adapter() -> VisionAdapter:
    settings = get_settings()
    if settings.vision_adapter == "anthropic":
        return AnthropicVisionAdapter()
    return MockVisionAdapter()
