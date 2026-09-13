"""Tests for the scorer and the harness's refusals — not for any model.

Labels below are hand-written fixtures describing hypothetical images, used
only to check the arithmetic and the refusal rules. They are not ground
truth for anything and no real image is involved.

    cd backend && .venv/bin/python -m pytest ../evaluation/visual/test_scoring.py -q
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "backend"))
sys.path.insert(0, str(HERE))

from app.services.admission import admit  # noqa: E402
from app.services.proposal import parse_proposal  # noqa: E402

import scoring  # noqa: E402

REGION = {"x": 1, "y": 1, "width": 10, "height": 10}


def _proposal(**kwargs):
    views = {v: {"visibility": "absent", "usable": False} for v in scoring.VIEWS}
    views.update(kwargs.pop("views", {}))
    payload = {
        "schema_version": "v1",
        "subject": kwargs.pop("subject", {"extent": "partial", "category": "unknown"}),
        "views": views,
        "readings": kwargs.pop("readings", []),
        "candidates": [],
        "findings": [],
        "image_limitations": [],
        "notes": "",
    }
    return admit(parse_proposal(json.dumps(payload)))


def _label(**kwargs):
    views = {v: {"visibility": "absent", "usable": False} for v in scoring.VIEWS}
    views.update(kwargs.pop("views", {}))
    label = {
        "labeler": "Test Person",
        "subject": {"extent": "partial", "category": "unknown"},
        "views": views,
        "odometer": {"readable": False},
        "model_family": {"readable": False},
        "make": {"readable": False},
        "visible_axle_count": None,
        "forbidden_claims": [],
    }
    label.update(kwargs)
    return label


VISIBLE = {"visibility": "visible", "usable": True}


def _odometer_reading(digits="365000", unit="km"):
    return [{"kind": "total_odometer", "raw_text": digits, "readability": "readable", "unit": unit, "is_total": True, "supporting_view": "odometer", "region": REGION}]


def test_exact_odometer_and_view_agreement():
    admission = _proposal(views={"odometer": VISIBLE}, readings=_odometer_reading())
    label = _label(views={"odometer": VISIBLE}, odometer={"readable": True, "digits": "365 000", "unit": "km"})
    score = scoring.score_admission("i1", "m", label, admission)
    assert score.odometer == "exact"
    assert score.view_tp["odometer"] == 1 and score.critical == []


def test_mileage_on_an_image_a_human_marked_unreadable_is_critical():
    admission = _proposal(views={"odometer": VISIBLE}, readings=_odometer_reading())
    label = _label(views={"odometer": {"visibility": "visible", "usable": False}})
    score = scoring.score_admission("i1", "m", label, admission)
    assert "invented_mileage" in score.critical


def test_view_admitted_where_human_says_absent_is_wrong_view_coverage():
    admission = _proposal(views={"front": VISIBLE})
    score = scoring.score_admission("i1", "m", _label(), admission)
    assert "wrong_view_coverage:front" in score.critical


def test_abstention_is_counted_not_hidden():
    admission = _proposal(views={"odometer": VISIBLE})
    label = _label(views={"odometer": VISIBLE}, odometer={"readable": True, "digits": "365000", "unit": "km"})
    report = scoring.aggregate([scoring.score_admission("i1", "m", label, admission)])["m"]
    assert report["odometer"]["abstained"] == 1
    assert report["clear_case_success"] == 0.0
    assert report["pass"]["admitted_odometer_all_exact"] is None  # nothing admitted: not a pass


def test_zero_denominator_is_na_not_perfect():
    report = scoring.aggregate([scoring.score_admission("i1", "m", _label(), _proposal())])["m"]
    assert report["view_precision"] is None
    assert report["pass"]["view_precision_ge_95"] is None


def test_model_labeler_and_pending_rows_are_refused():
    pending = {"image_id": "a", "status": "pending", "label": None}
    assert scoring.label_problems(pending)
    by_model = {
        "image_id": "b", "status": "labeled", "sha256": "x", "split": "dev", "vehicle_group": "g",
        "permission": {"source": "s", "granted_by": "p"}, "label": _label(labeler="Claude"),
    }
    assert any("person" in p for p in scoring.label_problems(by_model))


def test_vehicle_group_cannot_straddle_splits():
    rows = [{"vehicle_group": "truck-7", "split": "dev"}, {"vehicle_group": "truck-7", "split": "holdout"}]
    assert scoring.split_leaks(rows)


def test_budget_stops_before_exceeding_call_or_cost_limits():
    import pytest

    import evaluate_vision

    budget = evaluate_vision.Budget(max_calls=2, max_cost=1.0, per_call_reserve=0.4)
    budget.before_call()
    budget.charge(0.7)
    with pytest.raises(evaluate_vision.LimitReached):
        budget.before_call()  # 0.7 spent + 0.4 reserve would pass the $1.00 ceiling
    budget2 = evaluate_vision.Budget(max_calls=1, max_cost=5.0, per_call_reserve=0.1)
    budget2.before_call()
    with pytest.raises(evaluate_vision.LimitReached):
        budget2.before_call()
