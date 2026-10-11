#!/usr/bin/env python3
"""Produce a fail-closed DOCX completion matrix for one task workspace."""

from __future__ import annotations

import os
from pathlib import Path

import topify_build
import topify_common as common
import topify_profile
import topify_validate


def _latest(parent: Path) -> dict:
    pointer = parent / "latest.json"
    if pointer.is_file() and not pointer.is_symlink():
        try:
            return common.load_json(pointer)
        except common.PipelineError:
            return {}
    paths = sorted(parent.glob("attempt-*/summary.json"), reverse=True)
    for path in paths:
        try:
            return common.load_json(path)
        except common.PipelineError:
            continue
    return {}


def _row(row_id: str, status: str, detail: str, evidence: object = None) -> dict:
    return {"id": row_id, "status": status, "detail": detail, "evidence": evidence}


def hash_data(workspace: str | Path) -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    records = []
    for path in sorted(task.rglob("*")):
        if path.is_symlink():
            raise common.PipelineError(f"task contains a symlink: {path.relative_to(task)}")
        if path.is_file():
            records.append({
                "path": str(path.relative_to(task)),
                "sha256": common.sha256_file(path),
                "bytes": path.stat().st_size,
            })
    attempt_number, attempt = common.next_attempt(root / "evidence/data-hashes")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": "complete",
        "task_sha256": task_sha256,
        "file_count": len(records),
        "files": records,
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "data_hashes_recorded", attempt=attempt_number, file_count=len(records))
    return report, 0


def record_resource_agreement(workspace: str | Path, input_path: str | Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    source = common.load_json(input_path)
    required = {"schema_version", "task_sha256", "resources", "agreed_by", "agreed_at", "statement"}
    if set(source) != required or source.get("schema_version") != 1:
        raise common.PipelineError(f"resource agreement keys must be exactly {sorted(required)}")
    if source.get("task_sha256") != task_sha256:
        raise common.PipelineError("resource agreement does not identify the current task bytes")
    if source.get("resources") != metadata["request"]["resources"]:
        raise common.PipelineError("resource agreement does not match the declared resource envelope")
    if (
        not isinstance(source.get("agreed_by"), list)
        or not source["agreed_by"]
        or any(not isinstance(item, str) or not item.strip() for item in source["agreed_by"])
        or not isinstance(source.get("agreed_at"), str)
        or not source["agreed_at"].strip()
        or not isinstance(source.get("statement"), str)
        or not source["statement"].strip()
    ):
        raise common.PipelineError("resource agreement must identify parties, time, and a nonempty statement")
    attempt_number, attempt = common.next_attempt(root / "evidence/resource-agreement")
    report = {
        **source,
        "attempt": attempt_number,
        "status": "complete",
        "source_sha256": common.sha256_file(input_path),
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "resource_agreement_recorded", attempt=attempt_number)
    return report, 0


def check_package(
    workspace: str | Path,
    annex_path: str | Path,
    archive_path: str | Path,
    checker_path: str | Path,
    timeout_seconds: int,
) -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise common.PipelineError("package checker timeout must be a positive integer")
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    annex_input = Path(annex_path)
    archive_input = Path(archive_path)
    checker_input = Path(checker_path)
    annex_sha256 = common.sha256_file(annex_input)
    archive_sha256 = common.sha256_file(archive_input)
    checker_sha256 = common.sha256_file(checker_input)
    annex = annex_input.resolve(strict=True)
    archive = archive_input.resolve(strict=True)
    checker = checker_input.resolve(strict=True)
    if not os.access(checker, os.X_OK):
        raise common.PipelineError("package checker must be an executable regular file")

    attempt_number, attempt = common.next_attempt(root / "evidence/package-conformance")
    argv = [
        str(checker),
        "--annex", str(annex),
        "--archive", str(archive),
        "--task-root", str(task.resolve(strict=True)),
        "--task-sha256", task_sha256,
    ]
    process, stdout, stderr = topify_build._run_process(
        argv,
        root,
        timeout_seconds,
        common.sanitized_env(),
    )
    stdout_path = attempt / "stdout.json"
    stderr_path = attempt / "stderr.txt"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    process = {
        **process,
        "task_sha256": task_sha256,
        "annex_sha256": annex_sha256,
        "archive_sha256": archive_sha256,
        "checker_sha256": checker_sha256,
    }
    common.write_new_json(attempt / "process.json", process)

    checker_report = None
    reason = None
    if process.get("returncode") != 0:
        reason = process.get("error") or f"package checker exited {process.get('returncode')}"
    else:
        try:
            checker_report = common.load_json(stdout_path)
        except common.PipelineError as exc:
            reason = f"package checker did not emit one JSON object: {exc}"
    required_report = {
        "schema_version", "status", "task_sha256", "annex_sha256", "archive_sha256", "checks",
    }
    if reason is None:
        if (set(checker_report) != required_report or checker_report.get("schema_version") != 1
                or type(checker_report.get("schema_version")) is not int):
            reason = f"package checker report keys must be exactly {sorted(required_report)}"
        elif checker_report.get("status") != "complete":
            reason = "package checker did not report complete"
        elif checker_report.get("task_sha256") != task_sha256:
            reason = "package checker report task hash does not match current task bytes"
        elif checker_report.get("annex_sha256") != annex_sha256:
            reason = "package checker report annex hash does not match supplied annex"
        elif checker_report.get("archive_sha256") != archive_sha256:
            reason = "package checker report archive hash does not match supplied archive"
        else:
            checks = checker_report.get("checks")
            if (
                not isinstance(checks, list) or not checks
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"id", "status", "detail"}
                    or not isinstance(item.get("id"), str) or not item["id"].strip()
                    or item.get("status") != "complete"
                    or not isinstance(item.get("detail"), str) or not item["detail"].strip()
                    for item in checks
                )
            ):
                reason = "package checker report needs nonempty complete structured checks"

    status = "complete" if reason is None else "failed"
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": status,
        "task_sha256": task_sha256,
        "annex_path": str(annex),
        "annex_sha256": annex_sha256,
        "archive_path": str(archive),
        "archive_sha256": archive_sha256,
        "checker_path": str(checker),
        "checker_sha256": checker_sha256,
        "checker_report": checker_report,
        "process_sha256": common.sha256_file(attempt / "process.json"),
        "reason": reason,
        "checked_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "package_conformance_checked", attempt=attempt_number, status=status)
    return report, 0 if status == "complete" else 2


