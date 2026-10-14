---
name: edgebench-task-generator
description: Use when sourcing, authoring, repairing, reviewing or delivering long-horizon EdgeBench tasks, SForge rollouts, QC reports or Morrow Lab customer documents.
---

# EdgeBench Task Generator

## Current contract and scope

This Skill is consumed by the active OpenCode session as an interactive
Benchmark authoring workflow. The session is responsible for reading the
user's requirements document, selecting or creating the applicable profile,
creating the task workspace, authoring the task, running the stage checks and
reporting the next evidence gate. The scripts below are session tools; a
successful script invocation does not itself mean that a task or delivery is
complete.

## Hierarchical supervisor mode

The production Skill supports native OpenCode Task dispatch and headless Orca
CLI dispatch. The top Agent is a supervisor, not a task author. It hires one
producer employee per task; that producer owns the complete Seed-to-pre-rollout
workflow and may request child Tasks through the supervisor. An independent QC
employee performs QC-1 and, after a repair, QC-2. QC-1 failure requests repair.
If repaired QC-2 is still not passing, retain the producer and its evidence and
require the owner's review, regardless of severity; never automatically fire
it for failing two QC rounds. The supervisor presents the exact unmet items,
closed repairs, requirement/evidence, severity and impact, distinguishing actual
failures from incomplete verification or extra reviewer expectations. The owner
may accept a scoped exception without a reason, authorize another repair/review,
or explicitly terminate. A waiver is not a measured pass or rollout permission.
Bind that owner choice to the current QC ID/hash using the native-question
receipt and `qc-resolve --evidence`; a supervisor's own note cannot approve it.
Read `references/supervisor-protocol.md` for the Web/TUI answer receipt mapping.

