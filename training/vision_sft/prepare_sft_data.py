"""Prepare Gemini/Vertex supervised-fine-tuning data for the TIRage vision
step, against the REAL production `VisionProposal` v1 contract.

Zero third-party dependencies on purpose (stdlib only) — this only ever
touches paths, bytes, and JSON, never pixels, so it doesn't need Pillow or
anything else installed.

Input convention (you point --source-dir at wherever real, permissioned,
NON-EVALUATION images actually live):

    <source-dir>/<vehicle_group>/labels.json      # {filename: VisionProposal payload}
    <source-dir>/<vehicle_group>/front.jpg
    <source-dir>/<vehicle_group>/tire.jpg
    ...

`labels.json` maps each image filename in that vehicle's folder to a dict
in the exact shape `backend/app/services/proposal.parse_proposal` accepts
(schema_version, subject{extent,category}, views{<all 9 view keys>}, and
optionally readings/axle_geometry/candidates/findings/image_limitations/
notes). UNKNOWN is a correct value, not a gap to fill in.

Every candidate image is:
  1. SHA256'd and hard-rejected if that hash appears in
     evaluation/visual/manifest.json (the GOLD eval set — never touched,
     never read for content, only its hash list).
  2. rejected if unlabeled, or if its label fails to parse through the
     REAL parser (`parse_proposal`) — never a hand-rolled validator.
  3. grouped by vehicle_group and split train/val by a deterministic hash
     of the group name, so one vehicle can never appear on both sides.

Usage:
    python prepare_sft_data.py --source-dir /path/to/real/images
    python prepare_sft_data.py --source-dir /path/to/real/images --dry-run  # count only, no JSONL written
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
EVAL_MANIFEST_PATH = REPO_ROOT / "evaluation" / "visual" / "manifest.json"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "output"

sys.path.insert(0, str(BACKEND_DIR))
from app.contract import ALL_VIEWS, VISION_SCHEMA_VERSION  # noqa: E402
from app.services.proposal import ProposalSchemaError, parse_proposal  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

INSTRUCTION = (
    "You are inspecting ONE photo submitted for a used-truck appraisal in "
    "Turkey. Only report what is visible in THIS image. Output ONLY a JSON "
    f"object matching TIRage VisionProposal schema_version {VISION_SCHEMA_VERSION}: "
    "schema_version, subject {extent, category}, views (an object with ALL "
    f"of these keys, every one required: {', '.join(ALL_VIEWS)}), and "
    "optionally readings, axle_geometry, candidates, findings, "
    "image_limitations, notes. UNKNOWN/absent/unclear is the correct value "
    "whenever the pixels do not support a fact — never guess."
)


@dataclass
class PrepareReport:
    train: list[dict] = field(default_factory=list)
    val: list[dict] = field(default_factory=list)
    rejected_leakage: list[str] = field(default_factory=list)
    rejected_unlabeled: list[str] = field(default_factory=list)
    rejected_invalid_target: list[tuple[str, str]] = field(default_factory=list)
    vehicle_groups_seen: set[str] = field(default_factory=set)

    @property
    def scenarios_covered(self) -> list[str]:
        seen: set[str] = set()
        for record in (*self.train, *self.val):
            target = record["target"]
            subject = target.get("subject", {})
            if subject.get("extent") in ("partial", "unclear"):
                seen.add(subject["extent"])
            if subject.get("category") == "unknown":
                seen.add("unknown")
            for view_name, view in (target.get("views") or {}).items():
                if view.get("visibility") == "visible" and view.get("usable"):
                    seen.add(view_name)
                elif view.get("visibility") == "unclear":
                    seen.add("blurry/unclear")
        return sorted(seen)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_eval_hashes(manifest_path: Path) -> set[str]:
    """Every non-null sha256 already recorded in the GOLD eval manifest.
    Reads only hashes — never image bytes, never labels."""
    if not manifest_path.exists():
        return set()
    manifest = json.loads(manifest_path.read_text())
    return {row["sha256"] for row in manifest.get("images", []) if row.get("sha256")}


def assign_split(vehicle_group: str, val_fraction: float = 0.15) -> str:
    """Deterministic hash bucket, same pattern as data_pipeline/partition.py,
    so re-running never reshuffles a vehicle across train/val."""
    digest = hashlib.sha256(vehicle_group.encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return "val" if bucket < round(val_fraction * 100) else "train"


def collect_candidates(source_dir: Path, eval_hashes: set[str]) -> PrepareReport:
    report = PrepareReport()
    if not source_dir.exists():
        return report

    for group_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
        report.vehicle_groups_seen.add(group_dir.name)
        labels_path = group_dir / "labels.json"
        labels: dict = {}
        if labels_path.exists():
            try:
                labels = json.loads(labels_path.read_text())
            except json.JSONDecodeError as exc:
                report.rejected_invalid_target.append((str(labels_path), f"labels.json is not valid JSON: {exc}"))

        for image_path in sorted(group_dir.iterdir()):
            if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            rel = str(image_path.relative_to(source_dir))
            digest = sha256_file(image_path)

            if digest in eval_hashes:
                report.rejected_leakage.append(rel)
                continue

            target = labels.get(image_path.name)
            if target is None:
                report.rejected_unlabeled.append(rel)
                continue

            try:
                # Validate through the REAL production parser, not a
                # hand-rolled copy of its rules.
                parse_proposal(json.dumps(target))
            except ProposalSchemaError as exc:
                report.rejected_invalid_target.append((rel, str(exc)))
                continue

            record = {
                "image": str(image_path),
                "sha256": digest,
                "vehicle_group": group_dir.name,
                "instruction": INSTRUCTION,
                "target": target,
            }
            split = assign_split(group_dir.name)
            (report.train if split == "train" else report.val).append(record)

    return report


def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            # Vertex/Gemini SFT shape: one instruction+image in, one target
            # JSON string out. The target is re-serialized (not passed
            # through) so the JSONL always reflects what parse_proposal
            # actually accepted, not the raw label file's formatting.
            row = {
                "image": record["image"],
                "sha256": record["sha256"],
                "vehicle_group": record["vehicle_group"],
                "instruction": record["instruction"],
                "target": json.dumps(record["target"], sort_keys=True),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--eval-manifest", type=Path, default=EVAL_MANIFEST_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Report counts only, write nothing")
    args = parser.parse_args()

    eval_hashes = load_eval_hashes(args.eval_manifest)
    report = collect_candidates(args.source_dir, eval_hashes)

    if not args.dry_run:
        write_jsonl(report.train, args.out_dir / "train.jsonl")
        write_jsonl(report.val, args.out_dir / "val.jsonl")

    print(f"TRAINING EXAMPLES: {len(report.train)}")
    print(f"VALIDATION EXAMPLES: {len(report.val)}")
    print(f"VEHICLE GROUPS SEEN: {len(report.vehicle_groups_seen)}")
    print(f"REJECTED (eval leakage): {len(report.rejected_leakage)}")
    print(f"REJECTED (unlabeled): {len(report.rejected_unlabeled)}")
    print(f"REJECTED (invalid target): {len(report.rejected_invalid_target)}")
    print(f"SCENARIOS COVERED: {', '.join(report.scenarios_covered) or 'none'}")
    if report.rejected_leakage:
        print(f"LEAKAGE HASHES MATCHED (never used for training): {report.rejected_leakage}")


if __name__ == "__main__":
    main()
