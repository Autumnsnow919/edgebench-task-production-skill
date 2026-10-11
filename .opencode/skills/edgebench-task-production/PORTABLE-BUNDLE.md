# EdgeBench Task Production Skill Bundle

This directory is a portable OpenCode task-authoring Skill bundle. The bundle
contains the interactive Skill, the profile-driven production line, the vendor
source documents, and the `rl-task-qc` final static gate.

## Install in a project

Copy the bundle's `.opencode/skills/edgebench-task-production/SKILL.md` to the
project's `.opencode/skills/edgebench-task-production/SKILL.md`, or copy the
entire bundle root into a project and use the included relative layout. For
chat-first hierarchical dispatch, copy the entire `.opencode/` directory so
the role agents under `.opencode/agents/` are also available. Start a new
OpenCode session after installation so the Skill and Agent registry reloads.

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

For hierarchical production, initialize the transport-neutral supervisor ledger:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-status CAMPAIGN
```

The same ledger can be dispatched through OpenCode Tasks or a headless Orca
runtime. See `edgebench-task-generator/references/supervisor-protocol.md` for
employee identity, independent QC, profile-bound rollout checkpoints and
replacement rules.

In normal use, the owner asks the supervisor in chat to dispatch workers. The
supervisor runs these commands internally and invokes native OpenCode Tasks or
Orca's supervised workers; the owner does not need to copy the CLI commands.
Say “汇报所有下属” or “查看 producer-001” to receive the dashboard and a
focused worker summary.

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