Initialize the transport-neutral ledger with:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-init CAMPAIGN --mode dual --max-concurrent 100
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  employee-status CAMPAIGN
```

The campaign's `supervision.max_concurrent` is admission control, not a promise
that the host can run that many model processes. The supervisor must apply
resource and provider limits. Fired employees and their child trees are
archived, never deleted. A replacement gets a new employee identity and a new
Seed. See `references/supervisor-protocol.md` for the complete contract.

This is a **chat-first** workflow. When the owner asks the supervisor to start
or scale production, the active Agent executes the ledger commands and dispatch
calls itself; it must not return a copy-paste command list as the result. In an
OpenCode chat, dispatch native Tasks in the current session. In an Orca chat,
use `orca orchestration run-create`, `task-create`, `worker-start` and
`check`/`send` through the CLI. Return only the batch summary, job IDs,
checkpoint alerts and questions requiring owner approval. A command-line
invocation remains available for automation and recovery, but is not the
normal owner interaction.

For monitoring requests, run `supervisor-dashboard` internally and report the
employee table plus only actionable alerts. Use the OpenCode child-session
result or Orca `worker-show`/`worker-read` only for the named employee; the
supervisor should not flood the owner with every raw transcript.

Owner decisions must be asked with the supervisor's native `question` TOOL,
which is actually awaiting the answer in the bound session. A shell/API-created
Form is not equivalent: it has no waiting agent. After the tool returns the
owner answer, the supervisor executes the scoped lawful next step and retains
actual action/transport receipts; a state write is not execution. Acceptance
notes are optional. Routine helper completions and QC-1 repair stay with the
supervisor, not a blanket owner approval queue. Web messages now use the native
prompt inbox and show real replies. The compatibility `owner-form-create`
command requests a real supervisor review; it does not create forms.
`owner-form-sync` refuses legacy detached forms. For a Web-interactive headless
supervisor use native prompt admission, not non-interactive `opencode run`,
which may automatically cancel questions.

When several Benchmarks are active, use the supervisor registry and keep one
campaign/evidence tree per supervisor. The shared dedup root is the only
cross-supervisor index: it stores fingerprints and frozen task hashes, not
worker files. A producer may read and write only its own exclusive job root;
QC gets a separate root and read-only task evidence.

The production Web console may discover these supervisors from approved scan
roots. Only a validated `supervision/supervisor-stamp.json` is a recognized
watermark; arbitrary folders and process names are never auto-enrolled.

Bound employees report into the generic Secretary mailbox. The Secretary
coalesces heartbeat/completion/blocker reports over a bounded interval and
sends one evidence-linked digest to the actual Supervisor OpenCode session;
workers must not interrupt the Supervisor one by one. Blocked/failed/question
reports may trigger an early digest, but still use the same mailbox and
deduplication path. The resident Web watchdog detects a missing heartbeat or
idle bound session after five minutes and sends an observational alert through
the Secretary. It never fires or replaces an employee. The Supervisor must
inspect, contact/resume or escalate the worker and record what happened. The
Workforce card exposes `healthy`/`heartbeat-missed`/`stalled` instead of
trusting `employment_status`.

The live Web surface shows the Supervisor, Secretary status and active
top-level workers. Child helpers remain inside their parent's detail view.
Completed, fired, shelved and abandoned work leaves the live queue while its
immutable receipts remain in audit storage; the Secretary must not reopen it.
Read `references/supervision.md` for the benchmark-neutral contract.

**Owner control of lifecycle decisions:** candidate closure/shelving, changing
Seed, producer termination/replacement and standby require prior owner approval.
Report a failed screen truthfully, but do not silently stop the task on that
basis. Use `owner-request` with the employee, proposed action and a report
path/hash, then ask the actual native question; see `references/supervisor-protocol.md`.
While an owner request/question is pending, both directions of automated
worker communication and dispatch are held. Telemetry/evidence is retained;
owner chat and answers stay open. A separate Web owner pause also interrupts
only the bound supervisor (unless preserving its pending question) and parks
adapter worker inbox items. Resume does not replay lifecycle messages or
approve a proposal. Do not treat ordinary idle as another reason to send a
standby directive, which would wake the employee and generate another alert.

If the owner requests a browser dashboard, read
`references/supervisor-web.md` and start `supervisor-web` with authentication.
The Web console is another view/control plane over the ledger, not a second
source of truth.

## Public-task-first mode

When the owner asks to start from an open EdgeBench task or its trajectory,
read `references/public-task-first-production.md`. Pin the official public
catalog and task-definition bytes, then create an analysis-only inventory:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  public-task-inventory PUBLIC_TASKS.jsonl \
  --catalog-revision PUBLIC_CATALOG_REVISION \
  --output PRIVATE/public-task-inventory.json
```

Use the public task as a parent baseline: replay controls and inspect its
feedback, curve and shortcut. A child needs a new task identity and new
source/input/causal mechanism plus its own r0/r1/r2 evidence. Do not copy a
public patch, hidden case, task text, public score or trajectory into the child
as new evidence. Public upstream IDs still drive overlap/contamination review.
The inventory never promotes a Seed or authorizes rollout.

The owner may invoke the Skill in `rehearsal` mode to test the production line.
Rehearsal may discover and pin Seeds, run bounded micro-prototypes and verify
gates, but its files stay outside customer inputs and do not promote a Seed,
freeze a formal task, start rollout or establish delivery acceptance. Keep the
mode and its limitations in every rehearsal receipt.

For a new authoring request, capture the Benchmark requirements, initialize the
workspace, bind the profile intent before design, then bind it again to the
generated task bytes:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE --profile PROFILE_ID \
  --profile-file PROFILE_JSON --requirements-doc USER_REQUIREMENTS.md
