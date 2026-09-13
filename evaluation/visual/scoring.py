"""Scoring a vision proposal against a HUMAN label.

Pure functions: no network, no database, no model. Given what admission
accepted from a proposal and what a person recorded about the same image,
report agreement, abstention and critical false admissions.

Nothing here generates labels. A manifest row without a human label is not
scored — it is counted as pending, and a run over pending rows is refused
upstream in evaluate_vision.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

VIEWS = ("front", "rear", "side", "tire", "dashboard", "odometer", "cab", "chassis", "badge")
LABELED_STATUSES = ("labeled", "adjudicated")
SPLITS = ("dev", "holdout")

#: Critical false admission kinds (audit section G).
CRITICAL_KINDS = (
    "invented_mileage",
    "wrong_mileage",
    "unsupported_family_promoted",
    "wrong_family",
    "wrong_view_coverage",
    "false_tractor",
    "forbidden_claim",
)


# --- manifest validation -------------------------------------------------------


def label_problems(row: dict) -> list[str]:
    """Why a row cannot be scored. Empty list means it is a usable human label."""
    problems: list[str] = []
    rid = row.get("image_id", "?")
    if row.get("status") not in LABELED_STATUSES:
        return [f"{rid}: status {row.get('status')!r} is not a human label"]
    label = row.get("label")
    if not isinstance(label, dict):
        return [f"{rid}: no label object"]
    if not (isinstance(label.get("labeler"), str) and label["labeler"].strip()):
        problems.append(f"{rid}: label has no human labeler")
    if str(label.get("labeler", "")).strip().lower() in {"claude", "model", "gpt", "ai", "llm"}:
        problems.append(f"{rid}: labeler must be a person, not a model")
    if not row.get("sha256"):
        problems.append(f"{rid}: missing image sha256")
    permission = row.get("permission") or {}
    if not permission.get("source") or not permission.get("granted_by"):
        problems.append(f"{rid}: permission source/granted_by missing")
    if row.get("split") not in SPLITS:
        problems.append(f"{rid}: split must be one of {SPLITS}")
    if not row.get("vehicle_group"):
        problems.append(f"{rid}: missing vehicle_group")
    subject = label.get("subject") or {}
    if subject.get("category") is None or subject.get("extent") is None:
        problems.append(f"{rid}: subject extent/category unlabeled")
    views = label.get("views") or {}
    missing = [v for v in VIEWS if v not in views]
    if missing:
        problems.append(f"{rid}: views unlabeled: {missing}")
    return problems


def split_leaks(rows: list[dict]) -> list[str]:
    """A vehicle group must live in exactly one split (crops/retakes included)."""
    seen: dict[str, set[str]] = {}
    for row in rows:
        group, split = row.get("vehicle_group"), row.get("split")
        if group and split:
            seen.setdefault(group, set()).add(split)
    return [f"vehicle_group {group!r} appears in splits {sorted(splits)}" for group, splits in seen.items() if len(splits) > 1]


# --- per-image scoring ------------------------------------------------------------


@dataclass
class ImageScore:
    image_id: str
    model_id: str
    schema_error: str | None = None
    failure: str | None = None
    view_tp: dict[str, int] = field(default_factory=dict)
    view_fp: dict[str, int] = field(default_factory=dict)
    view_fn: dict[str, int] = field(default_factory=dict)
    odometer: str = "n/a"  # exact | wrong | abstained | invented | n/a
    family: str = "n/a"  # correct | wrong | abstained | promoted | n/a
    critical: list[str] = field(default_factory=list)
    hallucinated: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    cost_usd: float = 0.0


def _truth_usable_views(label: dict) -> set[str]:
    views = label.get("views") or {}
    return {v for v in VIEWS if (views.get(v) or {}).get("visibility") == "visible" and (views.get(v) or {}).get("usable") is True}


def _truth_absent_views(label: dict) -> set[str]:
    views = label.get("views") or {}
    return {v for v in VIEWS if (views.get(v) or {}).get("visibility") == "absent"}


def score_admission(image_id: str, model_id: str, label: dict, admission: Any) -> ImageScore:
    """Compare one AdmissionResult with one human label."""
    score = ImageScore(image_id=image_id, model_id=model_id)

    admitted_views = {view.view for view in admission.views}
    truth_views = _truth_usable_views(label)
    absent = _truth_absent_views(label)
    for view in VIEWS:
        score.view_tp[view] = int(view in admitted_views and view in truth_views)
        score.view_fp[view] = int(view in admitted_views and view not in truth_views)
        score.view_fn[view] = int(view not in admitted_views and view in truth_views)
        if view in admitted_views and view in absent:
            score.critical.append(f"wrong_view_coverage:{view}")

    claims = {claim.field: claim for claim in admission.claims}

    # Mileage: exact digits and unit, or abstain.
    truth_odo = label.get("odometer") or {}
    mileage = claims.get("mileage_km")
    if truth_odo.get("readable"):
        if mileage is None:
            score.odometer = "abstained"
        else:
            expected_km = _to_km(truth_odo.get("digits"), truth_odo.get("unit"))
            unit = (mileage.basis or {}).get("unit")
            if expected_km is not None and mileage.canonical_value == str(expected_km) and unit == truth_odo.get("unit"):
                score.odometer = "exact"
            else:
                score.odometer = "wrong"
                score.critical.append("wrong_mileage")
    elif mileage is not None:
        score.odometer = "invented"
        score.critical.append("invented_mileage")

    # Model family: an observation needs readable truth; candidates are not identity.
    truth_family = label.get("model_family") or {}
    family = claims.get("model_family")
    observed_family = family if family is not None and family.provenance == "observed_from_photo" else None
    if truth_family.get("readable"):
        if observed_family is None:
            score.family = "abstained"
        elif observed_family.canonical_value == truth_family.get("canonical"):
            score.family = "correct"
        else:
            score.family = "wrong"
            score.critical.append("wrong_family")
    elif observed_family is not None:
        score.family = "promoted"
        score.critical.append("unsupported_family_promoted")

    subject = label.get("subject") or {}
    category = claims.get("vehicle_category")
    if category is not None and not (subject.get("category") == "tractor_unit" and subject.get("extent") == "whole"):
        score.critical.append("false_tractor")

    truth_axles = label.get("visible_axle_count")
    axles = claims.get("visible_axle_count")
    if axles is not None and (truth_axles is None or str(truth_axles) != axles.canonical_value):
        score.hallucinated.append(f"visible_axle_count:{axles.canonical_value}")

    truth_make = label.get("make") or {}
    make = claims.get("make")
    if make is not None and (not truth_make.get("readable") or truth_make.get("canonical") != make.canonical_value):
        score.hallucinated.append(f"make:{make.canonical_value}")

    for forbidden in label.get("forbidden_claims") or []:
        if forbidden in claims:
            score.critical.append(f"forbidden_claim:{forbidden}")

    score.hallucinated.extend(c for c in score.critical if c not in score.hallucinated)
    return score


def _to_km(digits: Any, unit: Any) -> int | None:
    text = "".join(str(digits or "").split())
    if not text.isdigit():
        return None
    value = int(text)
    return value if unit == "km" else round(value * 1.609344) if unit == "mi" else None


# --- aggregation -------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    """None (reported as N/A) when the denominator is zero — never 100%."""
    return None if denominator == 0 else numerator / denominator


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def aggregate(scores: list[ImageScore]) -> dict:
    by_model: dict[str, list[ImageScore]] = {}
    for s in scores:
        by_model.setdefault(s.model_id, []).append(s)

    report: dict[str, dict] = {}
    for model_id, items in by_model.items():
        analysed = [s for s in items if s.schema_error is None and s.failure is None]
        per_view = {}
        tp = fp = fn = 0
        for view in VIEWS:
            vtp = sum(s.view_tp.get(view, 0) for s in analysed)
            vfp = sum(s.view_fp.get(view, 0) for s in analysed)
            vfn = sum(s.view_fn.get(view, 0) for s in analysed)
            per_view[view] = {"tp": vtp, "fp": vfp, "fn": vfn, "precision": _ratio(vtp, vtp + vfp), "recall": _ratio(vtp, vtp + vfn)}
            tp, fp, fn = tp + vtp, fp + vfp, fn + vfn

        odo = {k: sum(1 for s in analysed if s.odometer == k) for k in ("exact", "wrong", "abstained", "invented")}
        fam = {k: sum(1 for s in analysed if s.family == k) for k in ("correct", "wrong", "abstained", "promoted")}
        clear_cases = odo["exact"] + odo["wrong"] + odo["abstained"] + fam["correct"] + fam["wrong"] + fam["abstained"]
        clear_success = odo["exact"] + fam["correct"]
        admitted_odo = odo["exact"] + odo["wrong"]
        critical = [(s.image_id, c) for s in items for c in s.critical]
        latencies = [s.latency_ms for s in items if s.latency_ms is not None]

        report[model_id] = {
            "images": len(items),
            "schema_errors": sum(1 for s in items if s.schema_error),
            "call_failures": sum(1 for s in items if s.failure),
            "view_precision": _ratio(tp, tp + fp),
            "view_recall": _ratio(tp, tp + fn),
            "per_view": per_view,
            "odometer": odo,
            "model_family": fam,
            "clear_case_success": _ratio(clear_success, clear_cases),
            "clear_case_denominator": clear_cases,
            "critical_false_admissions": critical,
            "hallucinated_claims": sum(len(s.hallucinated) for s in items),
            "latency_ms_p50": _percentile(latencies, 0.5),
            "latency_ms_p95": _percentile(latencies, 0.95),
            "cost_usd": round(sum(s.cost_usd for s in items), 4),
            "pass": {
                "zero_critical_false_admissions": len(critical) == 0,
                "view_precision_ge_95": _meets(_ratio(tp, tp + fp), 0.95),
                "view_recall_ge_80": _meets(_ratio(tp, tp + fn), 0.80),
                "admitted_odometer_all_exact": None if admitted_odo == 0 else odo["wrong"] == 0,
                "clear_case_success_ge_80": _meets(_ratio(clear_success, clear_cases), 0.80),
            },
        }
    return report


def _meets(value: float | None, threshold: float) -> bool | None:
    """None when there is nothing to measure: not a pass."""
    return None if value is None else value >= threshold
