"""Vision adapter: the one place the backend talks to a multimodal model.

Everything else (routers, pricing, quality checks) depends only on
`VisionAdapter` / `VisionResult`, never on a vendor SDK — swapping providers
means writing one new adapter class, not touching the pipeline. This is the
seam the challenge brief is pointing at with "a thin wrapper that sends
photos to a vision API and prints whatever number comes back" — the vision
call here only ever returns *observations*, never a price.
"""

import base64
import json
import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

# The ONLY vehicle facts a vision model is allowed to contribute to the
# evidence model. Anything else it returns in `extracted_specs` is dropped.
#
# This is the hard guarantee behind "the model never outputs a price": even
# if a response came back with {"price": "2500000"} or {"asking_price": ...},
# it cannot reach an EvidenceRecord, because routers/media.py writes evidence
# straight from this dict. Prices come from services/pricing.py's comparable
# retrieval and nowhere else.
ALLOWED_SPEC_FIELDS = frozenset({"mileage_km", "year", "axle_config"})

# Specs that must parse as a plain integer to be believable at all.
_INTEGER_SPEC_FIELDS = frozenset({"mileage_km", "year"})

_ALLOWED_VISIBILITY = frozenset({"clear", "partial", "obstructed"})

_MAX_TEXT_LEN = 600


class VisionResponseError(ValueError):
    """The model's response could not be validated into a VisionResult.

    Raised for malformed JSON, a non-object payload, or missing required
    fields. Callers (routers/media.py) treat this the same as an API
    failure: the photo is kept, the session survives, and the media item is
    marked vision_status="failed" rather than being credited with evidence
    that was never established.
    """


@dataclass
class ComponentObservation:
    component: str
    observation: str
    visibility: str = "clear"  # clear | partial | obstructed
    recommended_action: str = "none"


@dataclass
class VisionResult:
    is_vehicle: bool
    is_truck_tractor_unit: bool
    vehicle_category_guess: str | None
    make_guess: str | None
    model_guess: str | None
    # A clearly legible badge/spec-plate reading is stronger evidence than a
    # visual guess — when present, the caller records model info as
    # observed_from_photo instead of inferred_candidate.
    visible_badge_text: str | None = None
    structural_damage_suspected: bool = False
    tire_concern_noted: bool = False
    extracted_specs: dict[str, str] = field(default_factory=dict)
    component_observations: list[ComponentObservation] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    notes: str = ""


class VisionAdapter(ABC):
    @abstractmethod
    def analyze_image(self, image_path: str | Path, component_hint: str | None = None) -> VisionResult:
        raise NotImplementedError


class MockVisionAdapter(VisionAdapter):
    """Offline stand-in so the pipeline is runnable/testable without an API
    key. Deliberately makes no claims about vehicle identity or condition —
    do NOT use this for the judged demo, only for local development."""

    def analyze_image(self, image_path, component_hint=None):
        return VisionResult(
            is_vehicle=True,
            is_truck_tractor_unit=True,
            vehicle_category_guess="unknown",
            make_guess=None,
            model_guess=None,
            extracted_specs={},
            visible_badge_text=None,
            structural_damage_suspected=False,
            tire_concern_noted=False,
            component_observations=[
                ComponentObservation(
                    component=component_hint or "unknown",
                    observation="Mock adapter — no real vision analysis performed.",
                )
            ],
            quality_issues=[],
            notes="VISION_ADAPTER=mock: set VISION_ADAPTER=anthropic and ANTHROPIC_API_KEY for real appraisals.",
        )


