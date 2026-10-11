# QEMU arm64 parity and independent Q1–Q7 review

Read this when preparing cross-architecture diagnostics or signing a frozen
EdgeBench task's quality review. These are **two distinct receipts**: scoring
parity measures the Judge's behavior, while Q1–Q7 asks whether the task and
its execution boundary are trustworthy. Neither substitutes for three model
rollouts or the owner's native-arm64 requirement; source/rights/dedup checks
are not required by the current owner. ARM64 does not repeat the model rollout
or Q1–Q7 review, but does perform the other applicable native checks.

## SForge arm64 under QEMU: diagnostic only

SForge 1.1.0 `eval` has no `--platform` switch. To compare scores without
modifying a model-scored task, create an **isolated diagnostic copy** of its
task JSON whose sole task-field change is `platform: linux/amd64` to
`linux/arm64`. Give its `BENCHMARK.yaml` a distinct benchmark name so new
base/Work/Judge image tags cannot overwrite images used by live trials. Keep
the original task bytes and scorer inputs frozen. Record both JSON SHA-256s,
the precise changed field and the two benchmark names. A changed platform
necessarily gives the diagnostic JSON a different hash: call this matched
**scoring semantics**, not the same task JSON.

The diagnostic SForge CLI entrance is the ordinary build/eval pair; the
`--tasks-dir` here points only to the platform-adjusted copy:

```bash
sforge --tasks-dir DIAGNOSTIC_TASKS --log-dir ISOLATED_LOGS build --task TASK_ID
sforge --tasks-dir DIAGNOSTIC_TASKS --log-dir ISOLATED_LOGS eval --task TASK_ID --archive IDENTICAL_SNAPSHOT.tar.gz --run-id UNIQUE_QEMU_ID --json
```

Keep the original amd64 run/log directories read-only throughout this probe.

1. Record `uname -m` for the physical host and `platform.machine()` inside
   a Docker container run with `--platform linux/arm64`. `aarch64` inside an
   `x86_64` host establishes emulation, **not native execution**.
2. Run `sforge build --task TASK_ID` in the isolated tasks/log directory.
   Retain argv, exit code, elapsed time and stdout/stderr digests. Inspect
   **all three** resulting image architectures (`base`, `work`, `judge`);
   builder support for arm64 does not prove an image was actually built for
   arm64. If the local builder tags an amd64 base as arm64, stop and diagnose
   rather than reporting arm64 parity. In the measured Docker-classic case,
   an isolated **diagnostic** base was rebuilt from SForge's generated base
   context with `docker buildx build --platform linux/arm64 --load --tag
   ISOLATED_BASE_TAG GENERATED_BASE_CONTEXT`, checked with
   `docker image inspect`, then the Work/Judge images were built through
   `sforge build --task`. Manual buildx alone is not SForge build evidence.
   A build proxy inherited as `127.0.0.1` is also not reachable inside the
   build container; inspect the actual apt/pip error and test direct mirror
   access before retrying without that proxy. Do not edit the shared SForge
   installation or production image tags to make a diagnostic pass.
3. Score the **same archived bytes** through SForge on amd64 and emulated
   arm64: a valid NOP, a fixed reference (or documented open-solution
   anchor), and at least one lawful interior snapshot. Bind each archive
   SHA-256 to its own process/report receipt. Compare validity, continuous
   score, raw metrics, named checks and domain outputs (for example case
    costs), not just the final number. Record any repeat noise and use
    appropriate diagnostic timeouts; QEMU latency is not native Judge timing.
   If the scored artifact is a **program**, additionally execute that exact
   submitted program through the real sandbox/import and Judge entrypoint.
   A published per-instance answer scoring 100 proves arithmetic on those
   cases; it cannot establish that a general solver or architecture-specific
   seccomp policy runs on arm64. Record oracle and program receipts separately.
4. Inspect runtime containers as well as image contents. In particular,
   `internet:false`/`--disable-internet` may constrain **Work** without
   constraining **Judge**. Observe the ephemeral Judge's actual
   `NetworkMode`, user and dropped capabilities, keeping credentials and
   full container environment out of receipts. A default `bridge` Judge is
   not proven offline even when the work container is isolated.

