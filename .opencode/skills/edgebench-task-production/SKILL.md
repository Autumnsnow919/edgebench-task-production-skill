---
name: edgebench-task-production
description: Use in an OpenCode session to author, repair, audit or deliver EdgeBench and compatible Benchmark tasks through profile-driven production, selected Harbor/SForge formats, rollout evidence and final QC.
---

# EdgeBench task production

Read `edgebench-task-generator/SKILL.md` for current production. The user
selects **SForge-only** or **Harbor** delivery; SForge is the rollout harness
for both. Select the applicable Benchmark profile first; EdgeBench thresholds
are defaults for `edgebench-v1`, not universal limits for compatible
Benchmarks. Mandatory documents: `Meta Vendor Data Spec V3.0.zip` and
the current V1 portion of `EdgeBench Vendor Dataset Requirements.md`.
Before authoring or measuring a task, also read
`edgebench-task-generator/references/authoring-lessons-2026-10-05.md`; before
repairing one, read `edgebench-task-generator/references/repair-evidence-and-reply.md`.
For hierarchical production, read
`edgebench-task-generator/references/supervisor-protocol.md`. The supervisor
may be the OpenCode main session or a headless Orca CLI coordinator; do not
assume that a desktop UI exists.
`edgebench-task-generator/references/vendor-v3-applicability.md` records the
scope and unresolved external delivery questions. Their Harbor-format clauses
apply when Harbor is selected; they are not evidence that an SForge ZIP passed
Harbor preflight.
Read `edgebench-task-generator/references/current-owner-scope.md` for the
default scope and
`edgebench-task-generator/references/five-task-qc-lessons-2026-10-01.md`
for prior samples' non-format failures. The older QC report's V2.4, Harbor,
platform upload and Oracle-user rules do not silently override the selected
format's current Meta V3.0 and EdgeBench V1 mapping.
For a **repair**, external QC finding, upload preflight error or stalled
rollout, first read `edgebench-task-generator/references/repair-evidence-and-reply.md`.
Apply the owner's latest task- or batch-specific scope before auditing; do not
promote a sample's exception to every EdgeBench task. **Every verified repair
requires its own point-by-point `修复逐条回复` Markdown** outside customer task
inputs/ZIP, with evidence, executed checks, remaining limits and the final
artifact SHA-256. An edit alone is not a completed fix.

## Interactive OpenCode authoring mode

The current project work may run in **Skill rehearsal mode**. A rehearsal tests
the production line with isolated Seed cards, pinned source bytes, synthetic or
micro-prototype scorers and gate receipts. Rehearsal outputs are private
process evidence only: they do not promote a Seed, create a formal customer
task, authorize rollout, or qualify as delivery evidence. Use a separate
an isolated temporary or private review workspace and label every result
`rehearsal`, `prototype-only`, `blocked`, or `incomplete` as applicable.

In hierarchical mode, the supervisor carries the campaign request through the
production stages while a producer employee owns exactly one task. The producer
may complete the full workflow and request child OpenCode Tasks, but the
supervisor alone approves state transitions, QC exceptions, freezes,
replacement and delivery. A separate QC employee must review the producer's
task. In single-agent mode the current OpenCode session may still own the task
directly; it must not treat an old sample package as a new task.

QC-1 failure goes to producer repair. If repaired QC-2 still does not pass,
hold the employee and evidence for the owner's review; no automatic `fired`
because two QC rounds failed. Show specific findings, requirements, evidence,
closed repairs, severity and impact, distinguishing failed from unverified
checks and discretionary reviewer demands. The owner may accept a scoped
exception without a reason, approve further repair/review, or explicitly fire.
Retain original QC findings and scores even when an exception is accepted.
The resolution requires the owner's real native-question answer receipt bound
to the current QC hash; a supervisor note is not owner authorization. See
`edgebench-task-generator/references/supervisor-protocol.md` for Web/TUI mapping.

## Chat-first dispatch

When the owner says “派发出题下属”, “启动一批题目”, “create producers”, or
an equivalent request in the current chat, do not merely print CLI commands for
the owner to copy. The supervisor must execute the internal ledger commands,
then dispatch the returned jobs through the active transport:

- In OpenCode, use the native Task tool in the current session. Give each Task
  its immutable prompt, employee ID, dispatch ID and exclusive `job_root`, then
  collect its report before advancing the campaign.
