# Authoring pitfalls measured on the real pipeline (2026-09-26)

Concrete failure modes hit while driving `topify_pipeline.py` with an
`authored_context` family. Read before `init`/`generate`/`measure`.
Read `authoring-lessons-2026-10-05.md` for updated pre-rollout checks and
`repair-evidence-and-reply.md` when an observed problem needs correction.

## Multi-file tasks through the single-file snapshot

The adapter submits exactly one declared file. For a task whose real artifact
is a small set of source files (e.g. 12 modules), the measured working pattern
is a **deterministic tar snapshot**:

- Ship a `pack_submission.py` in the agent workspace that tars exactly the
  declared target paths with sorted entries, `mtime=0`, numeric `uid/gid=0`,
  empty uname/gname, fixed modes — identical content must give identical bytes.
- Declare `artifacts = ["/workspace/submission.tar"]` and
  `snapshot_artifacts` likewise; the starter artifact is a prebuilt tar of the
  pristine files inside `environment/workspace/`.
- `tests/evaluate.py` unpacks the tar, rejects anything outside the exact
  allowed path set (no symlinks/devices/`..`), and feeds the extracted tree to
  the real scorer.
- `nop/nop.sh` and `solution/solve.sh` each run with an empty `$WORKSPACE` and
  must write `$WORKSPACE/submission.tar`; they may read task-relative files
  (`cwd` is the task root), e.g. `environment/workspace` or `solution/oracle`.

## Genuine NOP and a useful measured ladder

NOP must submit the **unchanged model-visible starter**, including a
deterministic tar of its original files when the interface is multi-file.
NOP and starter can have the same baseline score; measure improvements using
separate lawful interior, generic and reference submissions. If a legacy
`measure` check requires `nop < starter`, treat that as a tool/contract
alignment issue and preserve the failed diagnostic. Do not substitute empty
files, mark invalid output as a valid baseline, or modify the starter solely
to manufacture an inequality. Missing-output controls are useful negatives,
but are different from the actual NOP.

## Dockerfile constraints enforced by `build`

- Every line is parsed with `shlex.split`: **no backslash line continuations**
  ("No escaped character"). Write single-line RUN commands.
- Buildx respects the frozen `task.toml` build policy: current generated tasks
  declare justified `public` access and can fetch declared `apt`/`pip` build
  dependencies; old tasks declaring `no-network` still build with `--network
  none`. Pin their versions/indexes and record real rebuild receipts. This
  build permission does not enable outbound Work/Judge runtime access.
- Check the selected runtime's read-only filesystem and temporary-directory
  policy. A scorer using `tempfile` needs an allowed writable scratch path.
  Use the runner's bounded tmpfs or explicit scratch mount and set the
  evaluator subprocess's temporary directory accordingly. The strict host
  backend rejects image-declared volumes, so adding `VOLUME /tmp` is not a
  general fix. Test under the actual Judge UID/capabilities and limits.

## Manifest and regeneration hygiene

- `manifest.json` file records must be **string-sorted** by relative path:
  `sqlglot.png` sorts before `sqlglot/__init__.py`; `Path` sorting orders
  differently and is rejected.
- `generate` refuses to overwrite: after editing `authoring/task-source`,
  create a new owned revision workspace, sign its manifest, regenerate, and
  revalidate. Preserve the previous generated task and evidence until the
  new identity is checked; never delete the only frozen evidence copy.
- Any task byte change rebinds `task_sha256`: update `task_sha256` inside
  `generic-controls.json` and `difficulty-card.json`, then rerun
  `run-controls`, `difficulty-pack`, `difficulty-card`, `candidate-audit`.
  Stale-hash receipts make `candidate-audit` report `incomplete`.
- `difficulty-card` evidence `contains` strings must occur on a **single line**
  of the referenced task file; wrapped Markdown lines do not match.
- `measure` exits nonzero when the verdict is `MEASUREMENT_PARTIAL` (the
  image/cross-arch/batch checks stay `incomplete` by design at this stage).
  Do not chain it with `&&`; run the follow-on steps separately.

## Cheap-control interpretation

`candidate-audit` `revise` is a heuristic diagnostic. A control that saturates
only by reading the pinned mirror on the authoring host (e.g. extracting
published upstream answers) is not reachable by an offline agent; when the
main reviewer proceeds on such an identity anyway (owner-directed, backed by
historical frontier evidence), record the reachability argument and the
counter-evidence explicitly in the review rationale. An agent-reachable cheap
saturation remains a hard stop for that identity: investigate the next
same-family causal revision before `seed-shelve`; do not substitute bigger
inputs or a shifted anchor for a tested mechanism.
