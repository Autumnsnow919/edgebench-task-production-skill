# Current owner scope for fresh EdgeBench production

Read this before interpreting older sample dossiers or source-contract notes.

- Normative documents are `Meta Vendor Data Spec V3.0.zip` and the current
  **Requirement — V1** in `EdgeBench Vendor Dataset Requirements.md`. The
  appended archived draft and older V2.4 QC format opinions are not current
  authority. The user chooses `sforge-only` or `harbor` before rollout; the
  selected format's clauses are applied and the other format is recorded as
  not applicable.
- Customer handoff is format-specific: SForge-only uses the English SForge ZIP
  and separate English QC report; Harbor uses the unpacked Harbor package and
  its separate English QC report. Chinese README and QC translations live
  under the private owner-review tree only. Packaging is not a Smoldata/S3
  upload, finalize or recipient acceptance.
- Source declarations, four-catalog dedup, redistribution confirmation,
  mandatory simultaneous `setup_cmds`/`image_tag`, and Q8 task-quality review
  are `not_required_by_owner`. These are not measured passes. The owner
  reaffirmed these exclusions while requesting all other applicable Meta V3
  and EdgeBench V1 checks; freeze actual input/scorer/snapshot bytes and hashes.
- Main review signs Q1–Q7 and causal difficulty before frontier rollout. ARM64
  executes applicable Work/Judge builds, NOP/reference, fixed-snapshot repeats,
  native scoring and cross-architecture comparison. ARM64 does not duplicate
  the frontier-model rollout or main Q1–Q7 review. Historical emulated results
  stay labeled as such and cannot certify native task scoring.
- Work/Judge construction may fetch digest-pinned bases and version-pinned
  packages online; offline OCI images or wheelhouses are not required in the
  customer ZIP. Runtime and scoring remain no-network and need executed
  isolation evidence. The default known Debian base has a frozen apt snapshot
  and native index hashes; other bases need their own task-bound apt lock.
- The selected task budget is at least three hours; twelve is preferred.
  Preserve real NOP, interior, achieved reference or explicit open-solution,
  five same-reference evaluations, a real batch NOP scan, and at least three
  comparable complete SForge frontier trials per frozen identity. Retain
  task-bound resource agreement, reproducible dual-platform build receipts,
  structured score-time curve and per-file data SHA-256. Do not turn an
  incomplete or failed check into success using fixtures or a waiver.
- The active installation supplies a configured customer output root and a
  separate private-review root. Each parallel worker receives an exclusive
  `agent-workspaces/<job-id>/` directory under its campaign; only the main
  coordinator integrates shared state.
