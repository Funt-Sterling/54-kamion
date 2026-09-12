"""Shared test utilities: synthetic images (so quality checks have something
real to grade) and a scriptable stand-in for the vision adapter (so gate
scenarios are deterministic instead of depending on a live model call)."""

from pathlib import Path

import numpy as np
from PIL import Image

from app.services.vision import VisionAdapter, VisionResult


def make_image(path: Path, *, dark: bool = False, blurry: bool = False, size: tuple[int, int] = (640, 480), seed: int = 42) -> Path:
    """A flat-color image is both "dark" (if the color is near-black) and
    "blurry" (a constant image has zero Laplacian variance) at once, so
    `dark` and `blurry` are separate knobs but can't fully isolate from each
    other at the extremes — tests should assert on `reject_reason` rather
    than assuming only one quality issue fires."""
    width, height = size
    if dark:
        arr = np.full((height, width, 3), 5, dtype=np.uint8)
    elif blurry:
        arr = np.full((height, width, 3), 120, dtype=np.uint8)
    else:
        rng = np.random.default_rng(seed)
        arr = rng.integers(80, 200, size=(height, width, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path)
    return path


def upload_media(client, session_id: str, image_path: Path, *, kind: str = "photo", source: str = "captured", component_hint: str | None = None):
    data = {"kind": kind, "source": source}
    if component_hint:
        data["component_hint"] = component_hint
    with open(image_path, "rb") as f:
        return client.post(
            f"/sessions/{session_id}/media",
            data=data,
            files={"file": (image_path.name, f, "image/jpeg")},
        )


class ScriptedVisionAdapter(VisionAdapter):
    """Wraps a plain function so tests can control exactly what "the vision
    model" reports (or raise, to exercise the recoverable-error path)
    without any network access or API key."""

    def __init__(self, fn):
        self._fn = fn

    def analyze_image(self, image_path, component_hint=None) -> VisionResult:
        return self._fn(image_path, component_hint)


def patch_vision_adapter(monkeypatch, fn) -> None:
    monkeypatch.setattr("app.routers.media.get_vision_adapter", lambda: ScriptedVisionAdapter(fn))


def truck_result(**overrides) -> VisionResult:
    """A plausible, fully-resolved tractor-unit photo result — good default
    to override piecemeal in individual tests."""
    defaults = dict(
        is_vehicle=True,
        is_truck_tractor_unit=True,
        vehicle_category_guess="tractor_unit",
        make_guess="Mercedes-Benz",
        model_guess="actros",
        visible_badge_text="ACTROS",
        structural_damage_suspected=False,
        tire_concern_noted=False,
        extracted_specs={},
        component_observations=[],
        quality_issues=[],
        notes="",
    )
    defaults.update(overrides)
    return VisionResult(**defaults)
