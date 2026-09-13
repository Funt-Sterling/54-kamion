"""Proves the prep pipeline's mechanics — leakage rejection, group-aware
split, and real-parser validation — using synthetic, clearly-fake bytes.
Never reads or writes anything under evaluation/visual/ except a tmp_path
copy of the manifest shape, so this can never touch the real GOLD set.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prepare_sft_data import (  # noqa: E402
    VISION_SCHEMA_VERSION,
    assign_split,
    collect_candidates,
    load_eval_hashes,
    sha256_file,
    write_jsonl,
)


def _valid_target(**overrides) -> dict:
    views = {
        v: {"visibility": "absent", "usable": False}
        for v in ("front", "rear", "side", "tire", "dashboard", "odometer", "cab", "chassis", "badge")
    }
    views["front"] = {"visibility": "visible", "usable": True}
    target = {
        "schema_version": VISION_SCHEMA_VERSION,
        "subject": {"extent": "whole", "category": "tractor_unit"},
        "views": views,
    }
    target.update(overrides)
    return target


def _write_group(root: Path, group: str, images: dict[str, bytes], labels: dict) -> None:
    group_dir = root / group
    group_dir.mkdir(parents=True)
    for name, data in images.items():
        (group_dir / name).write_bytes(data)
    (group_dir / "labels.json").write_text(json.dumps(labels))


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib

    f = tmp_path / "x.jpg"
    f.write_bytes(b"synthetic-non-image-bytes")
    assert sha256_file(f) == hashlib.sha256(b"synthetic-non-image-bytes").hexdigest()


def test_valid_target_is_accepted_via_real_parser(tmp_path):
    src = tmp_path / "src"
    _write_group(src, "truck-0001", {"front.jpg": b"fake-bytes-truck1-front"}, {"front.jpg": _valid_target()})
    report = collect_candidates(src, eval_hashes=set())
    assert len(report.train) + len(report.val) == 1
    assert not report.rejected_invalid_target
    assert not report.rejected_leakage
    assert not report.rejected_unlabeled


def test_invalid_target_is_rejected_via_real_parser(tmp_path):
    """Missing a required view key must be caught by the REAL parser, not a
    hand-rolled check — this is the production rule from proposal.py."""
    src = tmp_path / "src"
    bad_target = _valid_target()
    del bad_target["views"]["badge"]  # every one of the 9 views is required
    _write_group(src, "truck-0002", {"front.jpg": b"fake-bytes-truck2-front"}, {"front.jpg": bad_target})
    report = collect_candidates(src, eval_hashes=set())
    assert len(report.train) + len(report.val) == 0
    assert len(report.rejected_invalid_target) == 1
    assert "badge" in report.rejected_invalid_target[0][1]


def test_unlabeled_image_is_rejected(tmp_path):
    src = tmp_path / "src"
    _write_group(src, "truck-0003", {"front.jpg": b"fake-bytes-truck3-front"}, labels={})
    report = collect_candidates(src, eval_hashes=set())
    assert report.rejected_unlabeled == ["truck-0003/front.jpg"]


def test_eval_leakage_is_hard_rejected(tmp_path):
    leaked_bytes = b"this-exact-image-is-also-in-the-gold-eval-set"
    src = tmp_path / "src"
    _write_group(src, "truck-0004", {"front.jpg": leaked_bytes}, {"front.jpg": _valid_target()})

    leaked_hash = sha256_file(src / "truck-0004" / "front.jpg")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"images": [{"sha256": leaked_hash}, {"sha256": None}]}))

    eval_hashes = load_eval_hashes(manifest_path)
    assert leaked_hash in eval_hashes

    report = collect_candidates(src, eval_hashes=eval_hashes)
    assert report.rejected_leakage == ["truck-0004/front.jpg"]
    assert len(report.train) + len(report.val) == 0


def test_load_eval_hashes_ignores_null_and_missing_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"images": [{"sha256": None}, {"sha256": None}]}))
    assert load_eval_hashes(manifest_path) == set()
    assert load_eval_hashes(tmp_path / "does-not-exist.json") == set()


def test_vehicle_group_never_split_across_train_and_val(tmp_path):
    src = tmp_path / "src"
    images = {f"view{i}.jpg": f"fake-bytes-{i}".encode() for i in range(6)}
    labels = {name: _valid_target() for name in images}
    _write_group(src, "truck-0005", images, labels)

    report = collect_candidates(src, eval_hashes=set())
    groups_in_train = {r["vehicle_group"] for r in report.train}
    groups_in_val = {r["vehicle_group"] for r in report.val}
    assert not (groups_in_train & groups_in_val)
    assert len(report.train) + len(report.val) == 6


def test_assign_split_is_deterministic():
    assert assign_split("truck-0001") == assign_split("truck-0001")


def test_write_jsonl_round_trips_through_the_real_parser(tmp_path):
    from prepare_sft_data import parse_proposal

    records = [
        {
            "image": "x.jpg",
            "sha256": "abc123",
            "vehicle_group": "truck-0001",
            "instruction": "do the thing",
            "target": _valid_target(),
        }
    ]
    out = tmp_path / "train.jsonl"
    write_jsonl(records, out)

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    # The stored target is itself a JSON string — exactly the shape
    # parse_proposal expects as raw model output.
    parse_proposal(row["target"])
