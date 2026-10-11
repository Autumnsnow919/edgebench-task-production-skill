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
