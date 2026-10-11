# EdgeBench Seed Sourcing Guide

## Purpose

This is an authoring guide for finding task candidates under the original
Topify DOCX. It does not add acceptance requirements. This owner excludes
source declarations, catalog dedup and redistribution authorization from the
current campaign; technical task-input hashes, environment, scoring, build,
resource and packaging checks still apply.

An EdgeBench seed is not a title, repository, issue, dataset, paper, or story.
It is a proposal with enough grounded material to build and falsify an
iterative task:

```text
Seed = real workflow and consequence
     + pinned source/data/generator
     + snapshot artifact
     + continuous raw objective
     + local feedback channel
     + natural improvement levers
     + achievable-upper strategy
     + offline verifier plan
     + content hashes for actual task inputs
```

The seed is useful only if an agent can submit a legal partial artifact, learn
something actionable, improve it, and repeat this process for hours. Complexity
without a learnable loop is not an EdgeBench seed.

## Campaign Funnel

For a 100-task campaign, use the DOCX category distribution as the default
planning vector, subject to agreement with Topify:

| Category | Final planning target | Initial leads at 8x | Screened at 4x | Executable prototypes at 2x |
|---|---:|---:|---:|---:|
| Systems and software engineering | 26 | 208 | 104 | 52 |
| Scientific problems and ML | 25 | 200 | 100 | 50 |
| Professional knowledge work | 18 | 144 | 72 | 36 |
| Combinatorial optimization | 12 | 96 | 48 | 24 |
| Formal mathematics and theorem proving | 10 | 80 | 40 | 20 |
| Interactive games and simulators | 9 | 72 | 36 | 18 |
| Total | 100 | 800 | 400 | 200 |

The multipliers are capacity-planning defaults, not acceptance thresholds.
High-attrition categories may need more leads. Do not lower task quality merely
to fill an exact percentage.

The funnel is:

```text
SOURCE_LEAD
  -> TECHNICALLY_PINNED_PROVISIONAL (--for-prototype --owner-sample)
  -> LOOP_HYPOTHESIS
  -> EXECUTABLE_MICRO_PROTOTYPE
  -> MEASURED_CANDIDATE or SEED_SHELVED
  -> source-specific task-family design and SForge work/judge preparation
```

Reject cheaply before writing a full verifier or spending frontier-model time.
Keep rejected cards and reasons so later scouts do not rediscover the same bad
lead.
The category targets apply to the **campaign as a whole**, not to one task.
`domain-plan` turns actual screened/provisional Seeds and campaign shortfalls
into the next small, multi-domain authoring batch. A source registry entry is
only a place to scout; it does not mean that category already has a viable
Seed, plugin, executable task, or accepted delivery. Each category's distinct
artifact, local signal, generic cheap controls and failure patterns are
recorded in `domain-production.json`.

## Acquisition Channels

### Upstream Change And Conformance Mining

Mine pinned open-source repositories, release notes, issue trackers, fixed bugs,
performance regressions, compatibility matrices, protocol standards, and
conformance suites. Good transformations include reimplementation, migration,
porting, recovery, compatibility repair, query or compiler optimization, and
reconstruction from a behavioral contract.

Do not copy a public patch into the task. A public issue with a searchable final
solution has high contamination risk. Prefer a pinned pre-change system plus a
new input distribution, another target architecture, a standards-derived
behavioral checker, or a cross-version combination that preserves the real
mechanism without asking for the known patch verbatim.

### Public Data And Reproducible Research Mining

Mine public scientific datasets, government data, benchmark instances, papers
with released code/data, and reproducibility packages. Convert them into an
offline objective such as prediction quality, inverse-problem residual,
calibration error, physical consistency, robustness across held-out instances,
or agreement with independently reproduced results.

Pin dataset bytes and preprocessing needed for scoring identity; this owner
does not require a separate license or redistribution dossier.

### Standards And Expert-Workflow Mining

Mine public regulations, standards, filing schemas, audit procedures, clinical
or educational guidance, public forms, data dictionaries, and official example
records. Extract a real role, decision, structured deliverable, and material
error consequence. Use public or author-generated records without personal or
confidential data.

Professional knowledge tasks need objective checks over calculations,
cross-document consistency, evidence traceability, schema validity, and
material business rules. A subjective essay score is not enough.

### Original Generator And Simulator Mining

