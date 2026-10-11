# SForge task review: quality, difficulty, and scoring

This is the production team's **pre-rollout authoring review**, not a native
SForge `rubric` field. The installed SForge 1.1.0 task format names a judge
command, parser, score direction, and selection rule; its structured `details`
are judge results, not a rulebook for task-quality review. Keep three distinct
objects: (1) main-agent Q1–Q7 task-quality review, (2) difficulty and long-loop
diagnostics, (3) the executed SForge judge's continuous task score.

The seven retained task-quality criteria are adapted from the external
README guide; the owner removed Q8 from the current review. DOCX runtime
isolation is assessed separately, not scored as Q8:

| ID | Evidence the main agent must inspect |
|---|---|
| Q1 | Public objective, inputs, outputs, units, boundaries and exceptions are unambiguous. |
| Q2 | Instruction and verifier agree in both directions; no undisclosed essential constraint or untested core requirement. |
| Q3 | Offline input, tools, resources and time make a lawful solution attainable. |
| Q4 | Independent grounding of truth/reference and domain assumptions; shared bugs are not independent proof. |
| Q5 | Judge distinguishes NOP, broken, partial and strong work; adversarial negatives cannot earn full credit. |
| Q6 | Semantically equivalent lawful outputs are accepted; tolerances and restrictions are justified. |
| Q7 | Repeated scores, timeout, error and skipped checks are interpreted correctly. |

Record each item as `pass`, `fail` or `pending`, with a concrete finding and
workspace-relative evidence file. `ready` requires all seven confirmed `pass`;
missing evidence is `pending`. This authoring rule does not turn the quality
rubric into the judge score or assert that SForge itself defines a rubric.
For bounded positive/negative SForge probes, independent formula checks and
**actual Work/Judge** network/user observations, read
`qemu-and-independent-review.md` before signing Q1–Q7. In particular, an
emulated arm64 score match is not an isolation check or a native architecture
receipt. A required no-network Judge boundary remains a separate DOCX check;
judge user/network settings alone do not establish whether egress was possible.
Use `pre-rollout-quality-gates.md` to apply the EdgeBench DOCX and any
confirmed **non-format** RL clauses to the same frozen task/adapter/images
*before* starting model hours. Put the applicable contract checklist under
`evidence/`, cite it in Q1–Q7 `task-review`, and preserve its SHA-256 and
review timestamp. The separate owner waiver/release decision must never be
reported as a technical Q pass or backdated to a previously started run.
`difficulty-probe` executes the task scorer twice with distinct lawful
snapshots, preserves process receipts and reports the score effect. One probe
is not enough. `difficulty-pack` binds a machine-readable set of declared
causal mechanisms to receipted probes. Each mechanism has a unique slug, one
causal dimension (`objective-decomposition`, `dependency-chain`,
`parallel-branch-join`, `shared-resource`, `conflicting-objective`,
`state-coupling`, `identifiability`, `generalization`, `sequential-interleave`,
or `error-recovery`), a testable evidence question, declared integrated and
ablated snapshots, a causal claim, an expected failure observable, and an
anti-gaming check. `difficulty-summary` rechecks the latest pack, its immutable
attempt receipt, each probe's raw report/process hashes, snapshot identity and
score effect. The summary reports measured mechanisms, per-mechanism score
effect, per-mechanism fraction of measured NOP-to-anchor headroom, and combined
effect. These are diagnostics, not a substitute for human difficulty judgment.

The design screen still requires `coupling_mechanism`, an `ablation_plan` with
at least three distinct ablations (name, removed mechanism, expected score
effect), and a `cheap_baseline` (method, domain fit, and the `control_id` under
which it will be measured). `candidate-audit` refuses `review-needed` unless
the current pack has at least three distinct measured mechanisms against the
current task bytes, each removing at least 2% of measured NOP-to-anchor
headroom and jointly covering at least 10%, and the declared cheap-baseline
`control_id` appears among validated generic controls. Reports produced with
looser floors never become the current diagnostic. A cheap control closing 85%
or more of the headroom within 1% of the interaction budget yields `revise` on
its own evidence without waiting for the mechanism pack. Before a `ready` review, the task must also have a current `difficulty-card`.
The card counts concrete **traps**, **dependencies**, **constraints**, and
**interleavings** (dependency×constraint interactions). Every counted item must
cite exact text/code evidence from the current task bytes; interleavings must
reference declared dependency and constraint IDs. The card reports counts and
interleaving density, but it is still not a score: it is combined with the
mechanism pack, cheap controls and rollout evidence. See
`references/difficulty-baseline-2026-09-26.md` for the public EdgeBench baseline
scan used to calibrate review.

