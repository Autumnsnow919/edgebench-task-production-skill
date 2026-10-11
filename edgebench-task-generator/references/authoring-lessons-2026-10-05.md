# EdgeBench authoring lessons from the 2026-10-05 repairs

Read this before `design`, `generate`, `validate`, `measure` or a pre-rollout
review. These are reusable authoring controls derived from recent task repairs;
they do not replace the current owner scope, the task contract, or the
task-specific scorer.

## Make the task executable for the model that will actually run it

1. **Validate the first parser before spending model hours.** Parse `task.toml`
   with the recipient's parser or an equivalent strict parser. Values beyond
   its lossless integer range belong in quoted metadata strings. Keep the
   numeric value used by the Judge/JSON contract separately and test that the
   conversion is unambiguous. A preflight parse error means no task ran and no
   model score exists.
2. **Test the exact Work image, UID and command path.** Start the final Work
   image as the actual model UID. Attempt the documented edit, import the
   required libraries with the documented local command, run the public
   feedback command, and inspect the resulting filesystem and network. A
   Dockerfile that says `chown`, an image `ENV`, or a development scorer that
   sets a temporary path does not prove the model sees the same behavior.
   Classify a failing optional command separately from a broken supported path.
3. **Build what will be shipped.** A private local image name is not a
   recipient-pullable base. Use a public digest-pinned base, pin build
   packages/wheels, create the declared runtime user, build the shipped
   context on every applicable native architecture, inspect the image identity,
   and run the scored Work/Judge boundary. Construction network access and
   runtime no-network are separate assertions.

## Design the score before designing the prose

4. **Write the public contract and Judge contract side by side.** For every
   required input, output, unit, timeout, exception, hard rule and score
   component, identify the actual check. For every Judge check, identify the
   public requirement or a fair hidden extension of it. Hidden data must not
   be the only way to discover a core output schema or necessary tool.
5. **Make local feedback usable without leaking the answer.** The model should
   be able to compile, validate schema/feasibility, see actionable named
   failures and improve a submission inside Work. Keep private cases, strong
   references and reward internals outside Work. Test the feedback command as
   the model UID in the final image, not only from the authoring host.
6. **Use a real score ladder.** Execute the original model-visible starter,
   NOP, a lawful interior/generic control and the strongest achieved reference
   under the same scorer. If a prewritten cheap method is already near the
   achieved reference, treat it as a shallow-task signal and revise the task
   mechanism. Do not inflate difficulty with larger inputs, arbitrary hidden
   thresholds, waiting, noise or a moved anchor. For an `open-solution` task,
   a published per-case schedule is a scale/reference anchor; it is not an
   executable general-solver result.
7. **Define invalidity and partial credit explicitly.** Preserve the grader's
   real semantics: a case-level failure may yield a positive global score and
   `valid=false`; a missing Judge report is an unscored infrastructure event,
   not a model zero. Decide whether selection is `score_first` or
   `valid_then_score` before rollout and report both the selected score and the
   best valid score when they differ.
8. **Accept semantic alternatives.** Test at least one independently written
   correct alternative and one negative/shortcut artifact. Avoid requiring a
   reference source string, fixed internal architecture, file order or a
   private path when the task goal is semantic. Reject empty output, hardcoded
   known cases, reference lookup and verifier interference through actual
   Judge checks.

## Keep the model experiment comparable

9. **Freeze bytes before rollout.** Bind task input, instruction, scorer,
   Work/Judge contexts, images, model/provider/effort, budget, auto-eval rule,
   resource policy and Q1–Q7 review timestamp. Any scoring boundary or image
   change gets a new identity and new controls; old scores stay historical.
10. **Probe the provider, then monitor reality.** Check the exact model catalog
    and one small request before launching long runs. During runs inspect the
    actual PID and checkpoint. Preserve provider `completion_unknown` receipts;
    never blindly retry an ambiguously completed request. A resume is valid
    only when the original owned Work container still exists and all identity
    fields match. If it does not, use a new full-budget run only with explicit
    authorization.
11. **Record all submissions and curves.** Keep every attempted index, numeric
    Judge report, unscored event, archive hash, best-so-far curve and 2h/4h/6h
    or task-specific checkpoint. Derive the mean and README tables from these
    records. Do not combine old 61/59/69-style historical tables with a new
    51/68/41 cohort, and do not call three interrupted runs three complete
    trials.

## Package evidence without rewriting results

12. **Separate raw evidence from customer summaries.** Keep original
    checkpoints and reports immutable in private evidence. A customer-safe
    summary may omit owner/PID/provider endpoint fields, but must preserve
    score, `valid`, selected submission, best-valid submission, final scored
    submission and hashes. Never edit a Judge report or checkpoint to make a
    quality review pass.
13. **Repackage after the last edit.** Use an allowlist; scan text, binaries and
    decompressed submissions for credentials, private host paths, internal
    endpoints and other-customer data. Re-extract the final ZIP and check every
    member's bytes, safe path, file type, allowed mode, links and manifest.
    `metadata/SHA256SUMS` must itself be in the ZIP when the package claims to
    contain that manifest. Recompute the outer ZIP hash only after all README,
    QC and metadata changes.
14. **Write the repair reply.** Each repair gets a separate Markdown response
    outside customer bytes. It lists every finding, source evidence, exact
    change, executed validation, remaining incomplete scope and final hashes.
    A document saying “fixed” is not evidence; the reply is the audit trail
    that makes the repair reviewable.
