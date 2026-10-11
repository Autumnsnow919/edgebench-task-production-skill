# Same-family causal hardening (revision 0 → 1 → 2)

This is an **authoring contract**, not a Topify score cutoff. A Seed's first
candidate is revision 0. Two separately measured causal revisions of the
**same task family** are required before a source-derived candidate may be
promoted. The controller dispatches one revision at a time after the previous
identity has a real `candidate-register` receipt with `revise` or
`review-needed`. A worker failure, an unmeasured prototype or an `incomplete`
audit does not consume a measured hardening round.

## What stays the same

Keep the pinned Seed, source-grounded professional objective, principal
submitted artifact and its semantics. `candidate-register` freezes the
upstream source URL/revision/artifact digest separately from mutable rights
review and the machine-local mirror path; a changed pinned source requires
main review under a different family identity. Use a new task ID and an unused isolated
workspace for each revision. Never edit a previously scored task. A changed
scorer, baseline or attainable reference requires a new frozen identity and
fresh NOP/interior/strong, five-repeat, controls and rollout evidence; never
rescale an old model score after seeing the result. Scores from different task
identities or model/provider/budget configurations cannot be averaged.

## What changes

Diagnose the previous candidate's cheap-control, feedback and score-time
receipts. Add one source-justified **causal constraint or dependency** that
addresses that specific weakness; the second hardening must use a distinct
mechanism. This might couple patient admission/theatre capacity with nursing
continuity, or tie parser, AST and generator obligations together. Merely
adding cases/files/records, obscuring a public scoring rule, sleeping, changing
an upper bound or moving to a different task on the same dataset does not
qualify. If no honest mechanism exists, return `blocked` to the main reviewer;
do not fabricate a successful revision.

## Receipt in the child's workspace

The author writes `authoring/hardening.json` (outside the scored task bytes)
with exactly this schema, replacing the sample IDs and claims with real ones:

```json
{
  "schema_version": 1,
  "seed_id": "combinatorial-optimization/source/lead",
  "revision_index": 1,
  "parent_task_sha256": "<64 lowercase hex characters from the registered parent>",
  "parent_task_id": "topify/source-original-v0",
  "causal_delta": "Admission changes now compete with theatre and downstream nurse capacity rather than being independently movable.",
  "source_grounding": "The pinned source instance defines shared theatre and nurse shift constraints.",
  "failure_observable": "The prior first-fit control cannot keep all three stage constraints feasible while preserving its old score.",
  "mechanism_id": "admission-theatre-nurse-coupling",
  "evidence": ["evidence/difficulty-probes/attempt-0001/summary.json"]
}
```

The example's words do not prove the change. The named mechanism must have a
**positive, current-task-bound measured ablation** in `difficulty-pack` and
`candidate-audit`: the receipt path and SHA-256 must match an actual
`ablation_probes` entry. Re-measure in the new task: lawful NOP, available
starter, nonzero interior when possible, achievable strong/reference, fixed
reference repeated five times, and domain-generic controls. Check that a
shortcut is actually available inside the delivered Work image; an author-host
copy of a published solution is a separate contamination probe, not an
agent-reachable cheap baseline. `run-controls` currently executes on the
authoring host; its scope is not an offline Work-image execution receipt.
Before claiming a Work-level shortcut, retain a separate real Work-process
test or leave reachability `incomplete`. A flat zero-score curve is an `incomplete`
learnability signal, not evidence that the task is sufficiently hard.

`candidate-register` checks the lineage and receipt binding but cannot
certify the *semantic* truth of a claimed mechanism. The main reviewer must
read the changed Work/Judge bytes and the matched intervention before Q1–Q7
review or rollout. After revision 2, no fourth distinct measured identity can
be registered under that Seed; promotion still requires pinned technical
task inputs, agreed resources and real SForge delivery evidence. The owner's
source declarations, written redistribution and four-catalog dedup are
`not_required_by_owner`, never inferred passes.

Legacy campaigns with any hand-authored candidate lacking a pinned-source
fingerprint, or with several hashes but no revision index/parent chain, are
**not silently promoted** to r0/r1/r2. Preserve their original receipts and
obtain an explicit main-agent lineage review rather than rewriting history.
