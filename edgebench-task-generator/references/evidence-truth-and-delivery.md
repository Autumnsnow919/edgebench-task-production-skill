# Score truth and lean customer delivery

Read after an executable score ladder exists and again before packaging.
Keep one append-only record for each frozen task/image/scorer identity and
scored intervention, then derive the score table, quality finding and ZIP
index from those receipts rather than retyping numbers into three documents.
For a requested repair or external QC finding, also read
`repair-evidence-and-reply.md`; produce a separate point-by-point reply for
each verified repair and derive its ZIP digest from the final verified bytes.
The 2026-09-29 Humanoid/ITC2007 retrospectives and the 2026-09-28 TB4
internal QC report motivated these checks; their individual results and
owner exceptions are **not** acceptance rules for a new EdgeBench task.

## Do not conflate different kinds of evidence

| Input | Correct interpretation | Incorrect shortcut |
|---|---|---|
| A published per-case oracle at 100 | Cost/calculation anchor on these cases. | Calling it a general executable solver's achieved 100. In `open-solution`, separately report the strongest *actually scored program*. |
| Prewritten “cheap” program identical to strong SHA | Self-equality on a scored snapshot. | Inferring any independent agent could invent that program during its measured copy/export time. |
| Independent control near the strongest achieved score | A real shallow-task diagnostic; measure source/hash, actual generation+Judge wall time, legal per-case metrics. | Comparing only with an unachieved declared 100 scale, or silently erasing the `revise` outcome via a broad waiver. |
| `valid=false` **and** a real positive score/report/archive | A scorer-defined partial score; keep it in the trajectory and explain which cases are valid. | Automatically replacing it with zero merely because a different case failed. |
| History `score=null`, no report, Docker creation error log | **Unscored infrastructure event** with its own log digest; not a model submission at zero. | Making up a scored report/archive or silently dropping an unexplained history entry. |
| QEMU ARM oracle 100, submitted program 0 | Judge arithmetic works for the oracle, while portability of the actual program failed. | “ARM64 task passed” or three additional frontier runs. |
| `final_result.timed_out=true` | One flag to compare with observed runtime, start timestamp, scored reports and configured budget. | Counting an early owner interruption as a full-budget trial based on the flag alone. |

`rollout-collect` checks an actual report/archive and history round identity,
retains legitimate scorer-defined positive partial credit even if the report
has `valid=false`, and accepts an otherwise comparable completed run with an
explicitly evidenced Docker API timeout that produced **no** report. Such
unscored events remain a separate `unscored_attempts` list with a log SHA;
unclassified or missing-log gaps fail closed. A task-specific grader can
have stricter validity semantics: never force every grader to award partial
credit, and never call a scorer execution error a model zero. Calculate
best-so-far and checkpoints only from timestamped scored reports; Step,
agent submissions and automatic snapshots are different counters.
The provider is part of the fixed model cohort: retain a private, task-bound
SHA-256 fingerprint of the configured provider+base URL in each launch process
receipt and in `rollout-collections`. At packaging, verify every complete
run's launch receipt against that fingerprint and the request's provider.
Keep provider URL/credential variables out of customer ZIP and QC prose.

## Freeze the delivered image, not a development approximation

Before model hours, record the task/scorer/Work/Judge content and image IDs;
actually build final contexts online from digest-pinned bases and pinned
dependencies. If reproducibility is claimed, run two independent builds
against those frozen inputs and compare image IDs or OCI content. Internet
access during construction does not permit internet access during scored Work
or Judge execution. Floating apt sources, unversioned network-only wheels,
mutable Git index bytes and a Dockerfile edited after scoring cannot certify
the image a model used. If
the environment or scoring boundary changes, issue a new identity and
repeat parity, task quality and model evidence. An ABI restriction such as
an x86-only seccomp filter must be checked on the **submitted program** as
well as the trusted reference under QEMU; see
`qemu-and-independent-review.md`.

For online-build reproducibility, `evidence/sforge-reproducibility/latest.json`
must bind the frozen task hash, sorted `linux/amd64`/`linux/arm64` platforms,
`dependencies_pinned: true` and a `{path, sha256}` reference named
`build_evidence`. The referenced JSON has `schema_version: 1`, the same
`task_sha256`, the prepared adapter's `contexts_sha256` map (`work`, `judge`),
and eight `rebuilds`: rounds 1 and 2 for both roles on both native hosts.
Each rebuild contains `architecture`, `role`, `round`, `image_id` (Docker
`sha256:` ID), plus `{path, sha256}` references to its original build process
JSON, `inspect_process` JSON and `inspect_stdout` JSON bytes. The build
process JSON records successful `returncode`, `image_id`, the
`observed_host_architecture` measured by the builder's process recorder, and
the complete `docker buildx build --platform ... --network ...` argv targeting the sealed
context. The inspected stdout must report the same ID and architecture, be
hash-bound to a successful `docker image inspect <tag>` process, and both
rounds must inspect to the same
image ID per role/architecture. The main reviewer still verifies that these
files came from real native builds; a synthetic receipt is not process proof.
No raw build-process file containing owner host paths is copied into customer
bytes. The packaged summary carries the scored context digests, including
build-relevant file modes. A bare `dependencies_pinned: true` assertion or an
unversioned apt source does not satisfy this gate.