```

Then use the same OpenCode session for:

```text
requirements → profile intent → source scout → source mirror
→ feasibility/learning-loop screen → Seed promotion
→ request → workspace init → task profile intent → design → generate
→ task-bound profile → validate → measure
→ pre-rollout-qc → main review/difficulty → format selection
→ preflight receipts → freeze → authorized rollout → collection
→ selected-format audit → package → rl-task-qc
```

For a single task, the main session may execute this sequence directly. Use
the campaign dispatch and native Task workers only when the user asks for
parallel or multi-task production. Ask for decisions that materially change
the profile or delivery contract; retain unresolved choices as `pending` or
`BLOCKED` rather than inventing defaults.

For a task, rollout, QC or customer-package **repair**, read
`references/repair-evidence-and-reply.md` before editing. Map every finding
to actual input, scored identity and evidence; repair and reverify the current
artifact rather than relabeling old scores. **Every verified fix must produce
a separate, point-by-point `修复逐条回复` Markdown** outside customer task inputs
and ZIP, with the change, executed checks, unresolved items and final
artifact hash. A changed file without passing evidence is not a completed
repair.

The canonical pre-rollout state sequence is:

```text
REQUIREMENTS_LOCKED → SEED_PROMOTED → D0 → D1 → D2 → DESIGN_ACCEPTED
→ BOTH_FORMS_BUILT → PREFLIGHT_PASSED → FROZEN → E1_SCREENED
→ E2_COMPLETE → CURVE_ACCEPTED → DELIVERY_ACCEPTED
```

Only content-addressed task-bound receipts advance a state. `freeze WORKSPACE`
creates the subject token after the active profile, format, task bytes,
Work/Judge identities and profile-required preflight gates agree. After freeze,
task/scorer/image/policy changes create a new identity; packaging and trajectory
metadata repairs retain the frozen identity.

The latest task-specific owner instruction takes precedence over the
default production scope and historical reviewer preferences below.

Produce a **reusable task production line**, not another hand-built scheduling
sample. Use SForge as the rollout harness, then deliver the user-selected
`sforge-only` or `harbor` format. Record the selection in the workspace before
frontier rollout; never infer it from a filename or silently substitute one
format's evidence for the other. Read `references/vendor-v3-applicability.md`
for the mandatory-source mapping and `references/current-owner-scope.md` for
the current handoff choices.
Read `references/five-task-qc-lessons-2026-10-01.md` for generalized non-format
failure modes from the five-sample review; its V2.4/Harbor/Smoldata clauses are
not replacements for the current Meta V3.0, EdgeBench V1 and owner decisions.
Read `references/authoring-lessons-2026-10-05.md` before authoring or measuring:
it covers executable model Work paths, parser-safe metadata, score ladders,
partial validity, provider/resume checks, comparable curves and final-package
evidence. It is a concise lessons reference, not a replacement for the
current owner contract or task-specific evidence.
Harbor generation, audit and export are required when Harbor is selected and
remain optional diagnostics for SForge-only. For the selected Meta handoff,
deliver the format-specific English package and a separate English QC report.
Keep the English README/materials in the selected package. Generate a Chinese
README and Chinese QC translation for the owner in an isolated private review
directory, never in customer bytes or beside the customer files as an upload
candidate. Local output is not a Smoldata/S3 submission or acceptance.

Mandatory source documents are `Meta Vendor Data Spec V3.0.zip` and current
`Requirement — V1` in `EdgeBench Vendor Dataset Requirements.md`. Ignore the
appended archived initial requirement. Meta V3.0's generic ARM recommendation
does not relax EdgeBench's mandatory dual architecture support; its
Terminal-Bench binary rule does not apply to EdgeBench `bounded_continuous`.
Both documents' Harbor/unpacked-Harbor delivery clauses apply when the user
selects Harbor. When the user selects `sforge-only`, the SForge handoff
mapping is used and Harbor checks remain inapplicable. A format choice is a
scoped delivery decision, not a measured pass for the other format.

Confirmed owner exclusions: source declarations, catalog dedup, redistribution
authorization, SForge `setup_cmds`+`image_tag` dual entry, and Q8 task review.
Record these as `not_required_by_owner`, never measured passes. Actual input,
scorer and snapshot bytes still have SHA-256 identities for reproduction.
DOCX/vendor Work/verifier isolation and no-network operation remain separate
technical checks; a configured network mode alone does not prove them.
**Build-time network is allowed:** recipients build independent Work/Judge
contexts online, fetching the declared digest-pinned base and version-pinned
dependencies. Do not require offline OCI images or a wheelhouse in the customer
ZIP. A build-network declaration is not runtime internet access; the scored
Work/Judge remain offline and must pass executed isolation checks.

**ARM64 performs applicable task checks except frontier rollout and main-agent
Q1–Q7 review:** image/context builds, NOP/reference or open-solution calibration,
same-snapshot repeats, scoring parity and reproducibility. Use the owner-managed
real arm64 host: **owner-authorized internal SSH target `root@10.2.39.3`**
(confirmed 2026-10-13). Connect with `ssh root@10.2.39.3`; use that host's
privately configured SForge binary path and an exclusive validation workspace.
Verify actual `aarch64`/`arm64` execution and retain task/image-bound build,
scoring, repeat and cross-architecture receipts. SSH access alone is not an
ARM64 pass. This endpoint belongs only in internal Skill/owner operations; never
copy it, credentials or host paths into customer tasks, README/QC or archives.
Historical QEMU receipts keep their actual execution mode; they do not become
new native measurements. Never write credentials into assets or commands.

## Select and bind the Benchmark profile

EdgeBench thresholds are profile values. They are not universal production
limits for compatible Benchmarks. Select a built-in profile or bind a
user-supplied profile before selecting the delivery format:

See `references/benchmark-profiles.md` for the profile schema and precedence.

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE --profile edgebench-v1

# A custom profile is a validated JSON contract derived from the user's
# requirements document; the document hash is retained in the receipt.
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE \
  --profile custom-benchmark \
  --profile-file CUSTOM_PROFILE.json \
  --requirements-doc USER_REQUIREMENTS.md
```