- In Orca, use the headless Orca CLI from the chat agent: check
  `orca status --json`, create or reuse the Run, create Tasks, start supervised
  workers, and consume `worker_done`, `escalation` and `question` messages.

The owner should see a concise dispatch summary, not a request to run
`employee-hire` or `supervisor-dispatch` manually. Ask the owner only for a
missing policy, resource approval, or a human decision gate. If Orca is not
running, report the infrastructure blocker and the exact `orca serve` remedy;
do not classify it as a producer or task failure. Read
`edgebench-task-generator/references/supervisor-protocol.md` for the command
mapping and receipt requirements.

When the owner asks to “看下属”, “汇报进度”, “show the team”, or names one
employee, run `supervisor-dashboard` internally. Present status, last report,
QC/rollout checkpoint and alerts in chat. Do not expose raw credentials or dump
all worker logs by default; offer a focused worker read only when needed.

When multiple Benchmarks or major supervisors are registered, keep their
campaign roots, employee trees, tokens and evidence scopes separate. A Web
selector may aggregate summaries for the owner, but a supervisor token can
only address its own Benchmark. Every employee and child Task still receives
an exclusive job root; never use a shared worktree as a shortcut.

For production discovery, scan only owner-approved roots. A campaign enters
the Web board only when `supervision/supervisor-stamp.json` validates against
its campaign and supervisor manifest. Do not infer a supervisor from an
ordinary directory name or an unverified agent process.

Every producer must pass the mandatory two-round difficulty challenge before
independent QC. When r0 is reported, the supervisor must explicitly tell the
producer that the first version is too simple for the target and require one
new, source-grounded causal mechanism. After r1, it must issue the same
challenge for a second distinct mechanism. Only r2 may enter QC, freeze or
rollout. This is a structured hardening policy, not permission to falsify a
model score; each challenge must name the observed weakness, mechanism and
evidence. Cosmetic volume, delay, noise or reward-ceiling changes do not count.

When the owner asks for a Web control panel, read
`edgebench-task-generator/references/supervisor-web.md` and start the
`supervisor-web` server through the shell. Use `--no-auth` only when the owner
explicitly requests trusted private-network access; `0.0.0.0` is allowed for a
trusted network, not a public unauthenticated service. The supervisor must also read pending owner commands under
`CAMPAIGN/supervision/commands/` and report their outcome; a queued Web command
is not yet an executed agent action.

At the start of a new authoring request, collect or locate:

- the user's Benchmark requirements document;
- Benchmark name, task count and capability/domain target;
- the desired delivery format, if the document does not decide it;
- budget, resource, architecture, reward and rollout expectations;
- source/dataset restrictions and any explicit exclusions.

Read the requirements document before choosing a default. If a requirement is
ambiguous or two documents conflict, present the conflict and ask for the
decision in the current session. Do not silently apply EdgeBench V1 thresholds
to another Benchmark. If no custom profile exists, derive one from the user
document in the private review area, show its threshold mapping in the session,
and retain the source document hash. Bind the profile to the generated task
workspace before validation and measurement:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE \
  --profile PROFILE_ID \
  --profile-file PROFILE_JSON \
  --requirements-doc USER_REQUIREMENTS.md
```

The user supplies the requirements; the Skill supplies and verifies the Seed.
Read `edgebench-task-generator/references/seed-sourcing.md`, the source
registry and the selected domain playbook. Search official registries or
approved public sources, preserve the original lead, pin the selected revision
and input bytes, and run the bounded feasibility/learning-loop screen before
promoting a Seed. Do not invent a Seed from a task title or jump directly to a
hand-authored task.

The session may use these production tools while keeping the main review in
the current OpenCode conversation:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  production-plan CAMPAIGN
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  scout-source WORKSPACE --registry REGISTRY.json \
  --source-id SOURCE_ID --capture CAPTURE.json
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  mirror-source WORKSPACE --source-id SOURCE_ID \
  --url PINNED_URL --revision REVISION --expected-sha256 SHA256
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  seed-screen CAMPAIGN --seed-id CATEGORY/SOURCE/LEAD \
  --for-prototype --owner-sample
```

Only a receipt-backed, technically screened Seed enters `SEED_PROMOTED`; a
feasibility probe or author proposal alone does not authorize task generation.

