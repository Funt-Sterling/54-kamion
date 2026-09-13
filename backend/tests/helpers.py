"""Shared test utilities: synthetic images (so quality checks have something
real to grade) and a scriptable stand-in for the vision adapter (so gate
scenarios are deterministic instead of depending on a live model call)."""

from pathlib import Path

import numpy as np
from PIL import Image

from app.contract import VISION_SCHEMA_VERSION
from app.services.proposal import parse_proposal
from app.services.vision import VisionAdapter, VisionCallResult


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
    """Wraps a plain function so tests control exactly what "the model"
    returns, with no network access and no API key.

    The function may return a VisionProposal, a VisionCallResult, or raise.
    It is called WITHOUT a hint, matching the hint-blind production
    signature — a test cannot accidentally depend on the request leaking
    into perception, because there is nowhere for it to leak.
    """

    def __init__(self, fn):
        self._fn = fn

    def analyze_image(self, image_path) -> VisionCallResult:
        produced = self._fn(image_path, None)
        if isinstance(produced, VisionCallResult):
            return produced
        return VisionCallResult(
            proposal=produced,
            raw_text="{scripted}",
            model_id="scripted-test-model",
            prompt_version="test",
            schema_version=VISION_SCHEMA_VERSION,
            usage={},
            latency_ms=0,
        )


def patch_vision_adapter(monkeypatch, fn) -> None:
    monkeypatch.setattr("app.routers.media.get_vision_adapter", lambda: ScriptedVisionAdapter(fn))


def truck_result(
    *,
    subject_extent: str = "whole",
    subject_category: str = "tractor_unit",
    visible_views: set[str] | None = None,
    readings: list[dict] | None = None,
    axle_geometry: dict | None = None,
    candidates: list[dict] | None = None,
    findings: list[dict] | None = None,
    **legacy,
):
    """Build a v1 VisionProposal for a scripted adapter.

    Views default to absent, so a test only credits what it explicitly
    names — a test can never accidentally rely on a view it did not ask
    for, which is the same discipline the production admission rules apply.
    """
    if legacy:
        raise TypeError(
            f"truck_result no longer accepts {sorted(legacy)}; the v1 contract uses "
            "views/readings/candidates/axle_geometry (see app/services/proposal.py)"
        )

    views = {name: {"visibility": "visible", "usable": True} for name in (visible_views or set())}
    return parse_proposal(
        proposal_json(
            extent=subject_extent,
            category=subject_category,
            views=views,
            readings=readings,
            axle_geometry=axle_geometry,
            candidates=candidates,
            findings=findings,
        )
    )


# --- v1 proposal fixtures -----------------------------------------------------

import json  # noqa: E402

from app.contract import ALL_VIEWS  # noqa: E402


def proposal_dict(
    *,
    extent: str = "whole",
    category: str = "tractor_unit",
    views: dict | None = None,
    readings: list | None = None,
    axle_geometry: dict | None = None,
    candidates: list | None = None,
    findings: list | None = None,
    image_limitations: list | None = None,
    drop_views: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a schema-valid v1 proposal, then let a test break one thing.

    Defaults every required view to absent/unusable so a test only has to
    state the views it cares about — which also means a test can never
    accidentally rely on a view it didn't ask for.
    """
    all_views = {view: {"visibility": "absent", "usable": False} for view in ALL_VIEWS}
    for view, value in (views or {}).items():
        all_views[view] = value
    for view in drop_views or []:
        all_views.pop(view, None)

    payload: dict = {
        "schema_version": VISION_SCHEMA_VERSION,
        "subject": {"extent": extent, "category": category},
        "views": all_views,
        "readings": readings or [],
        "candidates": candidates or [],
        "findings": findings or [],
        "image_limitations": image_limitations or [],
        "notes": "",
    }
    if axle_geometry is not None:
        payload["axle_geometry"] = axle_geometry
    payload.update(extra or {})
    return payload


def proposal_json(**kwargs) -> str:
    return json.dumps(proposal_dict(**kwargs))


# --- scenario proposals ---------------------------------------------------------
#
# Scripted stand-ins for what a model might propose about typical photos. They
# exercise how the software TREATS a proposal; they say nothing about whether
# a real model would produce them for a real image.

_REGION = {"x": 10, "y": 10, "width": 200, "height": 60}
_VISIBLE = {"visibility": "visible", "usable": True}


def front_badge_proposal(badge_text: str = "F-MAX", *, findings: list | None = None, extra_views: dict | None = None):
    """Whole tractor, front three-quarter, readable model badge."""
    return parse_proposal(
        proposal_json(
            views={"front": _VISIBLE, "badge": _VISIBLE, **(extra_views or {})},
            readings=[
                {"kind": "badge", "raw_text": badge_text, "readability": "readable", "supporting_view": "badge", "region": _REGION}
            ],
            findings=findings,
        )
    )


def appearance_only_proposal(family: str = "F-MAX"):
    """Whole tractor, no legible badge — the family is a shape guess."""
    return parse_proposal(
        proposal_json(
            views={"front": _VISIBLE},
            candidates=[{"field": "model_family", "value": family, "basis": "appearance"}],
        )
    )


def side_proposal(axles: int = 2, *, findings: list | None = None):
    """Whole tractor side profile with every axle in frame."""
    return parse_proposal(
        proposal_json(
            views={"side": _VISIBLE, "tire": _VISIBLE},
            axle_geometry={
                "visible_axle_count": axles,
                "whole_relevant_geometry_visible": True,
                "supporting_view": "side",
                "region": _REGION,
            },
            findings=findings,
        )
    )


def odometer_proposal(raw: str = "365000", unit: str = "km"):
    """Tight odometer crop: readable total, known unit, located."""
    return parse_proposal(
        proposal_json(
            extent="partial",
            category="unknown",
            views={"odometer": _VISIBLE},
            readings=[
                {
                    "kind": "total_odometer",
                    "raw_text": raw,
                    "readability": "readable",
                    "unit": unit,
                    "is_total": True,
                    "supporting_view": "odometer",
                    "region": _REGION,
                }
            ],
        )
    )


def closeup_proposal(view: str, *, findings: list | None = None, category: str = "unknown"):
    return parse_proposal(proposal_json(extent="partial", category=category, views={view: _VISIBLE}, findings=findings))


def upload_scripted(client, monkeypatch, tmp_path, session_id: str, proposal, *, name: str, seed: int, hint: str | None = None):
    """Upload one distinct synthetic image whose analysis returns `proposal`."""
    patch_vision_adapter(monkeypatch, lambda path, _hint: proposal)
    response = upload_media(client, session_id, make_image(tmp_path / name, seed=seed), component_hint=hint)
    assert response.status_code == 201, response.text
    return response.json()