The active profile controls budget, trial count, repeat count, architecture,
quality-review scope, reward and format applicability. User requirements take
precedence over Benchmark and vendor defaults. Universal evidence integrity,
privacy, hash and truthful-status checks remain active for every profile.

## Select and bind the delivery format

After task generation and before task review or model rollout, record the
user's choice against the current task bytes:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py delivery-format WORKSPACE --format sforge-only
# or
python3 edgebench-task-generator/scripts/topify_pipeline.py delivery-format WORKSPACE --format harbor
```

Audit the selected path with `format-audit WORKSPACE`. Use the SForge audit and
`package-samples` for `sforge-only`, or Harbor audit and `export-harbor` for
`harbor`. The unified `package-selected --manifest ...` command requires an
explicit `delivery_format` in the manifest and dispatches to the matching
packager. Before trajectories, write the independent PQC v0.2 process report:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  pre-rollout-qc WORKSPACE --output PRIVATE/pre-rollout-process-qc.md
```

## Operate the campaign loop

Six EdgeBench domains: systems/software (26), science/ML (25), professional
work (18), optimization (12), formal math (10), games/simulators (9). These
are campaign planning targets for 100 tasks, not exact small-batch quotas.
Do not relabel an optimization fixture to fill unrelated categories.

The default batch loop is employee-first, not a standalone scout/author pool:

```text
campaign/profile lock
→ employee-hire producer (one task)
→ supervisor-dispatch producer
→ producer discovers and pins its own Seed
→ producer requests seed/prototype child Tasks when needed
→ producer r0/r1/r2 → independent QC → freeze → rollout
```

The legacy `production-plan`/`production-dispatch` path signs short-lived
verifier/author jobs and is not the default for a user request to create
producers. Use it only when the owner explicitly requests source scouting,
legacy worker dispatch or unattended recovery. A scout is never a producer and
a source lead is never a completed task. Every producer and child worker gets
an exclusive job_root; the main supervisor reviews actual receipts and keeps
shared campaign writes serialized.

At discovery, read `references/seed-sourcing.md`,
`references/source-registry.md` and the applicable
`references/domain-production.json` domain. The producer owns the discovery;
the supervisor only accepts a receipt-backed Seed and dispatches bounded child
help under that producer's tree.

