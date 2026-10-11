# Benchmark profile contract

The production Skill has one reusable core and a profile for each Benchmark.
The profile is a machine-readable interpretation of the user's requirements;
it does not replace the source requirements document.

## Profile precedence

```text
user requirements document
  > Benchmark-specific requirements
  > Vendor/platform requirements
  > profile defaults
```

The profile receipt stores the profile JSON hash and, when supplied, the source
requirements-document hash. A custom profile should be checked into the
project or retained beside the private QC evidence, never silently generated
from an unrecorded conversation.

## Required schema

The profile contains:

- `profile_id` and `benchmark`;
- `requirements_precedence`;
- `delivery.formats`, `delivery.default_format` and `delivery.rollout_harness`;
- `capabilities`, which declares the reward types, artifact shape, architectures
  and review IDs supported by the selected adapter;
- `thresholds.interaction_budget_seconds_minimum`;
- `thresholds.interaction_budget_seconds_preferred`;
- `thresholds.frontier_trials_minimum`;
- `thresholds.reference_repeats`;
- `thresholds.architectures_required`;
- `thresholds.q_review_required` and `q_review_not_required`;
- `checks`, for format-specific and benchmark-specific applicability.

For hierarchical production, add the optional `supervision` object. It binds
the employment and rollout policy to the Benchmark rather than hard-coding
EdgeBench rules in the supervisor:

```json
{
  "supervision": {
    "employee_unit": "one-task",
    "qc": {"max_rounds": 2, "qc2_escalation": "supervisor-user"},
    "rollout": {
      "checkpoints": [
        {"name": "30m", "after_seconds": 1800, "mode": "observe", "metric": "best_score"},
        {"name": "1h", "after_seconds": 3600, "mode": "upper_bound",
         "metric": "best_score", "operator": "gte", "value": 80}
      ],
      "growth": {"enabled": true, "metric": "best_score", "baseline": "nop",
                  "operator": "gt", "min_delta": 0},
      "final": {"source": "requirements-document", "low_result_action": "owner_review"}
    }
  }
}
```

`upper_bound` means the task is too easy when the bound is reached at the
specified checkpoint. A provider, model, container, or judge failure is not a
quality failure and must be represented as an infrastructure retry. For a
binary-reward Benchmark, use `best_reward` and define its growth semantics in
the user requirements/profile; do not reuse the EdgeBench score threshold.

Use `references/profiles/edgebench-v1.json` for the current EdgeBench defaults.
Use `examples/custom-benchmark-profile.json` as a small custom profile example.
The example intentionally uses a shorter budget, fewer frontier trials and
fewer reference repeats while keeping the current adapter-compatible reward,
artifact and architecture capabilities. A profile that requests binary reward,
multi-file snapshots or a different architecture is rejected before authoring
until a dedicated adapter exists; this prevents a late rollout failure.

## Selection

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  benchmark-profile WORKSPACE \
  --profile custom-benchmark \
  --profile-file CUSTOM_PROFILE.json \
  --requirements-doc USER_REQUIREMENTS.md
```

The task request may also declare `benchmark_profile`. When declared, it must
match the selected profile. Legacy requests without that field retain the
EdgeBench compatibility behavior until a profile is selected.

Universal production integrity checks remain active across profiles: truthful
status, immutable evidence identity, privacy/credential scanning, safe archive
paths, hash consistency and no reuse of scores after a scoring identity change.
