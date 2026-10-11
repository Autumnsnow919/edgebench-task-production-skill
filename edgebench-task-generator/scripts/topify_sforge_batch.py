#!/usr/bin/env python3
"""Execute each delivered NOP script through the frozen SForge judge as a batch."""

from __future__ import annotations

from pathlib import Path
import re

import topify_build
import topify_common as common
import topify_delivery_contract
import topify_measure
import topify_owner_profile
import topify_sforge
import topify_validate


BATCH_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def batch_nop(
    manifest_path: str | Path, output_dir: str | Path,
    sforge_executable: str = "sforge",
) -> tuple[dict, int]:
    manifest = common.load_json(manifest_path)
    if (set(manifest) not in ({"schema_version", "batch_id", "workspaces"},
                               {"schema_version", "batch_id", "workspaces", "delivery_language"},
                               {"schema_version", "batch_id", "workspaces", "delivery_language", "delivery_profile"},
                               {"schema_version", "batch_id", "workspaces", "delivery_format"},
                               {"schema_version", "batch_id", "workspaces", "delivery_format", "delivery_language"})
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("delivery_language", "en") not in {"en-zh", "en"}
            or not isinstance(manifest.get("batch_id"), str) or not BATCH_ID.fullmatch(manifest["batch_id"])
            or not isinstance(manifest.get("workspaces"), list) or not manifest["workspaces"]):
        raise common.PipelineError("SForge NOP needs a valid batch delivery manifest")
    topify_delivery_contract.manifest_format(manifest, "sforge-only")
    owner_profile = manifest.get("delivery_profile") == topify_owner_profile.PROFILE_ID
    if "delivery_profile" in manifest and not owner_profile:
        raise common.PipelineError("unsupported SForge NOP delivery profile")
    output = Path(output_dir).absolute()
    if not output.is_dir() or output.is_symlink() or output.resolve(strict=True) != output:
        raise common.PipelineError("SForge batch evidence directory must exist and be canonical")
    planned = []
    seen = set()
    for workspace in manifest["workspaces"]:
        root, metadata = common.load_workspace(workspace)
        task_sha = topify_validate.task_sha256(root / "task")
        if task_sha in seen:
            raise common.PipelineError("SForge batch NOP contains duplicate task bytes")
        seen.add(task_sha)
        adapter_path = root / "evidence/sforge-adapters/latest.json"
        adapter = common.load_json(adapter_path)
        if adapter.get("status") != "complete" or adapter.get("task_sha256") != task_sha:
            raise common.PipelineError("SForge batch NOP needs a frozen, parity-tested task")
        prepared = adapter.get("adapter", {})
        tasks_dir = Path(prepared.get("tasks_dir", ""))
        if not tasks_dir.is_dir() or tasks_dir.is_symlink() or not prepared.get("task_id"):
            raise common.PipelineError("prepared SForge tasks directory is unavailable")
        if owner_profile:
            definition = tasks_dir / f"{prepared['task_id']}.json"
            if not definition.is_file() or definition.is_symlink():
                raise common.PipelineError("owner SForge NOP needs a frozen task JSON")
            topify_owner_profile.load_profile(
                manifest, root, task_sha, common.sha256_file(definition), metadata["task_id"])
        planned.append((root, metadata, task_sha, tasks_dir, prepared["task_id"]))
    task_hashes = sorted(seen)
    number, attempt = common.next_attempt(output / manifest["batch_id"])
    results = []
    for root, metadata, task_sha, tasks_dir, task_id in planned:
        run = attempt / task_sha[:20]
        run.mkdir()
        task = root / "task"
        script = task / "nop/nop.sh"
        try:
            snapshot, nop_process = topify_measure.run_nop_script(
                task, run / "nop-script", metadata["request"]["verifier_timeout_seconds"]
            )
            archive = run / "submission.tar.gz"
            topify_sforge._make_archive(archive, snapshot,
                                       topify_validate.artifact_relative(task))
            command = [
                sforge_executable, "--tasks-dir", str(tasks_dir),
                "--log-dir", str(run / "logs"), "eval", "--task", task_id,
                "--archive", str(archive), "--run-id", f"batch-nop-{task_sha[:12]}",
                "--timeout", str(metadata["request"]["verifier_timeout_seconds"]), "--json",
            ]
            process, stdout, stderr = topify_build._run_process(
                command, root, metadata["request"]["verifier_timeout_seconds"] + 60, common.sanitized_env()
            )
            (run / "stdout.json").write_bytes(stdout)
            (run / "stderr.txt").write_bytes(stderr)
            common.write_new_json(run / "sforge-process.json", process)
            if process.get("returncode") != 0:
                raise common.PipelineError("SForge NOP judge invocation did not finish")
            scored = topify_sforge._extract_json_report(stdout)
            score = scored.get("score_0_100")
            expected = metadata["request"]["scoring"]["baseline"]
            complete = (scored.get("task_id") == task_id and scored.get("valid") is True and type(score) in (int, float)
                        and score == expected)
            results.append({
                "task_id": metadata["task_id"], "task_sha256": task_sha,
                "status": "complete" if complete else "failed",
                "sforge_nop_score": score,
                "expected_lower_bound": expected,
                "nop_script_sha256": common.sha256_file(script),
                "snapshot_sha256": nop_process["snapshot_sha256"],
                "report_sha256": common.sha256_file(run / "stdout.json"),
                "process_sha256": common.sha256_file(run / "sforge-process.json"),
            })
        except (OSError, ValueError, TypeError) as exc:
            results.append({"task_id": metadata["task_id"], "task_sha256": task_sha,
                            "status": "failed", "reason": str(exc)})
    batch_complete = all(item["status"] == "complete" for item in results)
    report = {
        "schema_version": 1, "batch_id": manifest["batch_id"], "attempt": number,
        "status": "complete" if batch_complete else "failed",
        "task_sha256s": task_hashes, "results": results, "scanned_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    batch_report_sha = common.sha256_file(attempt / "summary.json")
    for root, metadata, task_sha, tasks_dir, task_id in planned:
        result = next(item for item in results if item["task_sha256"] == task_sha)
        n, task_attempt = common.next_attempt(root / "evidence/sforge-batch-nop")
        receipt = {
            "schema_version": 1, "attempt": n,
            "status": result["status"] if batch_complete else "incomplete",
            "task_sha256": task_sha, "task_sha256s": task_hashes,
            "batch_report_sha256": batch_report_sha,
            "batch_report_path": str(attempt / "summary.json"),
            "sforge_nop_score": result.get("sforge_nop_score"),
            "nop_script_sha256": result.get("nop_script_sha256"),
            "report_sha256": result.get("report_sha256"),
        }
        common.write_new_json(task_attempt / "summary.json", receipt)
        common.write_latest_json(task_attempt.parent / "latest.json", receipt)
    return report, 0 if batch_complete else 2
