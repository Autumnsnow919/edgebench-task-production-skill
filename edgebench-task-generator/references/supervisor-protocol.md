# Hierarchical supervisor protocol

This protocol turns the production line into a supervised organization without
coupling it to a desktop application. The supervisor may be the OpenCode main
session, a headless Orca runtime, or an adapter that consumes the same JSON
dispatch and receipt files.

The owner-facing interface is chat-first. The owner asks the supervisor to
start, scale, pause or replace workers; the supervisor invokes the ledger and
transport commands internally. The shell commands shown below are the
deterministic implementation interface and recovery interface, not a request
for the owner to copy them manually.

To inspect the team from chat, the supervisor runs the dashboard internally:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-dashboard CAMPAIGN
```

The result is a compact view of every employee's state, parent, task/Seed,
last dispatch, last heartbeat/report, QC decision, rollout gate and alert. A
single employee can be expanded with `--employee-id`. In OpenCode, the parent
session can additionally inspect the selected native Task's result. In Orca,
the supervisor maps the selected dispatch to `worker-show`/`worker-read` and
waits on `orchestration check`; the owner does not need to open a terminal pane.

Web owner input for the supervisor is stored as an immutable ledger receipt
and submitted to the *bound actual session's* native prompt inbox with resume.
The Web displays that session's real assistant replies and tools. Admission is
not acknowledgment or execution. An interrupted supervisor stays visibly
interrupted until an explicit owner message/review request resumes it. Reading
the dashboard or creating a ledger object never wakes an agent. Employee
ledger inboxes remain a separate durable channel; worker replies need receipts.

For a true owner gate, the supervisor calls the native `question` TOOL and
waits. Web reply validates the form's tool message/ID and running tool state,
then submits the answer to that tool. The same agent resumes, uses the answer
for the scoped next action and records actual evidence. Web does not map a
generic acceptance into rollout authorization. API-created detached forms
and old `owner_form: pending` ledger records cannot authorize production.
The `owner-form-create` compatibility command only sends a review request to
the supervisor; `owner-form-sync` refuses detached legacy forms. Routine Seed,
helper and QC-1 handling is the supervisor's responsibility. No automatic
decision, waiver or rollout is inferred from a review request.

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

With multiple major supervisors, each Benchmark is a separate campaign scope:
its campaign root, employee ledger, task workspaces, QC evidence and supervisor
token are not shared with another Benchmark. The owner Web token may aggregate
the scopes; a supervisor token is restricted to one registry entry. The only
shared filesystem is the dedup index, which contains content fingerprints and
task hashes, never source trees or worker logs.

## Employee ledger

Initialize the ledger after `campaign-init`:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100 \
  --dedup-root /var/lib/edgebench/dedup-global
```

The ledger records `employee_id`, `parent_id`, role, transport, one-task
assignment, exclusive `job_root`, prompt hash, employment state, reports and
termination evidence under `CAMPAIGN/supervision/`. It never deletes a fired
employee's work tree or evidence.

Supported transport modes are:

- `opencode-task`: the current OpenCode main session dispatches the named
  native Task or an equivalent `opencode run` process. The bundle includes
  `edgebench-supervisor`, `edgebench-producer`, `edgebench-qc`,
  `edgebench-seed-worker`, `edgebench-prototype-worker` and
  `edgebench-rollout` role agents when the full `.opencode/` directory is
  installed;
- `orca-cli`: a headless Orca coordinator maps the dispatch JSON to
  `orca orchestration worker-start` and consumes worker messages;
- `dual`: either transport may be selected per employee.

The Skill does not assume that a child OpenCode Task can recursively create
another Task. A producer writes a `child-request`; the supervisor validates and
dispatches it, so the same protocol works when nested Task creation is not
available to a worker.

## Producer and child work

Hire one producer per task before creating standalone scout workers. The Seed is
intentionally empty at hiring time; the producer owns discovery:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-hire CAMPAIGN --employee-id producer-001 \
  --role producer --transport opencode-task \
  --prompt-file WORK_ORDER.md
```

Strict campaigns reserve the Seed fingerprint before the producer is
dispatchable. The shared dedup root uses an exclusive lock, so two supervisors
cannot reserve the same source/objective/verifier signature concurrently.
After freeze, the task SHA-256 is registered in the same global index. A
collision blocks the later producer; it does not silently become a second
variant of the same question.

The producer may request a child, but does not create it directly:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  child-request CAMPAIGN --parent-id producer-001 \
  --child-id producer-001-seed-01 --role seed-worker \
  --transport opencode-task --objective "Pin and verify the Seed source"
```

