#!/usr/bin/env python3
"""Measure the final verifier image on the host's native architecture."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time

import topify_build
import topify_common as common
import topify_measure
import topify_validate


def native_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "linux/amd64"
    if machine in {"aarch64", "arm64"}:
        return "linux/arm64"
    return f"linux/{machine or 'unknown'}"


def _latest_complete_build(root: Path, task_sha256: str) -> dict:
    candidates = sorted((root / "evidence/builds").glob("attempt-*/summary.json"), reverse=True)
    for path in candidates:
        report = common.load_json(path)
        if report.get("status") == "complete" and report.get("task_sha256") == task_sha256:
            images = report.get("images")
            if isinstance(images, list) and {item.get("name") for item in images if isinstance(item, dict)} == {
                "agent", "verifier"
            }:
                return report
    raise common.PipelineError("no complete multi-platform build matches the current task")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _container_score(
    docker: str,
    image: str,
    architecture: str,
    snapshot: Path,
    output: Path,
    resources: dict,
    timeout: int,
    artifact: str = "solution.json",
) -> tuple[dict, dict]:
    started_at = common.utc_now()
    started = time.monotonic()
    output.mkdir(parents=True)
    staged = output / "workspace"
    staged.mkdir()
    destination = staged / artifact
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, destination)
    argv = [
        docker,
        "run",
        "--rm",
        "--platform",
        architecture,
        "--network",
        "none",
        "--read-only",
        "--pids-limit",
        "128",
        "--cpus",
        str(resources["verifier_cpu"]),
        "--memory",
        f"{resources['verifier_memory_mb']}m",
        "--mount",
        f"type=bind,src={staged},dst=/workspace,readonly",
        "--entrypoint",
        "/tests/test.sh",
        image,
    ]
    process = subprocess.Popen(
        argv,
        cwd=output,
        env=common.sanitized_env(),
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
    elapsed = time.monotonic() - started
    observation = {
        "argv": argv,
        "pid": process.pid,
        "started_at": started_at,
        "finished_at": common.utc_now(),
        "elapsed_seconds": elapsed,
        "measurement_scope": "snapshot-staging-and-container-grading",
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
        "snapshot_sha256": common.sha256_file(snapshot),
        "image": image,
        "architecture": architecture,
    }
    common.write_new_json(output / "process.json", observation)
    if timed_out:
        raise common.PipelineError(f"final verifier image timed out for {snapshot.name}")
    if process.returncode != 0:
        raise common.PipelineError(f"final verifier image exited {process.returncode} for {snapshot.name}")
    try:
        parsed = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise common.PipelineError(f"final verifier image emitted invalid JSON: {exc}") from exc
    report = topify_measure._report(parsed)
    common.write_new_json(output / "report.json", report)
    return report, observation


def measure_image(
    workspace: str | Path,
    architecture: str | None = None,
    docker_executable: str = "docker",
    build_timeout_seconds: int = 1800,
) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    validation = common.load_json(root / "evidence/harbor_validation.json")
    if validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256:
        raise common.PipelineError("current Harbor validation is not complete")
    build = _latest_complete_build(root, task_sha256)
    requested = architecture or native_architecture()
    if requested not in common.ARCHITECTURES:
        raise common.PipelineError("architecture must be linux/amd64 or linux/arm64")
    observed = native_architecture()
    attempt_number, attempt = common.next_attempt(
        root / "evidence/image-measurements" / requested.replace("/", "-")
    )
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": "incomplete",
        "state": "BUILT",
        "scope": "final-verifier-image-native",
        "task_sha256": task_sha256,
        "build_attempt": build["attempt"],
        "requested_architecture": requested,
        "observed_host_architecture": observed,
        "processes": [],
        "started_at": common.utc_now(),
    }

    def finish(reason: str, code: int = 2) -> tuple[dict, int]:
        report["reason"] = reason
        report["finished_at"] = common.utc_now()
        common.write_new_json(attempt / "summary.json", report)
        common.write_latest_json(attempt.parent / "latest.json", report)
        common.append_event(
            root,
            "image_measurement",
            architecture=requested,
            attempt=attempt_number,
            status=report["status"],
        )
        return report, code

    if requested != observed:
        return finish(
            f"requested {requested}, but this is a {observed} native host; emulation is not cross-architecture evidence"
        )

    build_network = topify_build.declared_build_network(task)
    image = f"topify-local/{task_sha256[:20]}-verifier-{requested.rsplit('/', 1)[-1]}:{attempt_number}"
    build_argv = [
        docker_executable,
        "buildx",
        "build",
        "--platform",
        requested,
        "--network",
        build_network,
        "--provenance=false",
        "--sbom=false",
        "--load",
        "--tag",
        image,
        str(task / "tests"),
    ]
    build_record, _, _ = topify_build._run_process(
        build_argv, root, build_timeout_seconds, common.sanitized_env()
    )
    report["processes"].append(build_record)
    if build_record.get("returncode") != 0:
        return finish("native verifier image build/load did not complete")

    inspect_record, inspect_stdout, _ = topify_build._run_process(
        [docker_executable, "image", "inspect", image], root, 60, common.sanitized_env()
    )
    report["processes"].append(inspect_record)
    if inspect_record.get("returncode") != 0:
        return finish("could not inspect the loaded verifier image")
    try:
        inspected = json.loads(inspect_stdout)
        details = inspected[0]
        image_architecture = f"{str(details['Os']).lower()}/{str(details['Architecture']).lower()}"
        image_id = details["Id"]
    except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report["status"] = "failed"
        return finish(f"invalid docker image inspection: {exc}")
    if image_architecture != requested or not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        report["status"] = "failed"
        return finish("loaded verifier image identity or architecture does not match the request")
    report["image"] = {"tag": image, "id": image_id, "architecture": image_architecture}

    mode = metadata["request"]["reference_mode"]
    artifact = topify_validate.artifact_relative(task)
    suffix = Path(artifact).suffix
    runs = attempt / "runs"
    runs.mkdir()
    try:
        nop_snapshot, nop_script_observation = topify_measure.run_nop_script(
            task,
            runs / "nop-script",
            metadata["request"]["verifier_timeout_seconds"],
        )
    except (common.PipelineError, OSError) as exc:
        return finish(str(exc))
    report["processes"].append(nop_script_observation)
    snapshots = {
        "nop": nop_snapshot,
        "starter": task / "environment/workspace" / artifact,
        "interior": task / "tests/calibration" / f"interior{suffix}",
    }
    if mode == "reference":
        try:
            strong, reference_script_observation = topify_measure.run_reference_script(
                task,
                runs / "reference-script",
                metadata["request"]["verifier_timeout_seconds"],
            )
        except (common.PipelineError, OSError) as exc:
            return finish(str(exc))
        report["processes"].append(reference_script_observation)
        report["reference_submission_count"] = 1
    else:
        strong = task / "tests/calibration" / f"strong{suffix}"
        report["reference_submission_count"] = "open-solution"
    score_reports = {}
    observations = []
    try:
        for name, snapshot in snapshots.items():
            score_reports[name], observation = _container_score(
                docker_executable,
                image,
                requested,
                snapshot,
                runs / name,
                metadata["request"]["resources"],
                metadata["request"]["verifier_timeout_seconds"],
                artifact=artifact,
            )
            observations.append(observation)
        repeat_reports = []
        for index in range(1, 6):
            item, observation = _container_score(
                docker_executable,
                image,
                requested,
                strong,
                runs / f"reference-{index}",
                metadata["request"]["resources"],
                metadata["request"]["verifier_timeout_seconds"],
                artifact=artifact,
            )
            repeat_reports.append(item)
            observations.append(observation)
    except (common.PipelineError, OSError) as exc:
        return finish(str(exc))

    scores = {
        name: float(value["total_score"])
        for name, value in score_reports.items()
    }
    repeat_scores = [float(item["total_score"]) for item in repeat_reports]
    scoring = metadata["request"]["scoring"]
    headroom = abs(float(scoring["upper"]) - float(scoring["baseline"]))
    valid = (
        scores["nop"] == float(scoring["baseline"])
        and topify_measure._interior_ladder(
            scores, repeat_scores[0], observations[0]["snapshot_sha256"],
            observations[1]["snapshot_sha256"],
        )
        and min(repeat_scores) > scores["interior"]
        and all(math.isfinite(item) for item in [*scores.values(), *repeat_scores])
    )
    if not valid:
        report["status"] = "failed"
        report["scores"] = scores
        report["reference_repeat"] = repeat_scores
        return finish("final verifier image score ladder or repeated reference check failed")
    durations = [item["elapsed_seconds"] for item in observations]
    report.update({
        "status": "complete",
        "state": "IMAGE_MEASURED",
        "scores": {**scores, "reference": repeat_scores[0] if mode == "reference" else "open-solution"},
        "reference_repeat": {
            "scores": repeat_scores,
            "range": max(repeat_scores) - min(repeat_scores),
            "relative_range": (max(repeat_scores) - min(repeat_scores)) / headroom,
            "snapshot_sha256": observations[-1]["snapshot_sha256"],
        },
        "timing": {
            "cold_start_seconds": durations[0],
            "typical_end_to_end_seconds": statistics.median(durations),
            "maximum_end_to_end_seconds": max(durations),
        },
    })
    return finish("Final verifier image passed the native score ladder and five-repeat check.", code=0)