Create the task request and workspace after Seed promotion, bind the profile
intent before design, then bind it again to the generated task bytes. For a
direct single-task authoring session, use this sequence in the same
OpenCode session:

```text
requirements intake → profile intent → source scout → source mirror
→ feasibility/learning-loop screen → Seed promotion
→ task request → workspace init → task profile intent
→ design screen → generate → task-bound profile receipt → validate → measure
→ pre-rollout-qc
→ main Q review/difficulty → format selection → preflight receipts → freeze
→ rollout only if authorized
→ collect → selected-format audit → package → rl-task-qc
```

The session may write the task workspace and private evidence under the
workspace root. It must keep customer output and private review output in their
separate configured trees. The authoring session may stop at any evidence gate
with a truthful `BLOCKED` or `incomplete` result; it must not lower a profile
threshold by editing a score, a report or a task after observing model results.

## Canonical freeze and rerun policy

Use the production state sequence below for every task family. A state advances
only from task-bound evidence; a directory, checklist, or author statement does
not advance it:

```text
REQUIREMENTS_LOCKED
→ SEED_PROMOTED
→ D0_EXECUTABLE_PROTOTYPE
→ D1_STRUCTURAL_ESCALATION
→ D2_STRUCTURAL_ESCALATION
→ DESIGN_ACCEPTED
→ BOTH_FORMS_BUILT
→ PREFLIGHT_PASSED
→ FROZEN
→ E1_SCREENED
→ E2_COMPLETE
→ CURVE_ACCEPTED
→ DELIVERY_ACCEPTED
```

Before spending model time, the session must have the active profile, selected
format, current task hash, current Work/Judge or Harbor build identities, and
all profile-required preflight gate receipts. Run:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py freeze WORKSPACE
```

The freeze receipt is the rollout subject token. After `FROZEN`, task-visible
bytes, scorer/Judge code, hidden cases, reward constants, images, dependencies,
resources, network policy, architecture scoring semantics and model-visible
starter/input are immutable. The rollout stage may append only run and
trajectory evidence.

Use the following rerun rules:

- Packaging, README, report, archive index, path, permission or trajectory
  metadata repairs keep the frozen subject and do not rerun the model.
- Provider failure, missing checkpoint, quota interruption or an incomplete
  run gets a new valid run under the unchanged frozen subject; it does not
  require a new task identity.
- A change to instruction, starter/input, artifact schema, scorer, Judge,
  hidden cases, reward formula/constants, Work/Judge image or dependencies,
  resource/network/capability policy, or architecture scoring semantics creates
  a new task identity and invalidates downstream rollout evidence.

When a profile requests a reward, artifact shape or architecture unsupported by
the current adapter, stop before authoring and ask for a compatible adapter or
an explicit user decision. Do not discover adapter incompatibility after a
rollout.

For a batch request, use the employee/supervisor path as the default. Hire and
dispatch one `edgebench-producer` per target task **before** creating any
standalone scout pool. Each producer owns the entire task lifecycle and begins
Seed discovery itself inside its exclusive job_root; it may request
`edgebench-seed-worker` or `edgebench-prototype-worker` children through the
supervisor. A standalone scout/verifier/author pool is legacy compatibility
behavior and may run only when the owner explicitly asks for source scouting or
legacy campaign dispatch. Never count a scout as a producer or a source lead as
a completed task.

The producer-first loop is:

```text
hire producer → dispatch producer → producer finds Seed
→ producer requests bounded children when needed
→ producer r0/r1/r2 → independent QC → freeze → rollout
```

Every producer gets a new exclusive job_root; retries never reuse fired trees.
The main supervisor reviews receipts, but does not take over the producer's
task. `production-dispatch`/`production-tick` remain explicit legacy or
unattended recovery tools, not the default interaction for a batch request.

For the employee/supervisor mode, initialize the ledger and use the
transport-neutral commands in
`edgebench-task-generator/references/supervisor-protocol.md`:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-status CAMPAIGN
```

`opencode-task` dispatch is executed by the current OpenCode session. `orca-cli`
dispatch is consumed by a headless `orca serve` runtime. Both paths write the
same employee, QC, rollout-gate and replacement receipts. A producer's rollout
is observed by the deterministic monitor; provider/model/container failures
retry the same frozen task, while a profile-defined early score upper bound
fires the producer tree and requires a new Seed.