For a `ready` review
the main agent must cite the current pack and independently explain what each
mechanism removed; different scores by themselves do not establish a deep
dependency mechanism.
The author supplies evidence; the **main agent** checks it and calls
`task-review WORKSPACE --input REVIEW.json` after SForge parity. The review
file contains `schema_version`, `task_sha256`, `reviewer_role: "main-agent"`,
`reviewer_id`, `decision: "ready|revise|reject"`, `rationale`, `quality` and
`difficulty`. Under `quality`, Q1–Q7 each have `status`, `finding`, and
workspace-relative `evidence` paths. Under `difficulty`, provide `mechanism`,
`structure: "interleaved-graph|domain-specific"`, `feedback_loop`,
`baseline_comparison`, `causal_probe`, `revision_strategy`, and `evidence`.
Use `examples/task-review.json` as a pending template; its strings and evidence
paths are **placeholders**, not passed rubric items.

## Distinguishing difficult work from decorative complexity

Public EdgeBench examples include `borden_source_inversion` (geometry and
transport calibration), `ann_vector_search_qps` (recall constrained QPS),
`lean_analysis_proofs` (independent lemmas plus build integration), and
`jagua_nesting_optimization` (geometric packing search). Their local task
metadata lives under `edgebench-explore/tasks/`. Their 2h–12h score curves
show both long-tail improvement and early plateaus. These examples illustrate
different mechanisms; copying their prompts, data, or objectives is not an
original Seed.

For each candidate, draw the causal graph in the task review:

1. State prerequisite steps, independently solvable branches, resource sharing,
   joins, rollback/validation points, and scored outputs downstream.
2. Show a matched probe that changes *one* claimed dependency and changes a
   verifier-visible outcome. If branches are actually independent, don't
   claim an interleaved graph. In inverse problems or games, describe equivalent
   genuine difficulty (identifiability, sequential observations, policy tradeoffs).
3. Compare a legal NOP, shipped starter, simple generic method, targeted
   partial fix, and strong-achievable reference using the same environment.
   Record raw metrics, validity, score noise and remaining headroom.
4. Show feedback that distinguishes failure mechanisms and lets a model repair
   without revealing held-out answers. Measure scoring latency relative to the
   hours-long interaction budget.

**Reject or revise** on an existing public solution, NOP near
full credit, a generic method saturating the strong result, a score cliff, an
unreachable upper, a causal link with no measurable effect, noisy scoring,
private-only feedback, or a task solvable just by waiting. Do not confuse a
flat curve with automatic failure: diagnose validity, scoring errors, feedback,
budget and whether work actually targeted the claimed bottleneck.

If the first candidate proves too easy, the author proposes a concrete new
causal constraint (for example overlapping joins with shared capacity and
rollback; robust calibration across coupled experiments; multi-goal proof
dependencies), explains its business/scientific basis, and **repeats** NOP,
baselines, strong measurement, probes, score-repeat tests and SForge parity
on a newly frozen task identity. The production loop requires two measured,
source-grounded same-family hardening rounds after the initial candidate
before promoting a real task; each must name its parent and newly tested
mechanism. Long model rollouts are reserved for a reviewed final identity,
not automatically spent on every shallow prototype. If no honest stronger
mechanism exists, report a blocker instead of inventing two rounds. Adding
record count, artificial noise, arbitrary hidden thresholds or waiting is not
a substitute. After both genuine rounds, shelve/switch a persistently shallow
Seed. Never mix scores from the old and revised identity.

## Rollout diagnosis

The one-shot `monitor-tick`/`monitor-batch` commands read each run's scored
submissions and write one immutable notification per new score/signal state.
Signals for invalid report, no valid submissions, early saturation, lack of
later activity/growth, or early termination are **investigation prompts**, not
automatic rejection. Compare best-so-far gain with five-repeat score variation
and with the frozen baseline-to-upper span. Inspect whether there are legal
improvements left, whether feedback was actionable, whether a judge or model
execution failed, and whether scores reflect comparable task/config/budget.
Only the main reviewer decides continue, revise, or reject; never edit a task
or stop an active run automatically. A revised task requires fresh run IDs and
its own three complete fixed-config runs.
