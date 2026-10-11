#!/usr/bin/env python3
"""Validate generated Harbor task semantics directly against the Topify DOCX."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat
import tomllib

import topify_common as common


def task_records(task: Path) -> list[dict]:
    records = []
    if not task.is_dir() or task.is_symlink():
        return [{"path": ".", "type": "missing-or-unsafe"}]
    for path in sorted(task.rglob("*")):
        relative = str(path.relative_to(task))
        try:
            mode = path.lstat().st_mode
        except OSError:
            records.append({"path": relative, "type": "unreadable"})
            continue
        if stat.S_ISLNK(mode):
            records.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif stat.S_ISREG(mode):
            records.append({"path": relative, "type": "file", "sha256": common.sha256_file(path)})
        elif stat.S_ISDIR(mode):
            records.append({"path": relative, "type": "directory"})
        else:
            records.append({"path": relative, "type": "unsupported"})
    return records


def task_sha256(task: Path) -> str:
    return common.canonical_sha256(task_records(task))


def context_sha256(context: Path) -> str:
    """Hash context bytes and file modes, both of which affect the built image."""
    records = task_records(context)
    for entry in records:
        if entry["type"] == "file":
            entry["mode"] = stat.S_IMODE((context / entry["path"]).lstat().st_mode)
    return common.canonical_sha256(records)


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _regular(path: Path, executable: bool = False) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return (
        stat.S_ISREG(mode) and not stat.S_ISLNK(mode)
        and (not executable or bool(mode & stat.S_IXUSR))
    )


def _real_directory(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _check(check_id: str, passed: bool, detail: str) -> dict:
    return {"id": check_id, "status": "complete" if passed else "failed", "detail": detail}


def _workspace_artifact(path: object) -> bool:
    if not isinstance(path, str) or not path.startswith("/workspace/"):
        return False
    parsed = PurePosixPath(path)
    return ".." not in parsed.parts and str(parsed) == path


def artifact_relative(task: Path) -> str:
    """The currently supported snapshot contract is one declared file."""
    config = _load_toml(task / "task.toml")
    artifacts = config.get("artifacts")
    if (not isinstance(artifacts, list) or len(artifacts) != 1
            or not _workspace_artifact(artifacts[0])):
        raise common.PipelineError("task must declare one safe /workspace snapshot file")
    relative = PurePosixPath(artifacts[0]).relative_to("/workspace").as_posix()
    if relative in ("", "."):
        raise common.PipelineError("snapshot must be a named file")
    return relative


def _load_toml(path: Path) -> dict:
    if not _regular(path):
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_optional_json(path: Path) -> dict:
    try:
        return common.load_json(path)
    except (common.PipelineError, OSError, ValueError):
        return {}


def evaluate(workspace: str | Path) -> dict:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    generated = _load_optional_json(root / "evidence/generation_manifest.json")
    config = _load_toml(task / "task.toml")
    scoring = _load_optional_json(task / "tests/scoring_metadata.json")
    request = metadata["request"]
    environment = task / "environment"
    verifier = task / "tests"
    entry = verifier / "test.sh"
    evaluator = verifier / "evaluate.py"
    entry_text = entry.read_text(encoding="utf-8") if _regular(entry) else ""
    evaluator_text = evaluator.read_text(encoding="utf-8") if _regular(evaluator) else ""

    contexts_ok = (
        _real_directory(environment) and _real_directory(verifier)
        and environment.parent == verifier.parent == task
        and _regular(environment / "Dockerfile") and _regular(verifier / "Dockerfile")
    )
    network_ok = all(_nested(config, *path) == "no-network" for path in (
        ("agent", "network_mode"),
        ("verifier", "network_mode"),
        ("verifier", "environment", "network_mode"),
    ))
    build_network = _nested(config, "environment", "network_mode")
    justification = (_nested(config, "environment", "open_internet_justification")
                     or _nested(config, "metadata", "open_internet_justification"))
    network_ok = network_ok and (build_network == "no-network" or
                                build_network == "public" and isinstance(justification, str)
                                and bool(justification.strip()))
    cleanup_tokens = ("PATH=", "unset PYTHONPATH PYTHONHOME PYTHONSTARTUP LD_PRELOAD", "exec python3")
    cleanup_ok = all(token in entry_text for token in cleanup_tokens)
    if cleanup_ok:
        cleanup_ok = entry_text.index("PATH=") < entry_text.index("unset PYTHONPATH") < entry_text.index("exec python3")
    stateless_ok = (
        _regular(entry, executable=True) and _regular(evaluator, executable=True)
        and "exec python3" in entry_text
        and not any(token in entry_text.lower() for token in ("serve ", "listen", "daemon"))
    )
    artifacts = config.get("artifacts")
    artifact_name = None
    if isinstance(artifacts, list) and len(artifacts) == 1 and _workspace_artifact(artifacts[0]):
        artifact_name = PurePosixPath(artifacts[0]).relative_to("/workspace").as_posix()
    snapshot_ok = (
        isinstance(artifacts, list) and len(artifacts) == 1
        and len(set(artifacts)) == len(artifacts)
        and all(_workspace_artifact(item) for item in artifacts)
        and artifacts == generated.get("snapshot_artifacts")
    )
    report_fields = scoring.get("report_fields")
    structured_ok = (
        isinstance(report_fields, list)
        and {"total_score", "raw_metric", "normalized_score", "reward", "valid", "checks"}.issubset(report_fields)
        and all(f'"{field}"' in evaluator_text for field in ("total_score", "valid", "checks"))
        and isinstance(_nested(config, "metadata", "report_parser"), str)
    )
    dev_split = _nested(config, "metadata", "development_split")
    local_scorer = _nested(config, "metadata", "local_scorer")
    local_ok = (
        _workspace_artifact(dev_split) and _workspace_artifact(local_scorer)
        and _regular(environment / "workspace" / str(dev_split).removeprefix("/workspace/"))
        and _regular(environment / "workspace" / str(local_scorer).removeprefix("/workspace/"), executable=True)
    )
    scoring_request = request["scoring"]
    continuous_ok = (
        _nested(config, "metadata", "reward_type") == "bounded_continuous"
        and _nested(config, "metadata", "score_direction") == scoring_request["direction"]
        and _nested(config, "metadata", "raw_metric") == scoring_request["raw_metric"]
        and _nested(config, "metadata", "formula") == scoring_request["formula"]
        and _nested(config, "metadata", "baseline") == scoring_request["baseline"]
        and _nested(config, "metadata", "upper") == scoring_request["upper"]
        and _nested(config, "metadata", "upper_source") == scoring_request["upper_basis"]
        and _nested(config, "metadata", "batch_mechanism") == "run-once"
        and isinstance(_nested(config, "metadata", "constants"), dict)
        and bool(_nested(config, "metadata", "constants"))
    )
    resources = request["resources"]
    budget_ok = (
        _nested(config, "agent", "timeout_sec") == request["interaction_budget_seconds"]
        and _nested(config, "verifier", "timeout_sec") == request["verifier_timeout_seconds"]
        and _nested(config, "agent", "cpus") == resources["agent_cpu"]
        and _nested(config, "agent", "memory_mb") == resources["agent_memory_mb"]
        and _nested(config, "verifier", "environment", "cpus") == resources["verifier_cpu"]
        and _nested(config, "verifier", "environment", "memory_mb") == resources["verifier_memory_mb"]
        and set(resources["architectures"]) == common.ARCHITECTURES
    )
    mode = request["reference_mode"]
    solve = task / "solution/solve.sh"
    solve_text = solve.read_text(encoding="utf-8") if _regular(solve, executable=True) else ""
    reference_ok = (
        _nested(config, "metadata", "open_solution") is (mode == "open-solution")
        and (
            mode == "open-solution" and not (task / "solution").exists()
            or mode == "reference" and _regular(solve, executable=True)
            and solve.stat().st_size > 0 and "WORKSPACE" in solve_text
            and artifact_name is not None and artifact_name in solve_text
        )
    )
    dockerfiles = [environment / "Dockerfile", verifier / "Dockerfile"]
    bases_ok = all(
        _regular(path) and any(
            line.startswith("FROM ") and "@sha256:" in line
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        for path in dockerfiles
    )
    private_isolation_ok = contexts_ok and not any(
        "private" in path.relative_to(environment).parts
        for path in environment.rglob("*")
    )
    nop_script = task / "nop/nop.sh"
    nop_text = nop_script.read_text(encoding="utf-8") if _regular(nop_script, executable=True) else ""
    nop_inputs = task / "nop"
    nop_source_ok = (nop_inputs.is_dir() and not nop_inputs.is_symlink()
                     and any(_regular(path) for path in nop_inputs.iterdir()
                             if path.name != "nop.sh"))
    nop_ok = (
        _regular(nop_script, executable=True)
        and nop_source_ok
        and "WORKSPACE" in nop_text
        and artifact_name is not None and artifact_name in nop_text
    )
    generated_records = generated.get("file_records")
    identity_ok = isinstance(generated_records, list) and all(
        isinstance(item, dict) and set(item) == {"path", "sha256"}
        and _regular(task / item["path"])
        and common.sha256_file(task / item["path"]) == item["sha256"]
        for item in generated_records
    )
    checks = [
        _check("docx.3.separate_verifier", _nested(config, "verifier", "environment_mode") == "separate",
               "Harbor declares a real separate verifier."),
        _check("docx.3.sibling_contexts", contexts_ok,
               "Agent and verifier build contexts are real sibling directories with Dockerfiles."),
        _check("docx.3.network_policy", network_ok,
                "Agent and verifier declare no-network runtime; build-time public access needs an explicit justification."),
        _check("docx.3.stateless_verifier", stateless_ok,
               "The verifier is a one-shot executable rather than a persistent service."),
        _check("docx.3.environment_cleanup", cleanup_ok,
               "PATH is reset and interpreter/loader injection variables are cleared before scoring."),
        _check("docx.3.private_isolation", private_isolation_ok,
               "Verifier-private paths are absent from the agent build context."),
        _check("docx.4.snapshot", snapshot_ok,
               "Explicit /workspace artifacts define each submitted snapshot."),
        _check("docx.4.structured_report", structured_ok,
               "The verifier declares total score, validity, raw/normalized values, reward, and named checks."),
        _check("docx.4.local_feedback", local_ok,
               "A development split and executable local scorer are present in the agent context."),
        _check("docx.4.continuous_reward", continuous_ok,
               "Continuous score metadata matches the locked formula, constants, direction, and endpoints."),
        _check("docx.4.budget_resources", budget_ok,
               "Hours-long budget, verifier timeout, resources, and target architectures match the request."),
        _check("docx.5.reference_mode", reference_ok,
               "Reference mode supplies a runnable nonempty solution or cleanly marks open-solution."),
        _check("docx.5.nop_script", nop_ok,
               "An executable NOP script creates the declared snapshot in an isolated workspace."),
        _check("docx.6.pinned_bases", bases_ok,
               "Both Dockerfiles pin their bases by digest."),
        _check("generator.identity", identity_ok,
               "Generated task inputs still match the content-addressed candidate manifest."),
    ]
    complete = all(item["status"] == "complete" for item in checks)
    return {
        "schema_version": 1,
        "verdict": "complete" if complete else "failed",
        "state": "HARBOR_VALIDATED" if complete else "GENERATED",
        "task_sha256": task_sha256(task),
        "checks": checks,
        "validated_at": common.utc_now(),
    }


def validate(workspace: str | Path) -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    report = evaluate(root)
    attempt_number, attempt = common.next_attempt(root / "evidence/validations")
    report["attempt"] = attempt_number
    common.write_new_json(attempt / "report.json", report)
    common.write_latest_json(root / "evidence/harbor_validation.json", report)
    common.append_event(
        root,
        "harbor_validated",
        attempt=attempt_number,
        verdict=report["verdict"],
        task_sha256=report["task_sha256"],
    )
    return report, 0 if report["verdict"] == "complete" else 2