Source/dedup/redistribution and SForge dual-entry checks are owner-scoped
items unless the current delivery contract restores them; they are not
measured passes when excluded. Pin actual task inputs for reproducibility and
screen through `seed-screen --for-prototype --owner-sample`. The task family
must retain measured r0→r1→r2 parent-linked causal revisions, generic controls
and main review. Read
`edgebench-task-generator/references/hardening-family-contract.md` for revisions;
no decorative volume, delay or moved reward ceiling can replace a mechanism.

Main review is Q1–Q7 plus difficulty, separate from Judge reward. Q8 review is
removed, but vendor Work/verifier no-network operation remains independently
checked. ARM64 runs applicable build, NOP/reference, snapshot-repeat and
cross-architecture checks; it does not repeat model rollout or Q1–Q7 review.
No source copies, host architecture probes or fixtures prove a runtime pass.
For this owner's ARM64 validation, the authorized native host is
`ssh root@10.2.39.3` (confirmed 2026-10-13). Use an exclusive remote validation
directory and the host's configured SForge executable; run actual task-bound
ARM64 checks and keep their receipts. Successful SSH login does not certify a
build or score. This address is internal Skill guidance only, never customer
task/README/QC/archive content. Do not embed SSH credentials or alter unrelated
remote services.
Online builds of the delivered Work/Judge source contexts are allowed; no
offline OCI or wheelhouse package is required. Pin build inputs and retain
rebuild receipts. This does not waive the executed no-network runtime boundary.

Before model hours read
`edgebench-task-generator/references/pre-rollout-quality-gates.md` and
`edgebench-task-generator/references/sforge-rollout-operations.md`. Preserve
comparable full-budget SForge trial reports/archives and the original review timestamp. Internal
Harbor scores are not substitutes for the actual scored SForge contexts.
Test the documented editing and local-feedback path under the actual scored
UID, image and controller policy. A provider health probe does not guarantee
a full-budget run; read real PIDs and checkpoints, and verify the original
owned Work container exists before trying to resume. Null-score events are
not zeros, and an interrupted run is not a completed full-budget trial.
Record `delivery-format WORKSPACE --format sforge-only|harbor` before rollout.
For `sforge-only`, use `sforge-audit WORKSPACE` and
`package-samples --manifest ... --output CUSTOMER_OUTPUT_DIR/NEW_ZIP
--review-dir PRIVATE_NEW_REVIEW_DIR`. For `harbor`, use `harbor-audit` and
`export-harbor --manifest ... --output CUSTOMER_OUTPUT_DIR/NEW_DIR`.
The unified `package-selected` command is preferred when the manifest carries
`delivery_format`. The existing customer output directory and new private
review directory must be in separate directory trees. Deliver the selected
format's English task package and separate English QC report; Chinese
README/QC belong solely to the private owner-review directory. Scan all shipped bytes,
including compressed submissions, for credentials, internal host/provider
details and other-customer/private data. Diagnostic `--draft` and owner-scoped incomplete review
samples are not formal acceptance. After either format is packaged, run
`rl-task-qc` with the same selected format and retain its report outside the
customer bytes.
After the **last** package/document edit, compare package members with the
extracted tree (including the actual hash manifest, currently
`metadata/sha256.json`), verify links from each
document's actual location, best score versus best-**valid** score, private
data and final ZIP SHA-256. Update the separate repair reply with that hash.
The active installation's `delivery-policy.json` fixes the English customer
output root and private review root. No worker writes to either; only the main
reviewer packages after formal audit.

Customer text follows `README文档设计指引对外.md` only where its claims match
the task/cohort. Do not invent upper/lower model roles, Steps, Agent wall time
or a 100-point task-quality total after Q8 removal. README stands alone without
referencing the external QC report. Ask the owner before choosing Smoldata/S3,
creating listings/datasets, uploading or finalizing; local export does not
claim recipient acceptance. Leave historical samples and live services intact.
The five-task QC retrospective requires the unmodified starter/NOP baseline,
actual model-snapshot replay determinism (five reference repeats alone do not
prove it), cross-task semantic dedup, complete scored-report/best-archive
reconciliation, and final extracted-package privacy scan. Keep missing real
process receipts incomplete; customer copy never lists internal waiver files.
