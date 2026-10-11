#!/usr/bin/env python3
"""Generate a deterministic continuous-score Harbor miniature."""

from __future__ import annotations

import json
from pathlib import Path


BASE_IMAGE = "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"


SCORER = r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def score(solution, instance):
    selected = solution.get("selected") if isinstance(solution, dict) else None
    schema_ok = isinstance(selected, list) and all(isinstance(item, str) for item in selected)
    unique_ok = schema_ok and len(selected) == len(set(selected))
    projects = {item["id"]: item for item in instance["projects"]}
    ids_ok = unique_ok and all(item in projects for item in selected)
    total_cost = sum(projects[item]["cost"] for item in selected) if ids_ok else 0
    capacity_ok = ids_ok and total_cost <= instance["capacity"]
    covered = set()
    if capacity_ok:
        for item in selected:
            covered.update(projects[item]["covers"])
    weights = {item["id"]: item["weight"] for item in instance["targets"]}
    raw = sum(weights[item] for item in covered if item in weights) if capacity_ok else 0
    total = max(0.0, min(100.0, float(raw)))
    valid = bool(schema_ok and unique_ok and ids_ok and capacity_ok)
    checks = [
        {"name": "solution_schema", "pass": bool(schema_ok)},
        {"name": "unique_project_ids", "pass": bool(unique_ok)},
        {"name": "known_project_ids", "pass": bool(ids_ok)},
        {"name": "capacity", "pass": bool(capacity_ok)},
        {"name": "positive_coverage", "pass": bool(raw > 0)},
    ]
    return {
        "total_score": total,
        "reward": total / 100.0,
        "raw_metric": raw,
        "normalized_score": total,
        "valid": valid,
        "checks": checks,
        "feedback": {
            "selected_cost": total_cost,
            "capacity": instance["capacity"],
            "covered_targets": sorted(covered),
            "uncovered_targets": sorted(set(weights) - covered),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solution", default="/workspace/solution.json")
    parser.add_argument("--instances", default="/workspace/dev/instances.json")
    args = parser.parse_args()
    try:
        solution = load(args.solution)
        instance = load(args.instances)
        report = score(solution, instance)
    except Exception as exc:
        report = {
            "total_score": 0.0,
            "reward": 0.0,
            "raw_metric": 0,
            "normalized_score": 0.0,
            "valid": False,
            "checks": [{"name": "report_generation", "pass": False}],
            "feedback": {"error": type(exc).__name__},
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
'''


INSTRUCTION = """# Continuous Allocation Repair

You are improving an allocation plan for weighted service targets under a fixed
capacity. Your submission is `/workspace/solution.json` with this shape:

```json
{"selected": ["project-id", "project-id"]}
```

Each selected project consumes capacity and covers one or more targets. Unknown
or duplicate project IDs and capacity overflow make the snapshot invalid. A
valid snapshot earns the total weight of all uniquely covered targets, mapped
monotonically to a score from 0 to 100. The best score from all submissions is
the run result.

Use `/workspace/dev/instances.json` and repeatedly run this local feedback tool:

```bash
python3 /workspace/tools/score_dev.py --solution /workspace/solution.json
```

The local report identifies invalid IDs, capacity use, covered targets, and
uncovered targets. Improve the snapshot incrementally and submit whenever it is
useful. The verifier uses separate private instances with the same public
schema, starts fresh for every submission, and has no network access.
"""


DEV_INSTANCE = {
    "capacity": 10,
    "targets": [
        {"id": "t1", "weight": 10},
        {"id": "t2", "weight": 15},
        {"id": "t3", "weight": 20},
        {"id": "t4", "weight": 25},
        {"id": "t5", "weight": 30},
    ],
    "projects": [
        {"id": "p1", "cost": 4, "covers": ["t1", "t2"]},
        {"id": "p2", "cost": 4, "covers": ["t3"]},
        {"id": "p3", "cost": 3, "covers": ["t4"]},
        {"id": "p4", "cost": 5, "covers": ["t1", "t3", "t5"]},
        {"id": "p5", "cost": 5, "covers": ["t2", "t4"]},
    ],
}


PRIVATE_INSTANCE = {
    **DEV_INSTANCE,
    "targets": [
        {"id": "t1", "weight": 12},
        {"id": "t2", "weight": 18},
        {"id": "t3", "weight": 16},
        {"id": "t4", "weight": 24},
        {"id": "t5", "weight": 30},
    ],
}


def _write(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def prepare(root: Path, request: dict, design: dict, seed: int) -> dict:
    del design, seed
    resources = request["resources"]
    scoring = request["scoring"]
    open_solution = request["reference_mode"] == "open-solution"
    task_toml = f'''schema_version = "1.4"
artifacts = ["/workspace/solution.json"]

[task]
name = {json.dumps(request['task_id'])}
version = "0.1.0"
description = "Iteratively improve a capacity-constrained weighted allocation plan."

[metadata]
benchmark = "topify-edgebench-100"
category = "Combinatorial Optimization"
reward_type = "bounded_continuous"
score_direction = {json.dumps(scoring['direction'])}
selection = "score_first"
raw_metric = {json.dumps(scoring['raw_metric'])}
formula = {json.dumps(scoring['formula'])}
constants = {{ baseline = {scoring['baseline']}, upper = {scoring['upper']}, capacity = 10 }}
baseline = {scoring['baseline']}
upper = {scoring['upper']}
upper_source = {json.dumps(scoring['upper_basis'])}
open_solution = {str(open_solution).lower()}
development_split = "/workspace/dev/instances.json"
local_scorer = "/workspace/tools/score_dev.py"
report_parser = "json:total_score,valid,checks"
batch_mechanism = "run-once"

[agent]
timeout_sec = {request['interaction_budget_seconds']}
cpus = {resources['agent_cpu']}
memory_mb = {resources['agent_memory_mb']}
gpus = {len(resources['gpu_types'])}
network_mode = "no-network"

[verifier]
environment_mode = "separate"
timeout_sec = {request['verifier_timeout_seconds']}
network_mode = "no-network"

[verifier.environment]
cpus = {resources['verifier_cpu']}
memory_mb = {resources['verifier_memory_mb']}
gpus = {len(resources['gpu_types'])}
network_mode = "no-network"

[environment]
network_mode = "public"
open_internet_justification = "Fetch declared build dependencies; runtime remains offline."
'''
    _write(root / "task.toml", task_toml)
    _write(root / "instruction.md", INSTRUCTION)
    _write(
        root / "environment/Dockerfile",
        f"FROM {BASE_IMAGE}\nWORKDIR /workspace\nCOPY workspace/ /workspace/\n",
    )
    _write_json(root / "environment/workspace/solution.json", {"selected": ["p1"]})
    _write_json(root / "environment/workspace/dev/instances.json", DEV_INSTANCE)
    _write(root / "environment/workspace/tools/score_dev.py", SCORER, executable=True)
    _write(
        root / "tests/Dockerfile",
        f"FROM {BASE_IMAGE}\nWORKDIR /tests\nCOPY . /tests\nRUN chmod 700 /tests/private && chmod 600 /tests/private/instances.json\n",
    )
    _write_json(root / "tests/private/instances.json", PRIVATE_INSTANCE)
    _write_json(root / "tests/calibration/interior.json", {"selected": ["p1", "p2"]})
    _write_json(root / "tests/calibration/strong.json", {"selected": ["p4", "p5"]})
    _write(root / "tests/evaluate.py", SCORER, executable=True)
    _write(
        root / "tests/test.sh",
        "#!/bin/sh\nset -eu\n"
        "PATH=/usr/local/bin:/usr/bin:/bin\nexport PATH\n"
        "unset PYTHONPATH PYTHONHOME PYTHONSTARTUP LD_PRELOAD\n"
        "exec python3 -I /tests/evaluate.py --solution /workspace/solution.json --instances /tests/private/instances.json\n",
        executable=True,
    )
    _write_json(root / "tests/scoring_metadata.json", {
        "schema_version": 1,
        "reward_type": "bounded_continuous",
        "direction": scoring["direction"],
        "raw_metric": scoring["raw_metric"],
        "formula": scoring["formula"],
        "constants": scoring["constants"],
        "baseline": scoring["baseline"],
        "upper": scoring["upper"],
        "upper_basis": scoring["upper_basis"],
        "report_fields": ["total_score", "raw_metric", "normalized_score", "reward", "valid", "checks"],
    })
    _write_json(root / "nop/nop.json", {"selected": []})
    _write(
        root / "nop/nop.sh",
        "#!/bin/sh\nset -eu\n"
        "SOURCE=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)/nop.json\n"
        "TARGET=${WORKSPACE:-/workspace}\n"
        "install -d -m 0755 \"$TARGET\"\n"
        "install -m 0644 \"$SOURCE\" \"$TARGET/solution.json\"\n",
        executable=True,
    )
    if not open_solution:
        _write_json(root / "solution/reference.json", {"selected": ["p4", "p5"]})
        _write(
            root / "solution/solve.sh",
            "#!/bin/sh\nset -eu\n"
            "SOURCE=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)/reference.json\n"
            "TARGET=${WORKSPACE:-/workspace}\n"
            "install -d -m 0755 \"$TARGET\"\n"
            "install -m 0644 \"$SOURCE\" \"$TARGET/solution.json\"\n",
            executable=True,
        )
    files = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
    return {
        "schema_version": 1,
        "files": files,
        "snapshot_artifacts": ["/workspace/solution.json"],
        "agent_context": "environment",
        "verifier_context": "tests",
        "private_files": ["tests/private/instances.json"],
        "reference_mode": request["reference_mode"],
    }