Start from a deterministic formal model, instance generator, simulator, or game
engine when public source reuse is unnecessary or contamination is high. This
channel is strong for optimization, control, recovery protocols, games, and
some scientific inverse problems.

The generator must define truth independently of model prose, produce small
instances that can be cross-checked exactly, and support held-out instances.
Synthetic data must still represent a coherent real mechanism; random records
with an arbitrary score are not a real-world task.

### Formalization Gap Mining

Mine mathematical papers, Lean/mathlib issue and blueprint lists,
dependency graphs, informal proofs not already formalized, and reusable theorem
families. A good seed contains multiple independently checkable goals and local
Lean compiler/proof-state feedback, so partial progress produces a continuous
aggregate score.

Search theorem names, statements, dependencies, and upstream projects to avoid
repackaging an existing formalization under another name.

### Simulator Scenario Mining

Mine open-source games, Gymnasium/PettingZoo/OpenSpiel-style environments,
transport or logistics simulators, and domain simulators with deterministic
seeds. Create scenario distributions and score policies or agent code across
held-out episodes. The snapshot is code/configuration, not a live process.

Avoid tasks whose score mainly rewards elapsed time, nondeterministic server
state, or privileged access to simulator internals unavailable to the agent.

## Domain Source Matrix

| Category | High-yield source pools | Promising task shapes | Natural local feedback | Main seed risks |
|---|---|---|---|---|
| Systems and software engineering | Permissively licensed infrastructure repositories, standards/RFC test suites, compiler and codec corpora, database/storage/network projects, release regressions | Reimplement a subsystem, port across runtime/architecture, repair recovery semantics, optimize throughput/latency, reconstruct protocol behavior | Build logs, public tests, differential traces, profiler output, conformance failures | Public patch leakage, one-test cliffs, architecture-specific dependencies, huge builds |
| Scientific problems and ML | OpenML/UCI and domain repositories with redistribution rights, government/scientific archives, reproducibility packages, licensed simulation data | Inversion, calibration, forecasting, anomaly detection, model/solver tuning, physical consistency repair | Residual plots, validation metrics, constraint violations, held-out development split | Noisy metric larger than headroom, GPU lock-in, theoretical upper, dataset leakage |
| Professional knowledge work | SEC and other government filings, official open-data portals, standards/regulations, public templates and manuals, author-generated records | Reconciliation, risk calibration, audit, compliance mapping, evidence-backed structured reports, educational assessment construction | Schema checks, cross-footing, revision notes, formula and consistency checks | Subjective judging, unclear legal basis, personal data, presentation points overwhelming substance |
| Combinatorial optimization | OR-Library, TSPLIB, MIPLIB, MiniZinc, DIMACS, scheduling/routing/packing generators, licensed domain simulators | Routing, packing, allocation, graph problems, scheduling, solver construction, online adaptation | Feasibility violations, objective value, lower/upper bounds, instance-level diagnostics | Easy generic solver saturation, hidden infeasibility, upper based only on metric boundary |
| Formal mathematics and theorem proving | Lean/mathlib issues and blueprints, licensed papers, dependency graphs, informal theorem collections | Formalize theorem families, fill proof dependency subgraphs, build reusable tactics or definitions | Lean compiler errors, proof states, completed weighted goals | Existing proof contamination, binary single-theorem reward, version-sensitive dependencies |
| Interactive games and simulators | Open-source games, Gymnasium/PettingZoo/OpenSpiel environments, transport/economic/control simulators | Policy/code optimization across deterministic held-out episodes, scenario adaptation, planning agents | Episode score, event log, constraint/safety checks, public scenario replay | Nondeterminism, live-state snapshots, reward exploits, excessive episode cost |

Named catalogs are discovery starting points only. Every selected artifact still
needs its own immutable identity, license, redistribution confirmation, and
source-level deduplication.

## Seed Card

Every adapter emits the same `seed_card.json` shape. LLM-generated prose may
suggest values, but executable scouts must verify source identity, licenses,
hashes, and measurements.