class AnthropicVisionAdapter(VisionAdapter):
    """Calls a Claude vision-capable model for structured, per-photo extraction.

    Imports the `anthropic` SDK lazily so mock-mode dev/test never needs it
    installed or configured.
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
        self._model = settings.vision_model

    def analyze_image(self, image_path, component_hint=None):
        path = Path(image_path)
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

        message = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                        {"type": "text", "text": self._build_prompt(component_hint)},
                    ],
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        return self._parse(raw)

    @staticmethod
    def _build_prompt(component_hint: str | None) -> str:
        return (
            "You are inspecting ONE photo submitted for a used-truck appraisal in Turkey. "
            "Only report what is visible in THIS image; never guess at things you cannot see, "
            "and never infer mechanical condition from appearance alone.\n"
            f"The uploader tagged this photo as: {component_hint or 'unspecified'}.\n\n"
            "Return ONLY a JSON object with these exact keys:\n"
            '- is_vehicle (bool)\n'
            '- is_truck_tractor_unit (bool): a tractor unit is the cab+engine unit that pulls a '
            "semi-trailer — NOT a pickup, van, bus, motorcycle, or the trailer itself\n"
            "- vehicle_category_guess (string, e.g. 'tractor_unit', 'motorcycle', 'passenger_car', "
            "'empty_scene', 'unknown')\n"
            "- make_guess (string or null): a visual guess even without a legible badge\n"
            "- model_guess (string or null): a visual guess even without a legible badge\n"
            "- visible_badge_text (string or null): ONLY the literal text of a manufacturer "
            "badge/spec plate if you can actually read it (e.g. 'ACTROS 1845') — null if no "
            "badge is legible in this photo, even if you can still guess the model visually\n"
            "- structural_damage_suspected (bool): true only for damage that looks like it could "
            "affect the frame/chassis structure (e.g. a bent frame rail, a cracked chassis) — "
            "NOT cosmetic dents, scratches, or surface rust\n"
            "- tire_concern_noted (bool): true if a tire shows a visible cut, bulge, or clearly "
            "uneven wear that isn't fully resolved by this photo alone\n"
            "- extracted_specs (object; ONLY fields actually legible in this photo, e.g. "
            '{"mileage_km": "480000"} if an odometer is clearly readable, or {"year": "2019"} '
            "if a registration/build plate shows it, or {\"axle_config\": \"4x2\"} if countable "
            "from a full side/rear view — omit any field you cannot actually read)\n"
            "- component_observations (array of {component, observation, visibility, "
            "recommended_action}; one entry per distinct thing you can say something concrete "
            "about; recommended_action is 'none' unless a retake/closer photo would help)\n"
            "- quality_issues (array of strings, e.g. 'too_dark', 'too_blurry', 'obstructed')\n"
            "- notes (short string)\n\n"
            "Never write 'mechanically sound' or invent a condition score. If you cannot tell "
            "something, say so in notes rather than guessing."
        )

    @classmethod
    def _parse(cls, raw: str) -> VisionResult:
        """Validate an untrusted model response into a VisionResult.

        Everything here treats the response as hostile-by-default: unknown
        spec fields are dropped, wrong types are discarded rather than
        coerced into plausible-looking evidence, and a response that isn't a
        JSON object at all raises VisionResponseError instead of silently
        yielding an empty-but-successful result. A photo we failed to read
        must never be indistinguishable from a photo that showed nothing.
        """
        payload = cls._extract_json_object(raw)

        if not isinstance(payload.get("is_vehicle"), bool):
            raise VisionResponseError("response is missing a boolean 'is_vehicle'")
        if not isinstance(payload.get("is_truck_tractor_unit"), bool):
            raise VisionResponseError("response is missing a boolean 'is_truck_tractor_unit'")

        return VisionResult(
            is_vehicle=payload["is_vehicle"],
            is_truck_tractor_unit=payload["is_truck_tractor_unit"],
            vehicle_category_guess=cls._optional_text(payload.get("vehicle_category_guess")),
            make_guess=cls._optional_text(payload.get("make_guess")),
            model_guess=cls._optional_text(payload.get("model_guess")),
            visible_badge_text=cls._optional_text(payload.get("visible_badge_text")),
            structural_damage_suspected=payload.get("structural_damage_suspected") is True,
            tire_concern_noted=payload.get("tire_concern_noted") is True,
            extracted_specs=cls._clean_specs(payload.get("extracted_specs")),
            component_observations=cls._clean_observations(payload.get("component_observations")),
            quality_issues=cls._clean_string_list(payload.get("quality_issues")),
            notes=cls._optional_text(payload.get("notes")) or "",
        )

    @staticmethod
    def _extract_json_object(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) < 2:
                raise VisionResponseError("unterminated code fence in response")
            text = parts[1]
            if text.startswith("json"):
                text = text[len("json") :]
        text = text.strip()
        if not text:
            raise VisionResponseError("empty response")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VisionResponseError(f"response was not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise VisionResponseError(f"expected a JSON object, got {type(payload).__name__}")
        return payload

    @staticmethod
    def _optional_text(value) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"null", "none", "unknown", "n/a"}:
            return None
        return cleaned[:_MAX_TEXT_LEN]

    @staticmethod
    def _clean_specs(value) -> dict[str, str]:
        """Keep only whitelisted, individually plausible spec readings.

        A model that hallucinates `{"price": "2500000"}` or reports a
        mileage of "about 400k" contributes nothing rather than something
        wrong — these values become evidence records that gate a real price.
        """
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key, raw_value in value.items():
            if key not in ALLOWED_SPEC_FIELDS:
                continue
            if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
                continue
            text = str(raw_value).strip()
            if not text:
                continue
            if key in _INTEGER_SPEC_FIELDS:
                digits = text.replace(" ", "").replace(".", "").replace(",", "")
                if not digits.isdigit():
                    continue
                text = digits
            cleaned[key] = text[:_MAX_TEXT_LEN]
        return cleaned

    @classmethod
    def _clean_observations(cls, value) -> list[ComponentObservation]:
        if not isinstance(value, list):
            return []
        observations: list[ComponentObservation] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            component = cls._optional_text(entry.get("component"))
            observation = cls._optional_text(entry.get("observation"))
            if not component or not observation:
                continue
            visibility = cls._optional_text(entry.get("visibility")) or "clear"
            if visibility not in _ALLOWED_VISIBILITY:
                visibility = "clear"
            observations.append(
                ComponentObservation(
                    component=component,
                    observation=observation,
                    visibility=visibility,
                    recommended_action=cls._optional_text(entry.get("recommended_action")) or "none",
                )
            )
        return observations

    @classmethod
    def _clean_string_list(cls, value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [text for text in (cls._optional_text(item) for item in value) if text]


def get_vision_adapter() -> VisionAdapter:
    settings = get_settings()
    if settings.vision_adapter == "anthropic":
        return AnthropicVisionAdapter()
    return MockVisionAdapter()
