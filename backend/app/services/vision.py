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

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
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
            "- make_guess (string or null)\n"
            "- model_guess (string or null)\n"
            "- extracted_specs (object; ONLY fields actually legible in this photo, e.g. "
            '{"mileage_km": "480000"} if an odometer is clearly readable — omit otherwise)\n'
            "- component_observations (array of {component, observation, visibility, "
            "recommended_action}; one entry per distinct thing you can say something concrete "
            "about; recommended_action is 'none' unless a retake/closer photo would help)\n"
            "- quality_issues (array of strings, e.g. 'too_dark', 'too_blurry', 'obstructed')\n"
            "- notes (short string)\n\n"
            "Never write 'mechanically sound' or invent a condition score. If you cannot tell "
            "something, say so in notes rather than guessing."
        )

    @staticmethod
    def _parse(raw: str) -> VisionResult:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[len("json"):]
        data = json.loads(raw.strip())
        observations = [ComponentObservation(**o) for o in data.get("component_observations", [])]
        return VisionResult(
            is_vehicle=bool(data.get("is_vehicle", False)),
            is_truck_tractor_unit=bool(data.get("is_truck_tractor_unit", False)),
            vehicle_category_guess=data.get("vehicle_category_guess"),
            make_guess=data.get("make_guess"),
            model_guess=data.get("model_guess"),
            extracted_specs=data.get("extracted_specs", {}),
            component_observations=observations,
            quality_issues=data.get("quality_issues", []),
            notes=data.get("notes", ""),
        )


def get_vision_adapter() -> VisionAdapter:
    settings = get_settings()
    if settings.vision_adapter == "anthropic":
        return AnthropicVisionAdapter()
    return MockVisionAdapter()
