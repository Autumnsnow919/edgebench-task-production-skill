#!/usr/bin/env python3
"""Capture real local verifier executions before final image measurements."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

import topify_common as common
import topify_validate


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _interior_ladder(scores: dict, strong: float, nop_hash: str, starter_hash: str) -> bool:
    """Permit a lawful zero-work starter only when NOP scores its exact bytes."""
    return (scores["nop"] <= scores["starter"] < scores["interior"] < strong
            and (scores["nop"] < scores["starter"]
                 or (isinstance(nop_hash, str) and common.HEX64.fullmatch(nop_hash)
                     and nop_hash == starter_hash)))


def _architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "linux/amd64"
    if machine in {"aarch64", "arm64"}:
        return "linux/arm64"
    return f"linux/{machine or 'unknown'}"


def _report(value: object) -> dict:
    if not isinstance(value, dict):
        raise common.PipelineError("verifier stdout must be one JSON object")
    required = {"total_score", "reward", "raw_metric", "normalized_score", "valid", "checks"}
    if not required.issubset(value):
        raise common.PipelineError("verifier report is missing required structured fields")
    for key in ("total_score", "reward", "raw_metric", "normalized_score"):
        if not common.finite_number(value.get(key)):
            raise common.PipelineError(f"verifier report {key} must be finite")
    if type(value.get("valid")) is not bool:
        raise common.PipelineError("verifier report valid must be Boolean")
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        or type(item.get("pass")) is not bool for item in checks
    ):
        raise common.PipelineError("verifier report checks must be named Boolean results")
    common.canonical_sha256(value)
    return value


def _run(task: Path, snapshot: Path, output: Path, timeout: int) -> tuple[dict, dict]:
    output.mkdir(parents=True)
    evaluator = (task / "tests/evaluate.py").resolve(strict=True)
    instances = (task / "tests/private/instances.json").resolve(strict=True)
    snapshot = snapshot.resolve(strict=True)
    argv = [
        sys.executable,
        "-I",
        "-B",
        str(evaluator),
        "--solution",
        str(snapshot),
        "--instances",
        str(instances),
    ]
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
    }
    started_at = _timestamp()
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=task,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    duration = time.monotonic() - started
    finished_at = _timestamp()
    stdout_path = output / "stdout.bin"
    stderr_path = output / "stderr.bin"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    parsed = None
    parse_error = None
    if not timed_out and process.returncode == 0:
        try:
            parsed = _report(json.loads(stdout.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, common.PipelineError) as exc:
            parse_error = str(exc)
    observation = {
        "schema_version": 1,
        "executor_scope": "development-local-host",
        "argv": argv,
        "cwd": str(task),
        "pid": process.pid,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": duration,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "architecture": _architecture(),
        "snapshot_path": str(snapshot),
        "snapshot_sha256": common.sha256_file(snapshot),
        "evaluator_sha256": common.sha256_file(evaluator),
        "instances_sha256": common.sha256_file(instances),
        "stdout_sha256": _bytes_sha256(stdout),
        "stderr_sha256": _bytes_sha256(stderr),
        "parse_error": parse_error,
    }
    common.write_new_json(output / "process.json", observation)
    if parsed is not None:
        common.write_new_json(output / "report.json", parsed)
    if timed_out:
        raise common.PipelineError(f"verifier timed out for snapshot {snapshot.name}")
    if process.returncode != 0:
        raise common.PipelineError(f"verifier exited {process.returncode} for snapshot {snapshot.name}")
    if parsed is None:
        raise common.PipelineError(f"verifier emitted an invalid report for {snapshot.name}: {parse_error}")
    return parsed, observation


def run_snapshot_script(
    task: Path,
    script_relative: str,
    output: Path,
    timeout: int,
    kind: str,
) -> tuple[Path, dict]:
    output.mkdir(parents=True)
    workspace = output / "workspace"
    workspace.mkdir()
    script = (task / script_relative).resolve(strict=True)
    argv = [str(script)]
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "WORKSPACE": str(workspace),
    }
    started_at = _timestamp()
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=task,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    snapshot = workspace / topify_validate.artifact_relative(task)
    observation = {
        "kind": kind,
        "argv": argv,
        "cwd": str(task),
        "pid": process.pid,
        "started_at": started_at,
        "finished_at": _timestamp(),
        "elapsed_seconds": time.monotonic() - started,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": _bytes_sha256(stdout),
        "stderr_sha256": _bytes_sha256(stderr),
        "script_sha256": common.sha256_file(script),
        "snapshot_sha256": common.sha256_file(snapshot) if snapshot.is_file() else None,
    }
    (output / "stdout.bin").write_bytes(stdout)
    (output / "stderr.bin").write_bytes(stderr)
    common.write_new_json(output / "process.json", observation)
    if timed_out:
        raise common.PipelineError(f"{kind} timed out")
    if process.returncode != 0:
        raise common.PipelineError(f"{kind} exited {process.returncode}")
    if not snapshot.is_file() or snapshot.is_symlink():
        raise common.PipelineError(f"{kind} did not create the declared solution snapshot")
    return snapshot, observation


def run_nop_script(task: Path, output: Path, timeout: int) -> tuple[Path, dict]:
    return run_snapshot_script(task, "nop/nop.sh", output, timeout, "nop-script")


def run_reference_script(task: Path, output: Path, timeout: int) -> tuple[Path, dict]:
    return run_snapshot_script(task, "solution/solve.sh", output, timeout, "reference-script")


def _check(check_id: str, status: str, detail: str) -> dict:
    return {"id": check_id, "status": status, "detail": detail}


def measure(workspace: str | Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    validation_path = root / "evidence/harbor_validation.json"
    validation = common.load_json(validation_path)
    current_task_sha256 = topify_validate.task_sha256(task)
    if validation.get("verdict") != "complete":
        raise common.PipelineError("Harbor validation must complete before measurement")
    if validation.get("task_sha256") != current_task_sha256:
        raise common.PipelineError("task changed after Harbor validation")

    _, attempt = common.next_attempt(root / "evidence/measurements")
    runs = attempt / "runs"
    runs.mkdir()
    timeout = metadata["request"]["verifier_timeout_seconds"]
    mode = metadata["request"]["reference_mode"]
    nop_snapshot, nop_script_observation = run_nop_script(task, runs / "nop-script", timeout)
    artifact = topify_validate.artifact_relative(task)
    suffix = Path(artifact).suffix
    snapshots = {
        "nop": nop_snapshot,
        "starter": task / "environment/workspace" / artifact,
        "interior": task / "tests/calibration" / f"interior{suffix}",
    }
    if mode == "reference":
        strong, reference_script_observation = run_reference_script(
            task, runs / "reference-script", timeout
        )
    else:
        strong = task / "tests/calibration" / f"strong{suffix}"
        reference_script_observation = None
    reports = {}
    observations = {}
    try:
        for name, snapshot in snapshots.items():
            reports[name], observations[name] = _run(task, snapshot, runs / name, timeout)
        repeat_reports = []
        repeat_observations = []
        for index in range(1, 6):
            report, observation = _run(task, strong, runs / f"reference-{index}", timeout)
            repeat_reports.append(report)
            repeat_observations.append(observation)
    except Exception:
        common.append_event(root, "measurement_failed", attempt=str(attempt.relative_to(root)))
        raise

    scores = {
        "nop": float(reports["nop"]["total_score"]),
        "starter": float(reports["starter"]["total_score"]),
        "interior": float(reports["interior"]["total_score"]),
        "reference": float(repeat_reports[0]["total_score"]) if mode == "reference" else "open-solution",
    }
    repeat_scores = [float(report["total_score"]) for report in repeat_reports]
    score_range = max(repeat_scores) - min(repeat_scores)
    scoring = metadata["request"]["scoring"]
    headroom = abs(float(scoring["upper"]) - float(scoring["baseline"]))
    lower_ok = scores["nop"] == float(scoring["baseline"])
    ladder_ok = _interior_ladder(
        scores, repeat_scores[0], observations["nop"]["snapshot_sha256"],
        observations["starter"]["snapshot_sha256"],
    )
    reference_ok = mode == "open-solution" or repeat_scores[0] == float(scoring["upper"])
    all_observations = [*observations.values(), *repeat_observations]
    process_ok = all(
        item["exit_code"] == 0 and not item["timed_out"] and item["parse_error"] is None
        for item in all_observations
    )
    repeat_ok = len(set(repeat_scores)) == 1
    durations = [item["elapsed_seconds"] for item in all_observations]
    checks = [
        _check("measure.nop_script", "complete",
               "The delivered NOP script executed successfully and created the scored snapshot."),
        _check(
            "measure.reference_script",
            "complete" if reference_script_observation is not None else "not_applicable",
            "Reference mode executes solve.sh once before five identical scores; open-solution has no script.",
        ),
        _check("measure.process_observation", "complete" if process_ok else "failed",
               "Every local score has an observed process, exit status, streams, and input hashes."),
        _check("measure.nop", "complete" if lower_ok else "failed",
               "The genuine NOP lands on the declared lower endpoint."),
        _check("measure.interior_ladder", "complete" if ladder_ok else "failed",
               "Starter and interior snapshots demonstrate non-binary score headroom."),
        _check("measure.reference_repeat", "complete" if repeat_ok and reference_ok else "failed",
               "The exact same strong snapshot scores identically five times."),
        _check("measure.timing", "complete",
               "Local process startup and end-to-end verifier times were measured."),
        _check("measure.final_images", "incomplete",
               "These host diagnostics must be rerun through the final verifier image."),
        _check("measure.cross_architecture", "incomplete",
               "A separately observed native score on the other target architecture is still required."),
        _check("measure.batch_nop_scan", "incomplete",
               "The per-task NOP ran; the delivery-batch NOP scan is not available yet."),
    ]
    report = {
        "schema_version": 1,
        "verdict": "incomplete",
        "state": "MEASUREMENT_PARTIAL",
        "scope": "development-local-host",
        "task_sha256": current_task_sha256,
        "architecture": _architecture(),
        "scores": scores,
        "reference_mode": mode,
        "reference_repeat": {
            "scores": repeat_scores,
            "range": score_range,
            "relative_range": score_range / headroom,
            "snapshot_sha256": repeat_observations[0]["snapshot_sha256"],
        },
        "timing": {
            "cold_start_seconds": all_observations[0]["elapsed_seconds"],
            "typical_end_to_end_seconds": statistics.median(durations),
            "maximum_end_to_end_seconds": max(durations),
            "declared_timeout_seconds": timeout,
        },
        "nop_script": nop_script_observation,
        "reference_script": reference_script_observation,
        "reference_submission_count": 1 if reference_script_observation is not None else "open-solution",
        "checks": checks,
        "attempt": str(attempt.relative_to(root)),
        "measured_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.append_event(root, "measurement_partial", attempt=report["attempt"], task_sha256=current_task_sha256)
    return report, 2