At discovery, read `references/seed-sourcing.md`, `references/source-registry.md`
and the applicable `references/domain-production.json` domain. Capture actual
leads, pin task inputs with `mirror-source`, refine the real-work/feedback
hypothesis and screen with:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py seed-screen CAMPAIGN --seed-id CATEGORY/SOURCE/LEAD --for-prototype --owner-sample
```

This advances a technically feasible provisional Seed without requiring the
excluded license/catalog dossier. Do not call legacy rights-screened
`seed-prototype`/`seed-promote` to work around these exclusions. An unavailable
scorer or dependency leaves a lead unapproved; bounded `feasibility-*` and
`micro-prototype-*` probes may study it, but unvalidated micro-scores are not
calibration or difficulty evidence.

## Author and harden one task family

Use isolated workspaces and a source-grounded professional objective. When no
plugin fits, use the hashed `authored_context` contract; read
`references/authored-family-contract.md` and `references/authoring-pitfalls.md`.
The current snapshot adapter supports one declared file; a small fixed source
set needs its measured deterministic-tar wrapper, not a claim of arbitrary
multi-file support.

```text
init -> design -> generate -> validate -> measure
     -> difficulty-pack/card -> run-controls -> candidate-audit
     -> candidate-register -> main review
     -> same-family r1 -> same-family r2 -> freeze or shelve
```

Every r0/r1/r2 keeps actual NOP/interior/strong and generic-control process
evidence. Each hardening identity preserves the Seed/work objective, names its
parent hash and targets an observed weakness with a distinct lawful causal
mechanism; read `references/hardening-family-contract.md`. No cosmetic volume,
waiting, noise or moved reward upper. If no defensible mechanism exists, record
blocked instead of inventing two rounds. After measured r2, the main agent
 freezes or calls `seed-shelve`; do not indefinitely iterate shallow families.

In hierarchical production these are mandatory supervisor turns, not optional
author suggestions:

```text
r0 complete → supervisor says “too simple; harden once” → r1
r1 complete → supervisor says “still too simple; harden a second mechanism” → r2
r2 complete → independent QC-1 → repair/QC-2 → freeze/rollout
```

The `hardening-challenge` receipt records each verdict. QC must reject a
producer whose `hardening_revision` is not exactly 2.
Across the batch, compare scored source instance, input cases, work objective,
Judge and weights; different runtimes or wrappers of one scored task count once.

Cheap saturation thresholds are authoring diagnostics, not vendor clauses.
Host controls are not automatically Agent-reachable; prove availability inside
the frozen offline Work image before declaring a speedrun. Read
`references/difficulty-and-rubric.md` and `references/evidence-truth-and-delivery.md`
when judging causal probes, partial reward or infrastructure failures.

## Build, calibrate and run SForge

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py build WORKSPACE
python3 edgebench-task-generator/scripts/topify_pipeline.py measure-image WORKSPACE --architecture linux/amd64
python3 edgebench-task-generator/scripts/topify_pipeline.py measure-image WORKSPACE --architecture linux/arm64
python3 edgebench-task-generator/scripts/topify_pipeline.py sforge-prepare WORKSPACE --sforge SFORGE_EXECUTABLE
python3 edgebench-task-generator/scripts/topify_pipeline.py hash-data WORKSPACE
python3 edgebench-task-generator/scripts/topify_pipeline.py format-audit WORKSPACE
```

