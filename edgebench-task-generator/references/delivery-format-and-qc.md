# EdgeBench format selection and staged QC contract

This document is the active bridge between user requirements, Benchmark
profiles, vendor documents and the reusable production line. It prevents a
Harbor report from being used to approve an SForge package, or the reverse.

EdgeBench V1 is the built-in `edgebench-v1` profile. Compatible Benchmarks may
use a custom profile derived from the user's requirements document. Thresholds
such as runtime, trial count, repeat count, architecture and reward type come
from the active profile; production integrity checks remain universal.

## Required source order

1. The user's task or batch format choice: `sforge-only` or `harbor`.
2. `Meta Vendor Data Spec V3.0.zip` and its normative Markdown.
3. `EdgeBench Vendor Dataset Requirements.md`, current `Requirement — V1`.
4. `01 过程小样质检v0.2（出题后，跑轨迹之前）.md` for the pre-rollout
   inspection/report shape.
5. `02 打包prompt v0.1(跑完轨迹后）.md` for post-rollout inventory, cleaning,
   link, mode and trajectory-reconciliation checks.
6. `rl-task-qc/` for the final independent static gate and its runtime
   instructions.

The archived requirement appended to the EdgeBench document and historical
V2.4-only naming opinions do not override the current V1/V3.0 mapping.

## Profile selection

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE --profile edgebench-v1
```

For a compatible Benchmark, use `--profile-file` with a validated profile JSON
and `--requirements-doc` with the user's source document. The receipt stores
both profile and requirements-document hashes.

## Format selection

Run this after task generation and before the main task review or any frontier
model run:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  delivery-format WORKSPACE --format sforge-only
# or
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  delivery-format WORKSPACE --format harbor
```

The receipt contains the frozen task SHA-256, reviewer identity, selection
time and the local source-document hashes. A task with a changed task SHA needs
a new selection receipt. Production tasks cannot pass rollout preflight without
one. The selection is a delivery decision; it is not evidence that any runtime
or platform check passed.

| Selected format | Customer task bytes | Required acceptance path | Packaging path |
|---|---|---|---|
| `sforge-only` | Task JSON, `BENCHMARK.yaml`, original English task text, sibling `contexts/work` and `contexts/judge`, scored evidence | `sforge-audit` | `package-samples` or `package-selected` |
| `harbor` | Unpacked Harbor task directories, `task.toml`, separate agent/verifier build contexts and Harbor evidence | `harbor-audit` plus Harbor runtime/rebuild/equivalence receipts | `export-harbor` or `package-selected` |

Both formats use SForge for frontier rollout in this production line. Harbor
also requires its own executed Harbor checks; SForge parity does not substitute
for those checks.

## Stage gates

| Stage | Required action | Evidence | Status meaning |
|---|---|---|---|
| After authoring, before trajectories | Run the 01 process inspection against the exact task package, read the public task before private verifier, run required NOP/reference/static controls, and write the independent report outside the candidate package | File inventory, G-01–G-13/G-15 results, Q1–Q7 review and runtime receipts; Q8 follows the current owner scope | Missing execution is `BLOCKED`; future model trials are `N/A`; a confirmed defect is `FAIL` |
| Before frontier rollout | Run `delivery-format`, `format-audit`, `rollout-check`, and bind Q1–Q7/difficulty to the frozen task hash and review timestamp | `evidence/delivery-contract/latest.json`, format audit, pre-rollout review, native build/runtime evidence | No selected format or stale identity blocks rollout |
| After trajectories, before packaging | Reconcile every raw scored submission, unscored event, archive/report hash, checkpoint, best score, best-valid score and final score; build a clean allowlist staging tree | Format-specific package manifest, score ledger, privacy scan, per-file SHA-256 | Missing or rewritten evidence stays incomplete |
| Final QC | Run `rl-task-qc` on the extracted package or ZIP with the same format choice | External JSON/static receipt outside customer bytes plus runtime/native evidence | Static PASS is only the static gate; it does not claim platform acceptance |

The reproducible report entry point for the first stage is:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  pre-rollout-qc WORKSPACE --output PRIVATE_REVIEW_DIR/pre-rollout-process-qc.md
```

It is deliberately read-only with respect to the task workspace. A report with
`BLOCKED` rows is a process record, not a rollout approval.

For the current owner scope, source/rights/dedup, SForge dual entry and Q8 are
recorded as `not_required_by_owner` where applicable. They must never be written
as measured passes. If a later task-specific instruction restores one, it must
be recorded in the task-bound contract before scoring.

## Final command

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  rl-task-qc FINAL_PACKAGE_OR_DIR --format sforge-only \
  --profile PROFILE.json \
  --json PRIVATE_REVIEW_DIR/rl-task-qc.json
```

Replace `sforge-only` with `harbor` for a Harbor package. Keep the QC output
outside customer bytes and rerun it after every package or document edit.
