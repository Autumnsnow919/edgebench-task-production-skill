# Source-backed quality checks **before** model rollout

Read this at campaign intake, before freezing final task/image bytes, and
again before `task-review ready` / `rollout-check`. For the current production,
read `vendor-v3-applicability.md`, `current-owner-scope.md` and
`five-task-qc-lessons-2026-10-01.md` before interpreting the historical
matrix: source/rights/dedup and dual-entry exclusions and historical QEMU ARM64 acceptance are scoped
owner decisions, not universal passes. The source hierarchy otherwise is:
current owner instructions → Meta Vendor Data Spec V3.0 and current EdgeBench
Vendor Dataset Requirements V1 → EdgeBench solicitation DOCX (background, SHA-256
`1c4f513f9bb561e1a3abafbf6ec76576d2ef9088ce62468afe5a072eec8cad6b`)
→ any separately agreed RL data contract (`rl-task-data-file-specification.md`,
SHA-256 `b99e1bb7cd8fddea635791b5a9691e32a2525925bb62054ba440e9919a3332e1`).
The TB4 internal report is a packaging failure case, **not** an EdgeBench
rubric or reward range. The two 2026-09-29 retrospectives supplied by the
owner are leads to check, not authority to waive a clause for every task.

## Contract mapping before choosing a Seed

| Scope | Apply | Do not transplant |
|---|---|---|
| EdgeBench DOCX, non-format clauses | Six-domain planning; hours-long feedback loop; isolated offline Work/Judge; source-scoped snapshots; attainable upper, continuous partial score and machine-readable formula; NOP, five repeats and reference or `open-solution`; timing; three comparable real model runs; rights/dedup, resources, architectures and per-file hashes. | Do not invent a 108-second control threshold, a mandatory 2h/6h/12h grid, or a fixed 6h budget: 3h is an acceptable budget lower bound and 12h is preferred. |
| User-selected SForge-only or Harbor handoff and SForge rollout | Select and bind the format before rollout. For SForge-only deliver frozen SForge JSON/BENCHMARK.yaml and independent Work/Judge contexts. For Harbor deliver unpacked Harbor task directories and independent agent/verifier contexts. Measurements must describe the actual selected/scored runtime. | Do not mix package trees or use one format's evidence to pass the other. SForge dual entry remains excluded unless separately selected by the owner. |
| Meta V3.0 and EdgeBench V1 | Apply the selected format's task identity, network policies, continuous reward, architecture and snapshot requirements. | Meta's optional ARM and Terminal-Bench binary rule do not relax EdgeBench's stricter architecture/continuous-score rules. |
| TB4 report and sample README guide | Learn whitelist packing, scope-specific evidence, clean English customer copy when requested, and secret/binary/cache scans. | TB4's 5-task difficulty band, eight slots, binary accuracy and Harbor metadata schema are not EdgeBench thresholds. Removing internal narratives must never erase genuine failures or license/QC limits from the external inspection report. |

At intake, record one short task- or campaign-scoped **acceptance contract**:
`required / pending / not_required_by_owner / not-applicable` for native ARM versus
QEMU, resource/GPU envelope, naming/version rules, the online-build
source/evidence package, customer language, run count and budget. An owner
waiver needs its exact task/version, scope, decision time and evidence hash;
it changes the delivery decision **only for that scope**, never a technical
`FAIL` into a measured `PASS`. Without confirmation, keep applicability
`pending` rather than assuming the RL or TB4 document overrides the DOCX.

## Cheap, ordered stop/go checks

These are decision dependencies, not a reason to serialize independent source
capture. The goal is fewer expensive final-image and
model-hour re-runs, not faster creation of an unqualified JSON fixture.

1. **Technical input and identity:** Pin real task-input bytes when using an
   upstream artifact, without requiring source declarations, licenses,
   redistribution confirmation or catalog dedup for this owner.
   Name logical vendor/dataset/task before scoring if the RL naming contract
   applies; a later `-v4` suffix does not repair a version identity.
