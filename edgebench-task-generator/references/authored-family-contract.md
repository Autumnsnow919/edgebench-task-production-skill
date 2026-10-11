# Source-specific family contract for all six EdgeBench domains

Use `plugin: "authored_context"` in a screened authoring request when a domain
has no checked-in task-family plugin. The author writes the actual task in its
**isolated** `WORKSPACE/authoring/task-source/` directory. `generate` only
copies byte-committed files: it does not execute arbitrary source-capture
code or mistake a source registry lead for an approved task.

## Required source layout

Inside `task-source/`, provide real, domain-specific files including:

```text
manifest.json
task.toml
instruction.md                  # English original
environment/Dockerfile
environment/workspace/<snapshot file>
environment/workspace/<public development split and scorer>
tests/Dockerfile
tests/test.sh
tests/evaluate.py
tests/scoring_metadata.json
tests/private/instances.json       # required scorer input and private_files entry
tests/private/<optional bytes>    # additional entries must be listed, if used
tests/calibration/interior.<snapshot suffix>
tests/calibration/strong.<snapshot suffix>
nop/nop.sh                       # writes the declared file into $WORKSPACE
solution/solve.sh                # when reference_mode=reference
```

The *one* `task.toml` artifact must be a named file under `/workspace/` (for
example `solution.py`, `Main.lean`, `policy.py`, `model.bin`, or `report.json`).
It must agree with `task_design.json` `snapshot_artifacts` and the paths used
by `tests/test.sh`, `nop/nop.sh` and `solution/solve.sh`. The local verifier
entrypoint accepts `--solution <snapshot>` and `--instances <private pack>`;
it emits bounded, structured raw/normalized score, validity and named checks.
The current local scorer/audit passes `tests/private/instances.json` as the
required `--instances` entrypoint; it may index other private data in that
directory. SForge protects the **whole** `tests/private/` directory.
The companion SForge adapter submits exactly this declared file and copies
only its public skeleton into the Judge image. The current pipeline supports
**one file**, not an arbitrary multi-file project snapshot; authors needing
multi-file source trees must extend and test that snapshot adapter first.

`manifest.json` has precisely these keys:

```json
{
  "schema_version": 1,
  "files": [
    {"path": "environment/Dockerfile", "sha256": "<actual SHA-256>"},
    {"path": "...", "sha256": "<actual SHA-256>"}
  ],
  "private_files": ["tests/private/instances.json"],
  "snapshot_artifacts": ["/workspace/solution.py"],
  "reference_mode": "reference"
}
```

List every generated task file (not the manifest itself) exactly once in
lexical path order with its actual lowercase SHA-256. Paths must be relative,
non-symlinked files under the approved task directories. `private_files`
may only point under `tests/`; their content must never enter Agent work.
The manifest's snapshot and reference mode must match the screened request
and task design. Changing task bytes requires a new workspace and new
measurement receipts; never replace a frozen scored identity in place.

## The source determines the task, not the fixture

- Systems/software: a source-code implementation or patch, measured against
  public conformance and held-out regression/performance cases; screen stock
  upstream wrappers and known published fixes.
- Science/ML: model/solver weights or predictions, with separate public and
  hidden data and quantitative residual/validation feedback; screen simple
  predictors and untuned available libraries.
- Professional knowledge: an evidence-backed structured deliverable with
  arithmetic, cross-document and citation checks; screen schema-only forms
  and single-source copying.
- Optimization: a solver or plan with graded feasible objective and honest
  reachable upper; screen generic greedy, CP-SAT and short local search.
- Formal mathematics: a pinned Lean toolchain, one submitted proof module,
  weighted theorem goals and compiler feedback; screen `simp`/`aesop` and
  existing published formalizations.
- Games/simulators: a submitted policy module evaluated over fresh fixed
  episodes with local replays and numeric reward; screen random, greedy and
  published example policies.

These are **authoring directions**, not six prebuilt customer tasks. Every
family needs independently executed NOP/interior/reference and generic
controls, causal review, rights, SForge native-image/isolation checks, and
full-budget rollout evidence before customer delivery. If an agent can copy
the family-independent cheap control and approach the upper in minutes,
record `revise` and switch the mechanism or Seed rather than adding volume.
