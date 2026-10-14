#!/usr/bin/env python3
"""Create a pinned inventory of public EdgeBench task definitions.

Analysis-only: no Seed promotion, task creation or rollout authorization.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import topify_common as common

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,100}$")


def inventory(source: str | Path, output: str | Path, catalog_revision: str,
              source_url: str = "https://huggingface.co/datasets/ByteDance-Seed/EdgeBench") -> tuple[dict, int]:
    source_path = Path(source).resolve(strict=True)
    if source_path.is_symlink() or not source_path.is_file():
        raise common.PipelineError("public task catalog must be a regular local file")
    rows = []
    for line_number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise common.PipelineError(f"invalid public task JSONL at line {line_number}") from exc
        task_id = value.get("task_id") if isinstance(value, dict) else None
        if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
            raise common.PipelineError(f"invalid public task_id at line {line_number}")
        rows.append({"task_id": task_id, "name": value.get("name"),
                     "category": value.get("category"), "description": value.get("description"),
                     "language": value.get("language"), "metric": value.get("metric"),
                     "internet": value.get("internet"),
                     "parent_definition_sha256": common.canonical_sha256(value),
                     "status": "parent-analysis-only"})
    if not rows or len({row["task_id"] for row in rows}) != len(rows):
        raise common.PipelineError("public task catalog is empty or contains duplicate task_id")
    result = {"schema_version": 1, "parent_kind": "public-edgebench-task-catalog",
              "catalog_url": source_url, "catalog_revision": catalog_revision,
              "catalog_sha256": common.sha256_file(source_path), "task_count": len(rows),
              "tasks": rows, "status": "parent-analysis-only; no Seed/task/rollout authorization"}
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise common.PipelineError("inventory output must be a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    common.write_new_json(destination, result)
    return result, 0