def _package_files_match(root: Path, report: dict) -> bool:
    for path_key, hash_key in (("annex_path", "annex_sha256"), ("archive_path", "archive_sha256")):
        value = report.get(path_key)
        digest = report.get(hash_key)
        if not isinstance(value, str) or not isinstance(digest, str):
            return False
        path = Path(value)
        try:
            if path.is_symlink() or path.resolve(strict=True) != path or common.sha256_file(path) != digest:
                return False
        except (common.PipelineError, OSError):
            return False
    attempt = report.get("attempt")
    process_sha256 = report.get("process_sha256")
    if type(attempt) is not int or attempt <= 0 or not isinstance(process_sha256, str):
        return False
    process_path = root / f"evidence/package-conformance/attempt-{attempt:04d}/process.json"
    try:
        if common.sha256_file(process_path) != process_sha256:
            return False
    except (common.PipelineError, OSError):
        return False
    return True


def completion_report(workspace: str | Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    validation_path = root / "evidence/harbor_validation.json"
    validation = common.load_json(validation_path) if validation_path.is_file() else {}
    static_ok = validation.get("verdict") == "complete" and validation.get("task_sha256") == task_sha256
    measurement = _latest(root / "evidence/measurements")
    measurement_checks = {
        item.get("id"): item.get("status")
        for item in measurement.get("checks", [])
        if isinstance(item, dict)
    }
    local_required = [
        "measure.nop_script",
        "measure.process_observation",
        "measure.nop",
        "measure.interior_ladder",
        "measure.reference_repeat",
        "measure.timing",
    ]
    if metadata["request"]["reference_mode"] == "reference":
        local_required.append("measure.reference_script")
    local_measurement_ok = (
        measurement.get("task_sha256") == task_sha256
        and all(
            measurement_checks.get(check_id) == "complete"
            for check_id in local_required
        )
    )
    build = _latest(root / "evidence/builds")
    build_ok = build.get("status") == "complete" and build.get("task_sha256") == task_sha256
    base_ok = build_ok and bool(build.get("base_images")) and all(
        set(item.get("platforms", [])) >= common.ARCHITECTURES
        for item in build.get("base_images", [])
        if isinstance(item, dict)
    )
    native = {}
    native_reports = {}
    for architecture in common.ARCHITECTURES:
        value = _latest(root / "evidence/image-measurements" / architecture.replace("/", "-"))
        native_reports[architecture] = value
        native[architecture] = (
            value.get("status") == "complete"
            and value.get("task_sha256") == task_sha256
            and value.get("requested_architecture") == architecture
            and value.get("observed_host_architecture") == architecture
        )
    cross_arch_scores = {
        architecture: value.get("scores")
        for architecture, value in native_reports.items()
    }
    score_values = list(cross_arch_scores.values())
    cross_arch_ok = all(native.values()) and len(score_values) == 2 and score_values[0] == score_values[1]
    baseline = float(metadata["request"]["scoring"]["baseline"])
    nop_per_task_ok = any(native.values()) and all(
        not native[architecture]
        or native_reports[architecture].get("scores", {}).get("nop") == baseline
        for architecture in native
    )
    rollout = _latest(root / "evidence/rollout-collections")
    profile_receipt = topify_profile.current(root, task_sha256)
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    required_trials = topify_profile.threshold(profile, "frontier_trials_minimum", 3)
    rollout_ok = (
        rollout.get("status") == "complete"
        and rollout.get("task_sha256") == task_sha256
        and rollout.get("completed_run_count", 0) >= required_trials
    )
    data_hashes = _latest(root / "evidence/data-hashes")
    data_hashes_ok = data_hashes.get("status") == "complete" and data_hashes.get("task_sha256") == task_sha256
    batch_nop = _latest(root / "evidence/batch-nop")
    batch_nop_ok = batch_nop.get("status") == "complete" and task_sha256 in batch_nop.get("task_sha256s", [])
    resource_agreement = _latest(root / "evidence/resource-agreement")
    resource_agreement_ok = (
        resource_agreement.get("status") == "complete"
        and resource_agreement.get("task_sha256") == task_sha256
    )
    package_conformance = _latest(root / "evidence/package-conformance")
    package_ok = (
        package_conformance.get("status") == "complete"
        and package_conformance.get("task_sha256") == task_sha256
        and _package_files_match(root, package_conformance)
    )
    package_status = (
        "complete" if package_ok else "failed"
        if package_conformance.get("status") == "failed"
        or package_conformance.get("status") == "complete"
        and package_conformance.get("task_sha256") == task_sha256
        else "incomplete"
    )
    source = metadata["request"]["source"]
    required_catalogs = {"EdgeBench", "MLS-Bench", "Terminal-Bench", "GDPval"}
    provenance_ok = required_catalogs.issubset(source.get("dedup_targets", []))
    rights_ok = bool(source.get("license") and source.get("redistribution_confirmation"))
    reference_ok = static_ok
    rows = [
        _row("docx.2.category", "complete", "Request declares one of the six DOCX categories.", metadata["request"]["category"]),
        _row("docx.3.harbor_static", "complete" if static_ok else "incomplete", "Current Harbor bytes must pass all separate-verifier, no-network, stateless, and hardening checks.", validation_path.as_posix() if static_ok else None),
        _row("docx.4.learning_loop", "complete" if static_ok else "incomplete", "Snapshot, actionable local feedback, continuous formula, endpoints, and hours-long budget are statically bound.", validation_path.as_posix() if static_ok else None),
        _row("docx.4.local_measurement", "complete" if local_measurement_ok else "incomplete", "Real local score ladder and end-to-end timing are required.", measurement.get("attempt")),
        _row("docx.5.reference_repeat", "complete" if local_measurement_ok else "incomplete", "Same strong snapshot must be scored five times with range and relative range.", measurement.get("reference_repeat")),
        _row("docx.5.cross_architecture", "complete" if cross_arch_ok else "incomplete", "Native final-image scores are required on amd64 and arm64 and their fixed snapshots must compare identically; emulation does not count.", {"native": native, "scores": cross_arch_scores}),
        _row("docx.5.nop_per_task", "complete" if nop_per_task_ok else "incomplete", "The genuine NOP must score at the lower endpoint through at least one native final verifier image.", {architecture: value.get("scores", {}).get("nop") for architecture, value in native_reports.items()}),
        _row("docx.5.batch_nop", "complete" if batch_nop_ok else "incomplete", "A delivery-batch NOP scan is required in addition to per-task NOP evidence.", batch_nop.get("attempt")),
        _row("docx.5.reference_mode", "complete" if reference_ok else "incomplete", "Runnable reference or explicit open-solution mode must be current.", metadata["request"]["reference_mode"]),
        _row("docx.5.frontier_rollouts", "complete" if rollout_ok else "incomplete", "At least three fixed-config, full-budget SForge runs and their actual mean are required.", rollout.get("mean_best_score")),
        _row("docx.5.provenance_dedup", "complete" if provenance_ok else "incomplete", "Source lineage must cover the four named public benchmark comparisons and retained Topify IDs when supplied.", source.get("dedup_targets")),
        _row("docx.5.redistribution_rights", "complete" if rights_ok else "incomplete", "License and written Topify redistribution confirmation are required.", source.get("license")),
        _row("docx.6.multiarch_build", "complete" if build_ok else "incomplete", "Agent and verifier OCI layouts must verify both target architectures.", build.get("attempt")),
        _row("docx.6.base_manifest_list", "complete" if base_ok else "incomplete", "Pinned base manifest-list digest must be inspected and cover both architectures.", build.get("base_images")),
        _row("docx.7.resources_declared", "complete", "CPU, memory, accelerator, architecture, and scoring GPU use are declared.", metadata["request"]["resources"]),
        _row("docx.7.resources_agreed", "complete" if resource_agreement_ok else "incomplete", "External agreement on the resource envelope must be retained.", resource_agreement.get("attempt")),
        _row("docx.8.file_hashes", "complete" if data_hashes_ok else "incomplete", "A SHA-256 inventory for every delivered task file is required.", data_hashes.get("file_count")),
        _row("docx.8.package_annex", package_status, "Contract naming/directory/archive annex conformance must come from an executed supplied checker.", package_conformance.get("attempt")),
    ]
    failed = any(item["status"] == "failed" for item in rows)
    complete = all(item["status"] == "complete" for item in rows)
    status = "failed" if failed else "complete" if complete else "incomplete"
    attempt_number, attempt = common.next_attempt(root / "evidence/completion")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": status,
        "task_sha256": task_sha256,
        "rows": rows,
        "complete_count": sum(item["status"] == "complete" for item in rows),
        "row_count": len(rows),
        "delivery_archive": package_conformance.get("archive_path") if complete else None,
        "score_table": {
            "nop": next(
                (
                    native_reports[architecture].get("scores", {}).get("nop")
                    for architecture in sorted(native_reports)
                    if native[architecture]
                ),
                measurement.get("scores", {}).get("nop"),
            ),
            "reference": measurement.get("scores", {}).get("reference"),
            "reference_submission_count": measurement.get("reference_submission_count"),
            "frontier_model": metadata["request"]["rollout"],
            "frontier_completed_runs": rollout.get("completed_run_count", 0),
            "frontier_mean_best_score": rollout.get("mean_best_score"),
        },
        "reported_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "completion_reported", attempt=attempt_number, status=status)
    return report, 0 if complete else 2
