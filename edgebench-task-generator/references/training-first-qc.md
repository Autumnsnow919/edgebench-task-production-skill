# Training-first QC policy

This is the owner policy for **EdgeBench review**. It does not apply to CritPT,
whose separate vendor brief remains authoritative. It does not erase a
selected customer's required delivery schema; it changes how the EdgeBench
production team prioritizes non-core findings before final packaging.

## What blocks a task

Block or return the task for repair when the finding changes the scientific or
training truth:

- the question states a false physical/technical fact or contradicts itself;
- a necessary input, boundary condition, unit, variable or objective is absent;
- the requested answer is not unique, not determinable, or the declared
  precision cannot be justified;
- the reference answer, solution, scorer or checkpoint accepts the wrong
  reasoning and rejects the correct reasoning;
- the task is materially easier because of an answer leak, copied solution,
  trivial shortcut or accidental public/private contamination;
- the training target is qualitative/open-ended when the selected Benchmark
  requires a determinate quantitative result;
- the task cannot be executed or safely scored under the agreed environment.

These are **semantic blockers**. They remain blockers even when the prose,
rubric or package looks polished.

The following EdgeBench checks are also hard gates and may not be accepted as
mere polish: native ARM64 validation where required by the current profile,
static package/QC checks, Work/Judge build and runtime isolation, NOP/reference
execution, scorer semantics, snapshot/input identity, and the declared
artifact/feedback contract. A hard-gate failure cannot be waived by saying the
task is difficult or useful for training.

## What may be accepted with a note

If the question is scientifically correct, self-contained, genuinely difficult
and produces a useful error signal, the following are normally non-blocking:

- rubric wording that is not maximally granular;
- checkpoint weights that are useful but not perfectly balanced;
- a checkpoint that is partly manual when the final target remains clear;
- minor metadata, naming, README or evidence-index omissions;
- a nonessential format mismatch that can be repaired during packaging;
- an imperfect but honest difficulty explanation or mechanism label.

Record these as `accepted` or `review`, with a repair note. Do not convert a
semantic defect into `accepted` merely because the question is difficult.

## Review decision

Use the following classification in the main review:

```text
pass      confirmed and clean
accepted  non-blocking weakness; training value is sufficient and evidence is recorded
pending   not enough evidence to decide
fail      semantic or execution blocker
```

`ready` may contain `pass` and `accepted` items. It may not contain `fail` or
`pending` items. The rationale must identify why every accepted weakness does
not change the training target. Formatting should still be repaired whenever
cheap and safe; acceptance is not an excuse to stop making the package clean.

## Evidence priority

Review in this order:

1. task truth and scientific correctness;
2. self-contained solvability and answer uniqueness;
3. difficulty, reasoning chain and useful feedback/error signal;
4. reference/scorer agreement;
5. rubric quality and checkpoint granularity;
6. metadata and presentation polish.

The Secretary may summarize these findings, but only the Supervisor/main
reviewer records the decision. A model score never repairs a false question.