For the production default `python:3.12-slim` pinned base, the generated Work
Dockerfile and `contexts/work/apt-lock.json` lock Debian trixie to the
`20260919T000000Z` snapshot and version the five installed tools (git, curl,
jq, sudo, xz-utils). Both amd64/arm64 `Packages.xz` SHA-256 digests were
checked against the snapshot's published `InRelease` SHA-256 rows. Builds fetch the
official snapshot online; the included `verify_apt_index.py` first checks the
downloaded native `Packages.xz` bytes against `apt-lock.json`, then apt uses
the explicit pinned `Dir::Etc::sourcelist` with other source parts disabled.
The audit also checks the sealed checker bytes, lock file, declared packages
and every active apt invocation; an alternate unpinned install invalidates
the reproducibility claim. The checker is part of the hashed Work context.
Builds do not require an
offline mirror. Scored runtime remains offline. A new base must
carry a task-bound `task/apt-lock.json` with matching base reference, codename,
snapshot, package versions and both verified index hashes. Verify the base OS,
snapshot availability and real native build before treating the lock as
operational proof.

After frontier runs, `evidence/sforge-model-replay/latest.json` is a distinct
post-rollout determinism receipt. It has `status: complete`, the frozen
`task_sha256`, `adapter_task_id`, and one `runs` entry per *complete* model
trial. Each entry identifies that trial's `run_id`, a retained best `round`,
its original `archive_sha256`, a hashed copy of its **original** scored
`report.json`, and `replays` for both `linux/amd64` and `linux/arm64`. Each
replay has `{path, sha256}` references named `snapshot`, `report`, `process`:
the snapshot must hash to that original archive; the process must have
`returncode: 0`, actual `observed_host_architecture` and `sforge eval --task
<adapter-ID> --archive <snapshot-path>` argv. Validity, `score_0_100`, raw
`metrics` and named `checks` must match the original report. The auditor
cross-checks original report SHA against the collected run's original
trajectory; a fabricated JSON process is not real native scoring evidence
and must be rejected by the main reviewer. A mismatch or missing one-run/
one-architecture receipt blocks the formal SForge audit, even if all five
fixed-reference scores were equal.

## Ship rebuildable sources and evidence

The agreed customer artifact is an English SForge **source/evidence** ZIP:
independent Work/Judge Dockerfiles and build contexts plus real build/scoring
receipts. Recipients may pull bases and dependencies while building. An OCI
image or offline wheelhouse is not required in the ZIP; do not claim offline
reconstruction. Preserve exact dependency identities and rebuild evidence.
The owner does not require both `setup_cmds` and `image_tag` entrances;
Work must never receive Judge-private assets.

Use a **file allowlist** for customer bytes: English-original task, exact
SForge task JSON, independent Work/Judge contexts, frozen
metric/score/quality receipts and selected scored reports/archives.
The current packager keeps **every scored report** for the score-time curve
and only the best/checkpoint submission archives by default; remaining
snapshot SHA-256s stay in `metadata/trajectories.json` for cross-checking
against retained raw SForge logs. This avoids copying a GB-scale mirror
of every workspace solely because a model submitted frequently.
Explicitly reject raw `run_config.json` (`api_key` can be present), owner
`.env`, `.judge-admin-secret`, raw HTTP sessions, proprietary binaries,
`__pycache__`/`.pyc`, absolute personal paths, unrelated-customer/candidate
catalogs and history duplicates. Check source/code/document text **and**
binary members; a hash manifest alone does not make a sensitive package
appropriate to distribute. Preserve source authorization evidence according
to its permitted private/public scope, not by stripping it and claiming
rights were checked.

Select customer language and filename scheme in the campaign intake
contract. Task/prompt bytes must be English from the start. The current Meta
handoff uses an English ZIP README and separate English quality report;
set `"delivery_language": "en"` in the batch manifest. Chinese owner-review
translations are generated outside the customer output tree by `--review-dir`.
The same manifest is accepted by `sforge-batch-nop`, `quality-report` and
`package-samples`. No translation alters scored
model-visible task bytes. The README is a standalone customer entry
and never points outside the ZIP to the quality report. A client-facing
report must keep material defects, uninspected retained IDs, model
interruptions, architecture emulation and explicitly scoped owner waivers
honest; “remove internal process notes” is **not** permission to delete
negative evidence or turn incomplete into pass.

Before presenting a ZIP: re-extract into an empty inspection directory;
check it contains only the intended format, task definitions load in SForge,
work/judge separation survives packing, all listed hashes/ZIP CRC and file
modes agree, README links exist, and every displayed score matches a raw
report/archive and the stated run cohort. Report `source-only`,
`emulated-arm64`, `native-arm64`, `owner-accepted-for-this-identity`,
`failed`, and `not-inspected` as distinct facts. A separate report or owner
release decision may accept a **specific** sample despite a technical
failure; its signed task/receipt scope and the failure remain visible.

For efficiency, log candidate `time_to_first_real_score`,
`time_to_failed_gate`, `model_hours_spent_on_revise`, number of measured task
identities, first-pass pre-rollout Q1–Q7 rate, unexplained no-report events,
and `time_from_final_result_to_verified_ZIP`. These are operational
diagnostics, **not** new Topify acceptance cutoffs. The main speedup is to
stop paying for doomed long rollouts and GB-scale bundles, not to weaken the
independent score and release checks.