```json
{
  "schema_version": 1,
  "seed_id": "category/source/lead-id",
  "category": "systems-software-engineering",
  "channel": "upstream-change",
  "real_work": {
    "role": "storage engineer",
    "scenario": "recover and optimize a damaged append-only index",
    "deliverable": "source tree and generated index artifacts",
    "material_consequence": "lost or duplicated records after restart"
  },
  "origins": [
    {
      "kind": "repository",
      "project": "project-name",
      "url": "https://example.invalid/project",
      "revision": "immutable-revision",
      "artifact_sha256": "64-hex",
      "offline_mirror": "/absolute/path/to/verified-mirror",
      "license": "SPDX-ID",
      "redistribution_status": "confirmed|pending|rejected"
    }
  ],
  "task_hypothesis": {
    "snapshot_paths": ["/workspace/..."],
    "raw_metric": "metric before normalization",
    "direction": "maximize",
    "improvement_levers": ["lever-a", "lever-b"],
    "development_feedback": ["signal-a", "signal-b"],
    "baseline_strategy": "runnable starter basis",
    "upper_strategy": "measured or sourced achievable result",
    "reference_mode": "reference",
    "offline_verifier_plan": "one-shot stateless evaluator",
    "binary_only": false,
    "requires_waiting": false
  },
  "feasibility": {
    "runtime_network": false,
    "architectures": ["linux/amd64", "linux/arm64"],
    "estimated_cold_start_seconds": 0,
    "estimated_score_seconds": 0,
    "estimated_interaction_hours": 0,
    "data_bytes": 0
  },
  "dedup": {
    "upstream_ids": [],
    "dataset_ids": [],
    "objective_signature": "input-output-metric signature",
    "compared_catalogs": [],
    "collisions": [],
    "status": "pending"
  },
  "risks": [],
  "status": "lead"
}
```

Real cards replace all illustrative values. Unknown facts are `pending`; they
are never guessed into a promotable value.

## Screening Order

### Source Screen

Reject or hold before prototyping when the source cannot be pinned, intended
redistribution is not permitted, runtime needs the public network, data cannot
be packaged reproducibly, or the same upstream/dataset already appears in a
target benchmark. Compare canonical repository lineage and dataset identity,
not names.

For the owner's current prototype-first cycle, `seed-screen --for-prototype`
permits a **source-pinned, mirrored, scoreable** lead whose written rights and
unavailable retained-ID comparisons alone remain pending. It records a
`prototype-candidate`, never `rights-and-dedup-screened`. Known collisions and
unverified source bytes still fail. `author-batch` can construct an isolated
candidate while negotiation continues; final seed promotion and delivery still
need the full source/rights/dedup screen.

### Loop Screen

Reject when the only outcome is pass/fail, the artifact cannot be snapshotted,
feedback is unavailable or private-only, progress requires waiting, or there is
no plausible achievable upper. Rank higher when there are several independent
repair/optimization levers and each can be diagnosed locally.

### Micro-Prototype Screen

Build the smallest executable instance and run NOP, starter, at least one lawful
interior candidate when the family supports one, and a strong reference when
available. Measure score order, repeat behavior, cold start, end-to-end cost,
and feedback usefulness. This is a cheap authoring probe, not final DOCX
evidence.

Before image builds or full model rollouts, `run-controls` executes one or more
task-family-independent authoring programs and scores their actual snapshots.
`candidate-audit` checks the retained generator, scorer, score ladder, time,
process and snapshot hashes; `candidate-register` records each task-version
result in the campaign. Configurable fast near-reference diagnostics suggest
`revise`, not an official acceptance cutoff. The main reviewer evaluates the
causal mechanism, and does not promote an easy identity by merely increasing
records or waiting.

Reject score cliffs, inverted objectives, NOP above the lower endpoint, trivial
generic saturation, unreachable anchors, verifier time that prevents iterative
submission, and hidden-data leakage. Revise the task mechanism rather than
adding records, sleep, noise, or tighter resources to make it look harder.

Before promoting a data-derived Seed, run a target-leakage probe over the
actual pinned bytes. Remove target, post-outcome, aggregate and answer-bearing
columns from the model-visible Work input; keep them only in the private Judge
input. Check that the chronological or group split has no overlap, that the
public input cannot reconstruct the target through deterministic identities,
and that the scorer uses the declared raw metric rather than a proxy. Run zero,
missing-ID, malformed, negative/nonfinite and at least one semantically wrong
prediction through the same scorer. A positive ladder from a scorer that reads
public labels is a prototype failure, not difficulty evidence.

### Promotion

