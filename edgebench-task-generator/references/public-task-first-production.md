# Public-task-first production

Use this route when the owner wants to start from an already public EdgeBench
task, its public task definition, public Work/Judge harness or an approved
trajectory/score report.

The official public set is a **parent/reference corpus**, not a new customer
task pool. Public definitions and scores can be used to inventory the objective,
feedback loop, controls, model shortcut and score-time curve. The parent bytes,
hashes and trajectories remain historical evidence. Never edit them, relabel
their scores, or reuse them as the new task's trajectory or freeze evidence.

Before creating a child Seed, pin the catalog revision and task-definition
bytes in a private `parent-analysis` record with the public task ID, definition
hash, upstream projects, public trajectory references, observed shortcut/gap,
evidence, proposed new mechanism and new input/source identity. Mark evidence
`observed`, `replayed`, `inferred` or `unverified`; a leaderboard row is not a
trajectory.

The child must change the model's actual reasoning burden through a
source-grounded mechanism: a new held-out input distribution, a different
architecture/compatibility contract, a cross-version or cross-component
dependency, a new independent truth source, or a materially different feedback
loop. It must have a new task identity and its own Work/Judge/evidence root.

Reject renamed tasks, copied public patches/hidden cases, cosmetic extra rows,
hidden thresholds, moved reward ceilings, or a different task on the same
upstream/dataset with the same verifier semantics. Public upstream identifiers
are used for collision/decontamination review even when a catalog/license
dossier is out of scope.

For each child record the parent weakness, evidence, causal mechanism, generic
control, strong/reference control, local feedback and expected headroom. Produce
r0/r1/r2 with parent hashes and distinct mechanisms. The parent trajectory may
explain design; it cannot become r0/r1/r2, a score, or a rollout receipt. Run
the new child's NOP/interior/strong/repeats and Work/Judge checks on its own
bytes. Promote only after overlap review, measurable headroom, a new identity
and the selected delivery contract are bound.