2. **Small executable score loop:** With the same scorer, run a lawful NOP,
   starter, actual interior and strongest **achieved** reference. Separately
   identify any published per-instance answer and an executable *general*
   solution; if the latter is absent, declare `open-solution`. Check one
   hand-verifiable or independent native metric and SForge structured report.
   Keep raw metric, clipped score and `valid` distinct. Freeze the upper
   before looking at frontier results; if a lawful strategy exceeds its raw
   achieved reference, review that identity rather than moving the anchor.
3. **Early difficulty kill-test:** Run an independently written generic
   control (not a copy of the strong file) from generation **through the
   actual Judge response**, and record both full wall time and score. A
   prewritten program's 0.04-second export does not measure time to invent
   that algorithm. Measure at least three *different lawful* causal
   ablations with the same pack/Judge; a score drop caused solely by an
   invalid submission is not evidence of the claimed mechanism. The 85%-of-
   achieved-headroom rule is the generator's diagnostic, not a DOCX clause;
   retain a `revise` receipt even if an owner narrowly waives that diagnosis.
4. **Final scored images:** Before hours of model spending, check the exact
    Work/Judge source and pinned dependency identities that will be delivered.
    Online `apt-get` is allowed, but a pinned `FROM` with floating package
    repositories is not reproducible; independently rebuild without changing
    the recorded inputs when reproducibility is claimed. Verify
    exact Work/Judge context hashes and two same-identity successful build
    process receipts per role/architecture; an arbitrary hashed summary cannot
    certify reproducibility. The known pinned Python base uses a generated
    Debian trixie apt lock (snapshot date, five versions and both native index
    hashes); other bases require their own validated task-bound lock. The lock
    does not replace executed builds or image-inspection receipts. Also check
    public local feedback and Judge import/runtime equivalence, negative
     submission handling, work-private-data separation, bounded Judge startup,
     actual network/users/capabilities, score parity and five repeats.
     Probe the model's public edit path and documented local-feedback command
     under the **actual scored Work** UID, filesystem and host-controller
     environment. Image `ENV`, a source Dockerfile's `chown`, and a development
     scorer's temporary `LEAN_PATH` do not prove that the model-run image's
     direct tools and target file work. Determine whether the failed command
     was required or merely optional; test the supported route before
     concluding the task is lawfully unsolvable.
     Check architecture-pinned binaries or seccomp policy before native arm64
    tests. Evaluate NOP/reference and repeated fixed snapshots with the same
    SForge judge on arm64; do not duplicate model rollout or Rubric review on
    that host. Historical QEMU diagnostics keep their emulated label.
5. **Main-agent sign-off before rollout:** Bind Q1–Q7, causal difficulty and
   applicable RL/DOCX non-format checks to task/adapter/image SHA-256s and a
   real `reviewed_at` timestamp. Cite scored positive, negative, equivalent,
   repeated and runtime-isolation receipts, including failures/unknowns.
   Include an append-only `evidence/pre-rollout-contract-review.json` in the
   current `task-review` evidence paths; this supplemental matrix is a
   reviewer finding, **not** an automatically passed CLI gate. `task-review`
    may record non-blocking Q findings as `accepted` under the training-first
    owner policy; `ready` rejects only `fail`/`pending` findings and still
    requires evidence for every accepted exception. `rollout-check`
   already reads this review, and `rollout-collect` now rejects full-budget
   classification if the review is absent or its timestamp is after a run's
   `started_at`. A later inspection can diagnose a finished run but cannot
   retroactively create a pre-rollout approval.

When a scorer, ABI, evaluation sandbox, reward constant, task bytes or
scored-image semantics change, create a new frozen identity. Remeasure
NOP/reference, generic controls, ablations, five repeats and SForge parity;
do not transfer prior model runs. A task-only package edit that does **not**
affect those bytes must still be hash-audited and described accurately.

## Decide what “ready” means

`task-review ready` is **task-quality** approval at a particular frozen
identity. It is not a native ARM receipt, external resource
agreement, three-run score table or customer acceptance. Keep technical
statuses `pass / fail / incomplete`, owner decisions/waivers in separate
fields, and an audit row for every non-format DOCX item. When a mandatory
item is absent, do not invent an acceptance threshold or use a unit fixture,
QEMU run, author prose, or published answer to fill it. A missing negotiated
resource agreement or native host remains `incomplete` until evidenced or an
explicit **task-bound** owner decision changes the applicable delivery scope.