Report the result as `SForge QEMU-emulated arm64 parity`. Keep
`sforge.native_cross_architecture` and native `measure-image` rows
**incomplete** until the same frozen task/snapshot is scored on actual arm64
hardware. Never reinterpret a QEMU receipt as a native one to unblock
`sforge-audit` or formal packaging. A measured example is
`edgebench-explore/authoring/hospital-build-solver-v2/evidence/qemu-arm64-20260928/`:
three fixed snapshots matched amd64 on score and structured metrics, but the
host was x86_64; those numbers are example evidence, not a universal
threshold.
For the **historical owner-reviewed 2026-09-29 sample**,
`owner-delivery-scope-2026-09-29.md` accepts such a measured SForge QEMU
result as its ARM64 comparison. Record `arm64_comparison=owner_accepted`
while keeping the execution receipt `emulated_arm64`; this does not turn the
native-only legacy auditor row into a real native-host observation.
The owner's later instruction for new Skill-generated evidence requires real
SForge scoring on the native arm64 host. The executable owner-sample profile
validates `native_arm64` receipts and does not accept emulated receipts for a
new task identity.

## Independent main-agent Q1–Q7

The **main reviewer** reads the English model-visible task, pinned source,
Work/Judge contexts, scoring formula and raw process receipts. Do not promote
an author's self-review or infer seven passes from NOP/reference/unit tests.
For the *same frozen task hash*, record each category as `pass`, `fail`, or
`pending` with a bounded finding and hash-bound evidence. After a scorer or
task-byte change, review the new identity; past model scores do not certify
it. Separate task quality, causal difficulty and the Judge's reward.

| Check | Minimum independent question/probe |
|---|---|
| Q1 clarity | Can an uninvolved reader locate the inputs, output, units, hard/soft boundaries and fallback behavior from the public instruction? |
| Q2 alignment | Do Work and Judge use the declared artifact and the same frozen formula/case constants? Check source hashes and recompute real report scores; disclose stale provenance IDs even if they do not change scoring. |
| Q3 lawful solvability | Do a valid starter and available offline tools permit an achievable improvement inside budget? Verify with real scoring, not only an image declaration. |
| Q4 grounded truth | Check the reference's hash, real validity and scorer output. Where `open-solution` uses a measured static upper, prove it is *attainable*, not globally optimal. Independently recompute metrics from raw case outcomes or compare with a separately grounded validator; name any provenance still unknown. |
| Q5 error discrimination | Score valid NOP/partial/reference and targeted malformed, unknown-ID and hard-infeasible variants. Check the grader's **actual** `valid` and score semantics: some multi-case reports can be globally `valid=false` yet earn legitimate positive partial credit on other cases; an infrastructure event without a report is neither that credit nor a measured zero. Empty/broken submissions must not masquerade as reference success. |
| Q6 lawful alternatives | Submit a semantically equivalent reordering/representation and a distinct feasible partial solution when the task permits one. Check that the scorer accepts them; one variant does not prove every possible boundary case. |
| Q7 repeatability | Rescore the *identical* archive at least five times, examine range versus headroom, rederive the formula from real reports, and check timeout/parse/error handling. State whether timing is native or emulated. |

Historical `hospital-build-solver-v2` reviews contain Q1–Q7 arithmetic and
positive/negative controls as well as a Q8 inference the owner does not adopt.
Do not reuse that Q8 conclusion. Check DOCX Work/Judge no-network and private
answer protection against the actual runtime separately; Docker `bridge` or
root identity alone is not a demonstrated egress or answer-leak path.

Use `task-review WORKSPACE --input REVIEW.json` for pipeline-native tasks
after reviewing the latest `difficulty-pack`, `difficulty-card`, SForge
parity and each Q1–Q7 finding. Only seven confirmed `pass` findings can be
`ready`; a `fail` or `pending` stays explicit. For externally authored SForge
tasks without a pipeline workspace, retain a separate reviewer receipt
bound to the original JSON/source hashes and reflect it in the independent
inspection report. Do not alter a frozen Judge or relabel model runs to
repair a failed quality check: fix the runtime and recheck on its actual
task/image identities, or issue a new task identity when scoring changes.