Promotion means a measured, rights-cleared Seed is worth full SForge
construction; Harbor can remain an internal scoring tool. It does not mean a
task passes delivery. Formal confidence comes later from five SForge reference
rescores, native cross-architecture SForge scoring, genuine NOP and batch
scans, and at least three full-budget frontier-model runs.

## Deduplication And Contamination

Build a fingerprint with:

- canonical upstream repository identity including forks and renamed projects;
- pinned revision/release and major transitive source lineage;
- public dataset IDs, versions, splits, and file hashes;
- input artifact type, required output type, raw objective, and verifier
  semantics;
- issue, PR, benchmark, theorem, scenario, and published-solution identifiers;
- generated-instance family and generator identity.

Compare this fingerprint with all available EdgeBench public tasks, Topify's
retained identifiers when supplied, MLS-Bench, Terminal-Bench, GDPval, and the
local candidate catalog. Missing retained identifiers keep that comparison
incomplete; they do not become an automatic pass.

Treat public solution patches, tutorials, competition notebooks, theorem proofs,
and benchmark reference implementations as contamination risks. They may still
ground an upper or checker when licensing permits, but the agent task should not
be exact public patch retrieval.

## Automated Scouts And Human Boundaries

Use adapters with one logical interface:

```text
discover(source_config, category, limit) -> SeedCard[]
verify(seed_card, offline_mirror, dedup_catalogs) -> ScreenedSeed
prototype(screened_seed, fixed_seed) -> MicroPrototypeEvidence
```

Adapters may query repository APIs, dataset catalogs, paper indexes, issue
trackers, and simulator registries during authoring. They must store immutable
IDs, retrieval timestamps, raw metadata, hashes, and an offline mirror before
the task is generated. Task runtime remains offline.

Automation can verify SPDX files, hashes, URL/revision reachability, buildability,
score behavior, and known-catalog overlap. It cannot manufacture written
redistribution consent, retained EdgeBench identifiers, expert correctness, GPU
availability, or real frontier-model trajectories. Those fields remain
incomplete until supplied or executed.

The production CLI accepts either a captured normalized catalog through
`scout --catalog` or raw local source metadata through `scout-source`. The
machine-readable source registry is `source-registry.json`. `scout-source`
performs no network access and emits conservative cards with pending rights,
deduplication, mechanism, and feasibility. Use append-only `seed-refine`
attempts to complete those cards without changing captured source identity.
Use `mirror-source` to fetch/verify a pinned official artifact into the
content-addressed campaign mirror before claiming its bytes in a Seed Card;
the capture alone stays `lead-only`. Official pages or API results without
immutable source bytes remain leads, not executable prototypes.
A discovery-origin URL with no pinned artifact remains in its captured card
unchanged; `seed-refine` may **append** separately pinned, mirrored artifacts
without deleting the discovery link. Only the appended artifact is the
reproducible input; a
discovery page is not passed off as a versioned data file. The main reviewer
checks that the pinned artifact actually belongs to the source lineage and
supports the stated workflow before `--for-prototype`.
For historical catalog leads that mistakenly classified an unmirrored issue or
landing page as an artifact, the reviewer may relabel **only** that origin's
`kind` to `discovery` in an append-only refinement. The original lead, URL,
revision, digest, and rights status are unchanged; any previously pinned
digest or mirror forbids this correction. Append the actual commit- or
version-pinned artifact separately. Never assign its digest to the mutable
discovery URL or invent runtime feasibility estimates to force a prototype
screen.
Domain discovery can be interactive, scripted, or API-backed, but changing web
APIs stay outside the acceptance core and a search result never becomes a task
merely because an LLM described it plausibly.

For the current owner, screen with `seed-screen --for-prototype --owner-sample`,
author an isolated task and use `candidate-audit`/`candidate-register` for
the measured r0→r1→r2 lineage. The older rights-screened
`seed-prototype`/`seed-promote` path is historical, not a customer gate.

## Diversity Controls

Balance more than category labels. Track upstream project, dataset family,
task shape, artifact type, feedback channel, raw metric, programming language,
runtime, and domain. A batch of 26 differently named recovery tasks over the
same codebase is not broad systems coverage.

Use campaign-level caps as planning controls, not DOCX claims. For example,
limit how many promoted seeds share one upstream, one dataset, one generator,
or one objective signature, and reserve capacity for underrepresented feedback
types. Record any override with its diversity rationale.
