# EdgeBench Task Production Skill Bundle

This directory is a portable OpenCode task-authoring Skill bundle. The bundle
contains the interactive Skill, the profile-driven production line, the vendor
source documents, and the `rl-task-qc` final static gate.

## Install in a project

Copy the bundle's `.opencode/skills/edgebench-task-production/SKILL.md` to the
project's `.opencode/skills/edgebench-task-production/SKILL.md`, or copy the
entire bundle root into a project and use the included relative layout. Start a
new OpenCode session after installation so the Skill registry reloads it.

## Run from this bundle root

All commands below use paths relative to the bundle root:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py doctor
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE --profile edgebench-v1 \
  --requirements-doc vendor-docs/EdgeBench-Vendor-Dataset-Requirements.md
```

For a compatible Benchmark, provide a validated custom profile JSON and the
user's requirements document with `--profile-file` and `--requirements-doc`.
The receipt stores both hashes. The current adapter rejects unsupported reward,
artifact and architecture shapes before task authoring.

## Interactive session workflow

Use the Skill in the current OpenCode session. The session finds and verifies a
Seed before authoring a task:

```text
requirements → profile → source scout → pinned mirror
→ feasibility/learning-loop screen → Seed promotion
→ task request → design → generate → validate → measure
→ pre-rollout QC → profile/format freeze → authorized rollout
→ trajectory collection → selected-format package → rl-task-qc
```

## Hierarchical production with OpenCode or headless Orca

The bundle also supports a supervisor/employee mode. One producer employee
owns one task from Seed through pre-rollout; an independent QC employee runs
QC-1 and QC-2; the producer executes the approved rollout; and a deterministic
controller reports profile-bound checkpoints to the supervisor. The supervisor
can archive a failed producer tree and hire a replacement with a new Seed.

This does not require a desktop UI. Use OpenCode's native Task dispatch, or
run Orca headlessly and consume the same JSON dispatch/receipt files:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-status CAMPAIGN

# Optional headless Orca runtime on the worker host:
orca serve --port 6768 --pairing-address SERVER_HOSTNAME
orca status --json
orca skills install --skill orca-cli --skill orchestration
```

The full authority, QC, replacement, checkpoint and `orca orchestration`
mapping is in `edgebench-task-generator/references/supervisor-protocol.md`.

The intended owner interaction is chat-first: say “派发 10 个出题下属” in
OpenCode or Orca. The supervisor Agent runs the ledger and dispatch commands
internally, starts OpenCode Tasks or Orca supervised workers, and reports only
the resulting job summary and approval questions. The CLI commands are the
automation/recovery interface, not a requirement for the owner to copy and run.

Use `rehearsal` mode for production-line tests. Rehearsal artifacts are private
and never count as Seed promotion, formal task delivery, model rollout or
customer acceptance.

## Included material

- `edgebench-task-generator/`: production Skill, scripts, profiles, source
  registry, seed guidance and examples.
- `vendor-docs/`: the EdgeBench/Meta/phase QC documents used by the default
  profile.
- `rl-task-qc/`: static package gate and runtime/checklist references.

All paths in this bundle are relative to this directory. Secrets, `.env`
files, historical workspaces, live run logs, model trajectories and customer
packages are intentionally excluded.
