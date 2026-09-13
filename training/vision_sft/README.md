# TIRage vision SFT data prep

`prepare_sft_data.py` turns real, permissioned, non-evaluation truck photos
+ hand/model-assisted `VisionProposal` labels into Vertex/Gemini
supervised-fine-tuning JSONL — validated through the actual production
parser (`backend/app/services/proposal.parse_proposal`), with hard
SHA256-based rejection of anything that also appears in the GOLD
`evaluation/visual/manifest.json` set.

**Current state: 0 usable source images found anywhere on this machine.**
Searched this repo and `$HOME`, `/home/user`, `/root`, `/tmp`, `/mnt`,
`/opt` for `super_scraper_handoff`, TruckMarket/Autoline/USP-Truck/TRODO
image dumps, and any local `.jpg/.jpeg/.png/.webp` files — none exist. This
container's network egress is also blocked (confirmed against
truckmarket.com.tr), so nothing can be fetched from here either. The
pipeline mechanics are real and tested (`tests/`, 9/9 passing) — what's
missing is source pixels + labels, not code. See the chat report for the
full explanation.

## Usage once real images exist

```
<source-dir>/<vehicle_group>/labels.json      # {filename: VisionProposal payload}
<source-dir>/<vehicle_group>/front.jpg
<source-dir>/<vehicle_group>/tire.jpg
```

```bash
python prepare_sft_data.py --source-dir /path/to/real/images --dry-run   # counts only
python prepare_sft_data.py --source-dir /path/to/real/images            # writes output/{train,val}.jsonl
python -m pytest tests/ -v
```

One `vehicle_group` = one physical truck; all its images land on the same
side of the train/val split, by a deterministic hash of the group name (see
`assign_split`), same pattern as `data_pipeline/partition.py`.

## Labels

There is no labeling source wired up yet either — `labels.json` is written
by a person (or, only if unavoidable, a separate labeling pass that is
never `evaluation/visual/`'s own labels). Nothing here calls a model to
generate its own training targets.
