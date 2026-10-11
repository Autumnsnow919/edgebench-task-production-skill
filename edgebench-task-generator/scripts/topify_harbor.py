#!/usr/bin/env python3
"""Generate a Harbor candidate through a confined task-family plugin."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil

import topify_common as common
import topify_validate


def _load_plugin(name: str):
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise common.PipelineError("invalid task-family plugin name")
    try:
        return importlib.import_module(f"topify_plugins.{name}")
    except (ImportError, ValueError) as exc:
        raise common.PipelineError(f"cannot load task-family plugin {name}: {exc}") from exc


def _files(root: Path) -> list[str]:
    result = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise common.PipelineError(f"generated task contains a symlink: {relative}")
        if path.is_file():
            result.append(str(relative))
        elif not path.is_dir():
            raise common.PipelineError(f"generated task contains an unsupported file type: {relative}")
    return sorted(result)


def generate(workspace: str | Path) -> dict:
    root, metadata = common.load_workspace(workspace)
    design_path = root / "task_design.json"
    if not design_path.is_file() or design_path.is_symlink():
        raise common.PipelineError("task design must be screened before generation")
    design = common.load_json(design_path)
    if design.get("request_sha256") != metadata["request_sha256"]:
        raise common.PipelineError("task design belongs to another authoring request")
    target = root / "task"
    if target.exists() or target.is_symlink():
        raise common.PipelineError("task directory already exists; refusing to overwrite user work")
    staging = root / f".task-generation-{os.getpid()}"
    if staging.exists() or staging.is_symlink():
        raise common.PipelineError("staging path already exists")
    staging.mkdir()
    try:
        plugin = _load_plugin(metadata["request"]["plugin"])
        manifest = plugin.prepare(staging, metadata["request"], design, metadata["request"]["seed"])
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise common.PipelineError("plugin returned an invalid candidate manifest")
        actual_files = _files(staging)
        if manifest.get("files") != actual_files:
            raise common.PipelineError("plugin manifest does not match generated files")
        required = {
            "task.toml", "instruction.md", "environment/Dockerfile",
            "tests/Dockerfile", "tests/test.sh", "tests/evaluate.py",
            "tests/scoring_metadata.json",
        }
        if not required.issubset(actual_files):
            raise common.PipelineError("plugin omitted required Harbor task files")
        if manifest.get("agent_context") != "environment" or manifest.get("verifier_context") != "tests":
            raise common.PipelineError("plugin contexts must be sibling environment and tests directories")
        for private in manifest.get("private_files", []):
            if not isinstance(private, str) or not private.startswith("tests/") or private not in actual_files:
                raise common.PipelineError("private files must remain inside the verifier context")
        mode = metadata["request"]["reference_mode"]
        has_solution = any(path == "solution/solve.sh" for path in actual_files)
        if mode == "reference" and not has_solution:
            raise common.PipelineError("reference mode requires a runnable solution/solve.sh")
        if mode == "open-solution" and any(path.startswith("solution/") for path in actual_files):
            raise common.PipelineError("open-solution mode must not contain a fake reference directory")
        staging.rename(target)
        file_records = [
            {"path": path, "sha256": common.sha256_file(target / path)}
            for path in actual_files
        ]
        evidence = {
            **manifest,
            "task_id": metadata["task_id"],
            "request_sha256": metadata["request_sha256"],
            "design_sha256": design["design_sha256"],
            "file_records": file_records,
            # Bind generation to the same canonical task identity consumed by
            # validate, measure, profile, freeze and packaging. The old
            # file-only digest omitted directories and therefore drifted as
            # soon as the task entered validation.
            "task_sha256": topify_validate.task_sha256(target),
        }
        common.write_new_json(root / "evidence/generation_manifest.json", evidence)
        common.append_event(root, "generated", task_sha256=evidence["task_sha256"])
        return {
            "state": "GENERATED",
            "task": str(target),
            "task_sha256": evidence["task_sha256"],
        }
    except Exception:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