Run native commands on their actual hosts, with the same content-addressed
workspace and saved receipts. `sforge-prepare` uses the internal task scoring
core, but final calibration, runtime isolation and reproducibility evidence
must describe the actual delivered/scored SForge Work/Judge contexts. Do not
substitute an internal Harbor image score for that SForge evidence. Optional
Harbor bridge checks do not add a second format acceptance gate.
Generated task build policy permits justified public access; buildx uses the
declared mode, while scoring containers retain `--network none`. An old frozen
task declaring no-network builds retains that older mode until a new identity
is generated; never reinterpret a scored build receipt. For online builds pin
dependencies and review independent rebuild evidence before marking the
reproducibility row complete.
Seal both delivered SForge context trees before image builds and compare their
hashes during audit/packaging. The reproducibility receipt must bind those hashes
and inspect two matching-image native build process records for each Work/Judge
role on both amd64 and arm64. An apt-based build needs fixed snapshot indexes
and versioned packages. The default pinned Python base now generates a Work
Dockerfile from Debian trixie snapshot `20260919T000000Z`, locking git/curl/jq/
sudo/xz-utils and both architecture package-index SHA-256s in `apt-lock.json`.
Build-time `verify_apt_index.py` downloads the selected native `Packages.xz`
and refuses a digest mismatch before apt installs from the explicit snapshot
source. No offline index/wheelhouse is shipped.
A different pinned Debian base needs a task-bound `task/apt-lock.json` with the
same verified schema. The lock alone is not a successful online build: preserve
real native builds, image-inspection receipts and offline scoring separately.

Before model hours, perform the applicable checks in
`references/pre-rollout-quality-gates.md`: genuine NOP, runnable oracle **or**
explicit open-solution, five identical-reference snapshot evaluations, cold-start
and end-to-end timing, structured score/validity/named checks, native architecture
comparison, pinned manifest-list base, reproducible contexts and agreed resource
envelope. Three hours is the accepted budget minimum; twelve is preferred.
Report spread relative to measured baseline-to-ceiling headroom.
Assess nonzero spread against measured headroom with task-bound evidence;
five records alone cannot establish acceptable or correctly reported noise.
The shipped NOP must evaluate the original unmodified model-visible starter:
both snapshot hashes and actual SForge scores must equal the declared baseline.
Do not swap in an easier empty file and call it a passing NOP.
Before spending model hours, exercise the **documented** edit and local-score
path inside the actual scored Work image, UID, workspace and host-controller
policy. A source Dockerfile `chown`, a different reference image or a local
development scorer's toolchain setup does not prove what the model sees.
Judge an observed failure against the supported lawful workflow rather than
promoting every failed optional shell command into a task-quality defect.
Check that the recipient can pull the pinned base and actually build the
delivered Work/Judge contexts before describing the package as rebuildable.

The main agent signs Q1–Q7 and causal difficulty against frozen bytes before
`started_at`. Q8 is not required. Record the actual resource agreement with
`record-resource-agreement`. Run a batch-bound NOP scan on the selected frozen
SForge adapters using `sforge-batch-nop` and the documented SForge manifest.

Read `references/sforge-rollout-operations.md` before launch. `rollout-check`
must be complete; `rollout-batch --workers N` uses the requested positive
concurrency (default three); there is no two-run ceiling. The owner's
2026-10-07 instruction permits Hospital/PACE/Sperner to run concurrently.
When the owner reports the account pool unavailable, pause model launches
and retries while retaining original evidence; offline repair/QC may continue.
Retain at least three same task/model/agent/provider/effort/budget trials and
their true arithmetic mean. Auto-eval is not an Agent submission. Do not infer
Agent Steps from tools/submissions, or turn absent scores into zeros. A short
`monitor-batch` tick observes without stopping runs; never restart a service
used by active sessions. `rollout-collect` must reconcile raw reports/archives,
history and the original pre-rollout review timestamp.
For a host-side controller, keep provider secrets on the host. A tiny model
request proves present reachability, not hours-long availability. Read real
PIDs and checkpoints at the requested cadence. An in-place resume requires
its **original owned Work container** and unchanged owner/image/policy/runtime
identity; `work_usable` alone does not establish the container still exists.
If it has been removed, preserve the original short run as diagnostic and
start a new, separately named full-budget run only when authorized. Neither
`completion_unknown` nor an unscored Judge attempt is a model score.
Before final delivery, re-score retained real model submissions against the
frozen Judge on applicable native hosts, comparing validity, raw metric,
normalized score and named checks. Five repeat reference scores do not excuse
historical snapshot drift. Missing replay receipts or a changed score remain
`incomplete`/failed, not a footnote. A model exceeding the measured reference
triggers upper-bound/identity review rather than silent rescaling.

