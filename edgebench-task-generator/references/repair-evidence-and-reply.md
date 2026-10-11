# Repair evidence and a reply for every EdgeBench fix

Read when an owner supplies a QC report, a vendor preflight error, a failed
rollout, or requests a task/package repair. This is a procedure for the
**currently selected task, scorer, runtime and delivery contract**. A past
sample's numbers, format, waiver or quality score do not become defaults for
another sample. The user-selected Harbor or SForge-only contract and the
applicable vendor scope decide whether format checks, Q8, language and
external upload are in scope.

## Start with a finding ledger

Before editing, map **each requested finding** to the exact existing file,
observed failure and applicable requirement. Mark it `confirmed`,
`not_reproduced`, `format_exception`, or `needs_evidence`, with the test or
original report supporting that classification. Keep independent root causes
separate. An LLM interpretation of a transcript is a lead: reproduce the
claimed failure under the *actual scored image, identity, UID, filesystem,
network policy and command*, then decide whether it blocks the supported
solution path. A failed optional shell command is not by itself a Q3 or Q7
defect when the documented local scorer and lawful source replacement work.
Never change a raw Judge `valid`, report or checkpoint to make a finding pass.

For platform errors, follow the first failing stage: a preflight TOML parse
error means **no task was executed**. If the platform cannot represent an
integer exactly, verify its parser/contract and encode that TOML metadata
value as a string; preserve the numeric value used by the actual Judge/JSON
unless the scorer contract also changes. Before describing an image as
deliverable, check its `FROM` is publicly retrievable by the recipient (a
digest attached to a private local tag is not public), pin its dependencies,
build the **shipped** context, inspect actual user and architecture, and run
the supported local Work feedback. Keep construction-network permissions
distinct from offline agent/Judge execution.

## Protect the scored identity

Record hashes for original task/prompt/score contract, source contexts and
scored Work/Judge images **before and after** a repair. Do not edit the old
rollout artifacts. If model-visible inputs, scoring semantics, or scored
Work/Judge behavior change, rerun the affected controls and model trials on
a new identity; old model scores remain attributed to the old image. If only
non-model-visible packaging metadata changes, independently verify that claim
and identify the exact unchanged input/score hashes. Do not infer parity from
similar source code, a Dockerfile that was not built, or a reference run under
a different image. Optional SForge↔Harbor bridges are not a prerequisite
unless this owner's current delivery contract requires them.

Under a host-side model controller, keep provider credentials outside Work
and Judge. Check the intended model's catalog and one tiny request before a
long launch, but a successful health probe does not guarantee six hours of
availability. Monitor actual PIDs and checkpoints at the owner-requested
interval; a background monitor PID is not proof the runs are progressing.
For `completion_unknown`, preserve the attempted-request receipt and do not
blindly replay an ambiguously billed request. An in-place resume requires
the **original owned Work container to exist** and its owner, image, policy,
boot/runtime and provider identity to match. Inspect that container before
starting a resume; `work_usable: true` in a checkpoint alone is insufficient.
If attachment is impossible, keep the stopped run as diagnostic evidence and
use a new run ID and full measured budget when the owner authorizes a fresh
trial. Never transfer partial active seconds or quietly call a short run a
completed full-budget cohort.

## One score ledger, many views

Derive README, CSV, QC and packaged trajectories from original submission
entries **and their matching Judge reports and snapshot SHA-256s**, not from
hand-entered tables. Reconcile every attempted submission: count completed
numeric scores separately from `score=null` infrastructure events; preserve
both reports and snapshots for claimed per-submission evidence. Compute
the requested checkpoint grid (for example 2h/4h/6h) only from actual
timestamped scored submissions.

Keep these measurements distinct:

- Original selector's best, including real positive partial credit with
  `valid=false` under a task's score-first rule.
- Best **valid** submission if the customer explicitly asks for valid-only
  selection; never overwrite the original selector's result.
- Final scored submission, which may have a different validity or score.
- Measured active agent wall time versus configured budget; Agent Step is
  `not recorded` unless the runner actually records main-model Steps.
- A measured reference, NOP or published scale anchor versus model trials.
- Original terminal/cohort status and whether a run qualifies under the
  stated trial rule. Supplementary runs do not retroactively become a
  fully overlapping batch.

Do not bring a historical cohort's CSV/score table/model-trial directory
into the current package as though it described the new run. Before shipping,
compare the best/valid/count/mean/checkpoints in *every* table against the
same set of trial IDs. A copy of only best and final snapshots is insufficient
if the report promises full scored curves or all scored submissions.

## Repackage and re-audit after the **last** change

Select files by an explicit allowlist. Preserve task-required build inputs
and original English model-visible bytes; exclude host-private provider
configuration and raw session receipts. A customer-safe trial summary can
omit private host metadata without altering the original private checkpoint
or any original scored Judge report. Preserve needed source/score identities
and keep old versus new cohorts visibly separate in the private ledger.

Apply the owner's current `README文档设计指引对外.md` to customer documents:
benchmark-only outer names, English self-contained in-folder README, separate
English QC and, when requested, Chinese documents outside the customer
folder/ZIP. State model roles only if preassigned; when the applicable guide
asks for a comparison table with absent roles, show `not assigned` rather
than inventing a weak model. An owner request to omit a useless empty table
for a particular delivery takes precedence. The independent QC must keep
material defects and evidence limits truthful even if detailed operational
history is excluded from the customer README. Do not add source/license
review claims to a delivery from which the owner excluded that review.

Build the archive **after** all final document changes. Write the actual hash
manifest (currently `metadata/sha256.json`) first and confirm the archive
actually contains that manifest. Independently reopen the completed archive;
compare every member's uncompressed SHA-256, safe
relative path, type and allowed mode against the extracted customer tree.
Check links from each document's *actual* location (an external Chinese
README needs the customer-folder prefix), UTF-8/English-only task inputs,
privacy in text and decompressed archives, and the outer ZIP hash. A digest
computed before the last edit is invalid. `uploads preflight` and platform
acceptance are separate **executed** facts: never infer either from a valid
local ZIP; run them only for the requested platform/format.

## Mandatory reply artifact for **each repair**

Every repair produces an independent Markdown **逐条修复回复** outside task
inputs and outside the customer ZIP. Create or update it when the fix is
verified, **before** reporting the fix as complete. Do this for skill/process
repairs as well as task, image, metadata, rollout and package repairs. If
several findings are fixed in one change, use one reply with a row for each
finding. Keep its prior version and update the final ZIP hash only after
repacking. Never say a repair is finished based on intent or a code edit.

Use this minimal structure, omitting fields that genuinely do not apply:

```markdown
# EdgeBench 修复逐条回复 — <date and batch/task>

| 报告条目 | 原始证据与适用范围 | 修复文件/行为 | 验证命令与实际结果 | 状态 |
|---|---|---|---|---|
| <section> | <path, observed failure> | <path, exact changed behavior> | <test, exit/status, evidence path> | 已修复 / 不适用 / 未完成 |

## 成绩与身份
<scored task/image/prompt unchanged or new identity; measured scores,
validity and trial eligibility kept distinct>

## 交付核验
<customer ZIP and report locations, manifest/member count, final SHA-256,
links/permissions/privacy checks actually executed>

## 剩余项
<specific unfinished checks; no invented platform upload/acceptance>
```

The reply is a **report on evidence**, not a place to conceal inconvenient
results or claim a manual decision changed a Judge score. Preserve a real
failure and its cause privately even if the corresponding customer document
uses a shorter, neutral explanation.