The supervisor then dispatches an employee through the emitted JSON contract:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-dispatch CAMPAIGN --employee-id producer-001
```

For a batch, the producer-first helper plans or prepares employees without
claiming that their OpenCode/Orca sessions have started:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  producer-plan CAMPAIGN --count 5 --transport opencode-task
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  producer-dispatch CAMPAIGN --count 5 --transport opencode-task
```

The main chat supervisor must bind each prepared dispatch to an actual
OpenCode session (`employee-session-bind`) and then monitor heartbeats. The
legacy `production-dispatch` command is not the default producer path.

Every dispatch is bound to a prompt hash, contract hash and exclusive job root.
Retries use a new dispatch attempt and never reuse a fired employee's worktree.

## QC loop

The normal path has at most two independent QC rounds:

```text
r0 complete → supervisor “too simple” challenge → r1
→ supervisor “still too simple” challenge → r2
→ QC-1 → repair by producer → QC-2 → rollout approval
```

The supervisor must issue two hardening challenges after the producer's first
completion. Each challenge is recorded with its round, observed weakness and a
different causal mechanism requirement. r0 and r1 cannot be approved by QC or
sent to rollout. A producer that cannot provide an honest mechanism is blocked,
not padded with records, sleep, noise or a moved reward bound.

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  hardening-challenge CAMPAIGN --producer-id producer-001 --round 1 \
  --note "Too simple: add a source-grounded dependency coupling admission with downstream capacity."
```

QC checks the executable task, NOP/reference behavior, error handling, score
ladder, snapshot identity, isolation, reproducibility and the selected
Benchmark requirements. A QC failure is not a model-rollout failure.

QC-1 non-pass requires repair. If repaired QC-2 still has an unmet item,
retain the employee and its tree and require the owner's review regardless of
severity. Two failed QC rounds are **not automatic termination**. Present the
exact failed/incomplete checks, requirements, evidence and impact; show which
first-round findings the repair closed. Reviewer recommendations outside the
agreed rubric must be identified as recommendations, not silently added gates.
The owner chooses scoped exception (reason optional), more repair/review, or
explicit termination. `qc-resolve` binds this choice to the current QC decision
and report hashes. Generic `employee-fire` refuses unresolved QC failures.
Default automatic QC is capped at two rounds; `continue_repair` authorizes only
the next numbered review. No resetting to QC-1 or relabeling a third review as
QC-2. Further non-pass results return to the owner. Preserve original findings
and scores; accepting an exception does not start rollout, change a Judge
result, or claim missing ARM/build/privacy evidence passed.

QC owner decisions require an actual native question answer, not a supervisor
note asserting approval. In the question text include the producer ID and the
full current `qc_decision_sha256`; use option labels `接受指定例外`,
`继续返修复检`, `淘汰员工子树`. This binds the answer to the affected subject,
not a generic “accept everything”. Web stores the verified tool reply under
`supervision/native-interactions/.../reply.json` with its scoped QC identity.
For a TUI/native chat answer, `qc-owner-answer` inspects the real completed
question tool in the bound session; an error/cancelled question is not an answer.
Then `qc-resolve --evidence <reply.json>` validates the subject and chosen
operation. An unrelated task's answer, a different choice or a changed QC
report cannot be reused. Acceptance and continued-repair notes are optional.

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  qc-owner-answer CAMPAIGN --producer-id producer-001 --decision continue_repair
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  qc-resolve CAMPAIGN --producer-id producer-001 --decision continue_repair \
  --evidence ACTUAL_NATIVE_REPLY_JSON
```

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

In an Orca chat, the normal interaction is therefore:

```text
owner:  "派发 10 个出题下属，先做 systems/software"
agent:  checks Orca → creates/reuses a Run → creates Tasks
        → starts supervised workers → waits for worker_done/escalation/question
        → updates the Skill ledger → reports the batch summary
```

In an OpenCode chat the same request causes the supervisor to call the internal
ledger commands and dispatch native Tasks in the current session. It must not
respond with only a list of commands. If a required policy or resource limit is
missing, it asks one focused question; if the runtime is unavailable, it reports
the exact infrastructure blocker and does not mark the producer failed.