## Package SForge and customer documents

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py sforge-audit WORKSPACE
python3 edgebench-task-generator/scripts/topify_pipeline.py package-samples --manifest BATCH_DELIVERY.json --output "CUSTOMER_OUTPUT_DIR/【Morrow Lab】EdgeBench Sample YYYY-MM-DD.zip" --review-dir PRIVATE_NEW_REVIEW_DIR
```

Use `examples/batch-delivery.json` with actual frozen workspaces; the current
default customer language is English. The ZIP contains the frozen task JSON,
`BENCHMARK.yaml`, independent Work/Judge Dockerfiles/contexts, original English
task text, metrics and scored evidence. A separate English QC report is beside
the ZIP. `--review-dir` writes two Chinese owner-review documents outside the
customer destination: use an existing customer output directory and create
the new private review directory outside its tree. The active installation's
`delivery-policy.json` fixes customer output and private translations under
separate configured roots. Older owner-specific sample profiles remain
`review_sample_incomplete`/`formal_eligible: false` when applicable.
Neither a default diagnostic `--draft` ZIP nor an incomplete review sample is
a passing formal acceptance receipt. Synthetic fixtures are not vendor evidence.

For customer copy, apply `README文档设计指引对外.md` where it fits EdgeBench:
plain business context, first-person design motivation, task-specific consequences,
early measured outcomes, per-trial metric definitions and self-contained README.
Do not force upper/lower model roles onto a single-model cohort, invent Step or
Agent wall time, or reuse the guide's eight-category 100-point score after Q8
removal. The README must not reference the external QC report. Missing mandatory
vendor evidence stays incomplete even if no implementation defect is confirmed.
Before any customer file is published, the packager uses an allowlist and
scans ZIP members, decompressed scored snapshots and the independent report
for credentials, internal endpoints/host paths, other-customer material and
private data identifiers. Block a tainted frozen task instead of editing its
scored bytes and recycling old model results. Do not ship authoring-review,
quality-review, source/owner-scope or raw provider/session files.
Reconcile the scorer's selected best, any requested **best valid** result,
last scored report, each graded submission and actual time checkpoints as
different measurements. Under score-first selection, a globally
`valid=false` submission can retain positive valid-case credit; preserve it
without calling it the best valid answer. Keep the old cohort's CSV and
`model_trials` separate from the new cohort and derive all README/CSV/QC
counts from the same original submission IDs. Write the actual package hash
manifest (currently `metadata/sha256.json`) before archive creation and verify
it is actually **inside** the archive. After the last document change, check every member's content/mode, relative links
from the document's real folder, sensitive text/archives and final ZIP
SHA-256. Update the separate repair reply with that final digest; local ZIP
verification is not a platform preflight or acceptance receipt.

Local packaging does not establish external platform acceptance. Before creating a Smoldata
offer/dataset, uploading, finalizing or selecting S3, ask the owner for channel,
dataset, sample/licensed status and the agreed identifiers/terms. EdgeBench V1
rejects first-batch failed tasks rather than permitting fix-and-resubmit; Meta's
generic replacement feature does not automatically override that rule.

`harbor-audit`, `record-harbor-evidence` and `export-harbor` are the
format-specific path when the user selects Harbor. `sforge-audit` and
`package-samples` are the matching SForge-only path. After either final package
is built, run the repository `rl-task-qc` gate with the selected format and
retain its JSON receipt outside the customer package:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py rl-task-qc FINAL_PACKAGE_OR_DIR \
  --format harbor --profile PROFILE.json --json PRIVATE/rl-task-qc.json
```

The final QC is a separate static gate. It does not turn missing runtime,
native architecture, model-trial or external platform evidence into a pass.
