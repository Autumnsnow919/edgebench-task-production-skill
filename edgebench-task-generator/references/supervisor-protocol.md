# Hierarchical supervisor protocol

This protocol turns the production line into a supervised organization without
coupling it to a desktop application. The supervisor may be the OpenCode main
session, a headless Orca runtime, or an adapter that consumes the same JSON
dispatch and receipt files.

## Authority model

```text
owner/user
   ↕ escalation and policy decisions
supervisor (OpenCode main or Orca coordinator)
   ├── producer employee: exactly one task, full pre-rollout lifecycle
   │    └── child OpenCode Tasks requested through the supervisor
   └── independent QC employee(s): read-only review and repair findings
```

The producer can perform the complete task workflow:

```text
requirements → profile → Seed → design → generate → validate → measure
→ pre-rollout QC → repair → QC-2 → freeze → rollout execution
```

The producer cannot approve its own Seed, QC, freeze, exception, replacement,
or delivery. A QC employee cannot be the producer or a child of that producer.
The supervisor owns state transitions and receives only summaries and alerts by
default; raw logs remain in the task's private evidence tree.

## Employee ledger

Initialize the ledger after `campaign-init`:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100
```

The ledger records `employee_id`, `parent_id`, role, transport, one-task
assignment, exclusive `job_root`, prompt hash, employment state, reports and
termination evidence under `CAMPAIGN/supervision/`. It never deletes a fired
employee's work tree or evidence.

Supported transport modes are:

- `opencode-task`: the current OpenCode main session dispatches the named
  native Task or an equivalent `opencode run` process;
- `orca-cli`: a headless Orca coordinator maps the dispatch JSON to
  `orca orchestration worker-start` and consumes worker messages;
- `dual`: either transport may be selected per employee.

The Skill does not assume that a child OpenCode Task can recursively create
another Task. A producer writes a `child-request`; the supervisor validates and
dispatches it, so the same protocol works when nested Task creation is not
available to a worker.

## Producer and child work

Hire one producer per task:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-hire CAMPAIGN --employee-id producer-001 \
  --role producer --transport opencode-task \
  --seed-id systems-software-engineering/cpython/lead-001 \
  --prompt-file WORK_ORDER.md
```

The producer may request a child, but does not create it directly:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  child-request CAMPAIGN --parent-id producer-001 \
  --child-id producer-001-seed-01 --role producer \
  --transport opencode-task --objective "Pin and verify the Seed source"
```

The supervisor then dispatches an employee through the emitted JSON contract:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-dispatch CAMPAIGN --employee-id producer-001
```

Every dispatch is bound to a prompt hash, contract hash and exclusive job root.
Retries use a new dispatch attempt and never reuse a fired employee's worktree.

## QC loop

The normal path has at most two independent QC rounds:

```text
producer complete → QC-1 → repair by producer → QC-2 → rollout approval
```

QC checks the executable task, NOP/reference behavior, error handling, score
ladder, snapshot identity, isolation, reproducibility and the selected
Benchmark requirements. A QC failure is not a model-rollout failure.

If QC-2 still finds a problem, the supervisor records severity and asks the
owner when the issue is not an automatic termination. The supervisor may record
an explicit exception or fire the producer; it must never silently send an
unverified repair to rollout.

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  qc-decision CAMPAIGN --producer-id producer-001 --qc-id qc-001 \
  --round 1 --decision repair_required --severity major \
  --report QC_REPORT.json --note "Repair the invalid partial-artifact path."
```

## Rollout execution and monitoring

The producer may start the rollout, but it cannot change the frozen task. The
deterministic controller records the exact process, frozen task hash, model,
timestamps, valid submissions, scores, errors and checkpoint observations. The
producer's prose is never the source of truth.

For a running SForge trial, `monitor-tick` can produce observations. The
supervisor evaluates them against the selected profile:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-rollout-gate CAMPAIGN --employee-id producer-001 \
  --observations OBSERVATION.json \
  --profile-file edgebench-task-generator/references/profiles/edgebench-v1.json
```

The profile, not this document, defines the checkpoint times, metric, early
upper bound, growth rule and full-budget rule. The EdgeBench example observes
30 minutes and rejects a task at the one-hour checkpoint when `best_score >=
80`; the value is a profile example and must be reconciled with the active
requirements document before formal use.

The gate distinguishes:

- `CONTINUE`: no gate has fired;
- `EARLY_TOO_EASY`: an early upper bound was reached; fire the producer tree,
  archive it, and create a replacement with a new Seed;
- `INFRA_FAILURE`: provider, quota, container, judge or observation failure;
  retain the producer and retry the unchanged frozen task;
- `FINAL_POLICY_REVIEW` / `FINAL_POLICY_FAIL`: apply the selected Benchmark's
  final rule, which may be binary reward, score growth or a requirements-owned
  rule;
- `ACCEPT`: the profile growth rule passed.

The gate does not kill processes. A supervisor may stop only exact processes
owned by the corresponding dispatch and must preserve their logs. Never use a
broad `pkill` pattern.

## Replacement

Early difficulty failure terminates the producer and its complete child tree.
The old Seed and evidence remain read-only; the new producer receives a new
Seed and new identity:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-fire CAMPAIGN --employee-id producer-001 \
  --reason "EARLY_TOO_EASY at the 1h profile checkpoint" \
  --evidence rollout-gate.json

python3 edgebench-task-generator/scripts/topify_pipeline.py \
  replacement-order CAMPAIGN --former-employee-id producer-001 \
  --new-employee-id producer-002 \
  --new-seed-id combinatorial-optimization/minizinc/lead-017 \
  --transport orca-cli --prompt-file NEW_WORK_ORDER.md
```

Infrastructure failure is explicitly different: it creates a new run under the
same frozen subject and does not fire the producer.

## Headless Orca

Orca is optional. On a Linux server without a desktop window, its runtime can
be started with:

```bash
orca serve --port 6768 --pairing-address SERVER_HOSTNAME
orca status --json
orca skills install --skill orca-cli --skill orchestration
```

The supervisor uses the JSON dispatch produced by this Skill and maps it to
Orca's `run-create`, `task-create`, `worker-start`, `check`, `send`, and
`worker-stop` commands. A missing Orca executable is an unavailable transport,
not evidence that a task or worker failed.
