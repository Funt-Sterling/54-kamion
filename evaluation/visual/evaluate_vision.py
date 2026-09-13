"""Human-labeled visual evaluation of the production vision contract.

Run from backend/ with its venv, so backend/.env supplies the API key:

    cd backend
    .venv/bin/python ../evaluation/visual/evaluate_vision.py validate
    .venv/bin/python ../evaluation/visual/evaluate_vision.py plan --split dev --models claude-sonnet-5,claude-opus-5
    .venv/bin/python ../evaluation/visual/evaluate_vision.py run --split dev \\
        --models claude-sonnet-5,claude-opus-5 --prices ../evaluation/visual/prices.json \\
        --max-calls 60 --max-cost-usd 5 --confirm-paid
    .venv/bin/python ../evaluation/visual/evaluate_vision.py rescore runs/<file>.json

Refusals are the point of this script:

* No paid call is made unless EVERY row in the requested split carries a
  human label, a permission record, a vehicle group and a matching image
  hash, and no vehicle group straddles splits.
* No paid call is made without per-model prices, because the cost ceiling
  cannot be enforced without them.
* Every real API request counts toward --max-calls, including SDK-level
  retries (disabled here) and the structured-output fallback. The run stops
  at the first limit or access failure; it never retries indefinitely and
  never silently swaps models.

The production prompt, schema, parser and admission rules are used
unchanged — the evaluation measures the contract the app actually runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE))

from scoring import ImageScore, aggregate, label_problems, score_admission, split_leaks  # noqa: E402

DEFAULT_MANIFEST = HERE / "manifest.json"
RUNS_DIR = HERE / "runs"


class LimitReached(RuntimeError):
    pass


def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["images"] if isinstance(data, dict) else data


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def readiness(rows: list[dict], split: str | None) -> tuple[list[dict], list[str]]:
    """Rows eligible for the split, and every reason the split is not ready."""
    problems = split_leaks(rows)
    selected = [r for r in rows if split is None or r.get("split") == split]
    if split is not None and not selected:
        problems.append(f"no manifest rows assigned to split {split!r}")
    for row in selected if split is not None else rows:
        problems.extend(label_problems(row))
        image = HERE / str(row.get("path") or "")
        if row.get("path") and image.is_file():
            if row.get("sha256") and sha256_file(image) != row["sha256"]:
                problems.append(f"{row.get('image_id')}: image bytes do not match manifest sha256")
        else:
            problems.append(f"{row.get('image_id')}: image file not found at {row.get('path')!r}")
    return selected, problems


def cmd_validate(args) -> int:
    rows = load_manifest(Path(args.manifest))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("status", "?")] = counts.get(row.get("status", "?"), 0) + 1
    print(f"{len(rows)} manifest rows; by status: {counts}")
    for split in ("dev", "holdout"):
        selected, problems = readiness(rows, split)
        print(f"\n[{split}] {len(selected)} rows — {'READY' if not problems else f'NOT READY ({len(problems)} problems)'}")
        for problem in problems[:25]:
            print(f"  - {problem}")
        if len(problems) > 25:
            print(f"  … {len(problems) - 25} more")
    return 0


def cmd_plan(args) -> int:
    rows = load_manifest(Path(args.manifest))
    selected, problems = readiness(rows, args.split)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"split={args.split} images={len(selected)} models={models}")
    print(f"planned calls (no retries) = {len(selected) * len(models)}; --max-calls default 60")
    if problems:
        print(f"NOT RUNNABLE: {len(problems)} problem(s); first: {problems[0]}")
        return 2
    return 0


class CountingClient:
    """Wraps `client.messages.create` so every real request is counted and
    the budget is checked BEFORE the request is sent."""

    def __init__(self, inner, budget: "Budget"):
        self._inner = inner
        self._budget = budget

    def create(self, **kwargs):
        self._budget.before_call()
        return self._inner.create(**kwargs)


class Budget:
    def __init__(self, max_calls: int, max_cost: float, per_call_reserve: float):
        self.max_calls, self.max_cost, self.reserve = max_calls, max_cost, per_call_reserve
        self.calls, self.cost = 0, 0.0

    def before_call(self) -> None:
        if self.calls >= self.max_calls:
            raise LimitReached(f"call limit {self.max_calls} reached")
        if self.cost + self.reserve > self.max_cost:
            raise LimitReached(f"cost ceiling ${self.max_cost:.2f} would be exceeded (spent ${self.cost:.4f})")
        self.calls += 1

    def charge(self, usd: float) -> None:
        self.cost += usd


def _cost(usage: dict, price: dict) -> float:
    return (
        usage.get("input_tokens", 0) * price["input_per_mtok"]
        + usage.get("output_tokens", 0) * price["output_per_mtok"]
        + usage.get("cache_creation_input_tokens", 0) * price.get("cache_write_per_mtok", price["input_per_mtok"])
        + usage.get("cache_read_input_tokens", 0) * price.get("cache_read_per_mtok", price["input_per_mtok"])
    ) / 1_000_000


def _adapter_for(model: str, budget: Budget):
    os.environ["VISION_MODEL"] = model
    os.environ["VISION_MAX_RETRIES"] = "0"  # hidden SDK retries would bypass the call limit
    from app.config import get_settings
    from app.services.vision import AnthropicVisionAdapter

    get_settings.cache_clear()
    adapter = AnthropicVisionAdapter()
    adapter._client.messages = CountingClient(adapter._client.messages, budget)  # noqa: SLF001
    return adapter


def cmd_run(args) -> int:
    rows = load_manifest(Path(args.manifest))
    selected, problems = readiness(rows, args.split)
    if problems:
        print(f"REFUSED: split {args.split!r} is not fully human-labeled/permissioned ({len(problems)} problems).")
        for problem in problems[:10]:
            print(f"  - {problem}")
        return 2
    if not args.confirm_paid:
        print("REFUSED: pass --confirm-paid to make real API calls.")
        return 2
    if not args.prices:
        print("REFUSED: --prices is required so the cost ceiling can be enforced.")
        return 2
    prices = json.loads(Path(args.prices).read_text())
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    missing = [m for m in models if m not in prices]
    if missing:
        print(f"REFUSED: no price entry for {missing}")
        return 2

    from app.services.admission import admit
    from app.services.proposal import ProposalSchemaError

    budget = Budget(args.max_calls, args.max_cost_usd, args.per_call_reserve_usd)
    records: list[dict] = []
    scores: list[ImageScore] = []
    stopped: str | None = None

    try:
        for model in models:
            adapter = _adapter_for(model, budget)
            for row in selected:
                started = time.monotonic()
                record = {"image_id": row["image_id"], "model_id": model, "raw_text": None, "usage": {}, "error": None}
                try:
                    call = adapter.analyze_image(HERE / row["path"])
                    record.update(raw_text=call.raw_text, usage=call.usage, structured=call.structured_output)
                    score = score_admission(row["image_id"], model, row["label"], admit(call.proposal))
                    score.latency_ms = call.latency_ms
                except LimitReached:
                    raise
                except ProposalSchemaError as exc:
                    score = ImageScore(row["image_id"], model, schema_error=str(exc)[:300])
                    record["error"] = f"schema: {str(exc)[:300]}"
                except Exception as exc:  # access/transport failure: stop, do not retry
                    record["error"] = type(exc).__name__
                    records.append(record)
                    stopped = f"{model}: {type(exc).__name__} — stopping (no retries, no model swap)"
                    raise LimitReached(stopped) from exc
                if score.latency_ms is None:
                    score.latency_ms = int((time.monotonic() - started) * 1000)
                score.cost_usd = _cost(record["usage"], prices[model])
                budget.charge(score.cost_usd)
                records.append(record)
                scores.append(score)
    except LimitReached as exc:
        stopped = stopped or str(exc)
        print(f"STOPPED: {stopped}")

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS_DIR / f"{args.split}-{stamp}.json"
    report = aggregate(scores)
    out.write_text(
        json.dumps(
            {
                "split": args.split,
                "models": models,
                "limits": {"max_calls": args.max_calls, "max_cost_usd": args.max_cost_usd},
                "calls_made": budget.calls,
                "cost_usd": round(budget.cost, 4),
                "stopped": stopped,
                "complete": stopped is None and len(scores) == len(selected) * len(models),
                "report": report,
                "scores": [asdict(s) for s in scores],
                "records": records,
            },
            indent=2,
        )
    )
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_view"} for k, v in report.items()}, indent=2))
    print(f"calls={budget.calls} cost=${budget.cost:.4f} written to {out}")
    return 0 if stopped is None else 3


def cmd_rescore(args) -> int:
    """Re-score saved raw proposals against the CURRENT manifest labels.
    Makes no API calls — use after a label is adjudicated."""
    from app.services.admission import admit
    from app.services.proposal import ProposalSchemaError, parse_proposal

    run = json.loads(Path(args.run_file).read_text())
    rows = {r["image_id"]: r for r in load_manifest(Path(args.manifest))}
    scores = []
    for record in run["records"]:
        row = rows.get(record["image_id"])
        if row is None or label_problems(row) or not record.get("raw_text"):
            continue
        try:
            scores.append(score_admission(row["image_id"], record["model_id"], row["label"], admit(parse_proposal(record["raw_text"]))))
        except ProposalSchemaError as exc:
            scores.append(ImageScore(row["image_id"], record["model_id"], schema_error=str(exc)[:300]))
    print(json.dumps(aggregate(scores), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    plan = sub.add_parser("plan")
    plan.add_argument("--split", choices=("dev", "holdout"), required=True)
    plan.add_argument("--models", required=True)
    run = sub.add_parser("run")
    run.add_argument("--split", choices=("dev", "holdout"), required=True)
    run.add_argument("--models", required=True)
    run.add_argument("--prices")
    run.add_argument("--max-calls", type=int, default=60)
    run.add_argument("--max-cost-usd", type=float, default=5.0)
    run.add_argument("--per-call-reserve-usd", type=float, default=0.15)
    run.add_argument("--confirm-paid", action="store_true")
    rescore = sub.add_parser("rescore")
    rescore.add_argument("run_file")
    args = parser.parse_args()
    return {"validate": cmd_validate, "plan": cmd_plan, "run": cmd_run, "rescore": cmd_rescore}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
