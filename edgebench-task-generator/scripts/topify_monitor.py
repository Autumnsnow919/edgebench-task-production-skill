#!/usr/bin/env python3
"""One-shot SForge rollout observation for periodic execution (never sleeps)."""

from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import math
import os
from pathlib import Path
import subprocess

import topify_common as common
import topify_validate


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _progress(task: Path, started: float, budget: int) -> tuple[list[dict], list[str]]:
    observations = []
    problems = []
    for report_path in sorted((task / "submissions").glob("*/report.json")):
        try:
            if report_path.is_symlink() or not report_path.resolve(strict=True).is_relative_to(task):
                raise common.PipelineError("unsafe submission path")
            report = common.load_json(report_path)
            score = report.get("score_0_100")
            submitted_at = report.get("submitted_at")
            if (report.get("valid") is not True or not common.finite_number(score)
                    or not 0 <= score <= 100 or not common.finite_number(submitted_at)):
                raise common.PipelineError("invalid score or time")
            elapsed = float(submitted_at) - started
            if not 0 <= elapsed <= budget + 300:
                raise common.PipelineError("score timestamp outside run budget")
            observations.append({
                "round": report_path.parent.name,
                "elapsed_seconds": elapsed,
                "score": float(score),
                "report_sha256": common.sha256_file(report_path),
            })
        except (common.PipelineError, OSError, ValueError, TypeError):
            problems.append(report_path.parent.name)
    observations.sort(key=lambda item: (item["elapsed_seconds"], item["round"]))
    current = None
    for entry in observations:
        current = entry["score"] if current is None else max(current, entry["score"])
        entry["best_so_far"] = current
    return observations, problems


def observe(workspace: str | Path, log_dir: str | Path, run_id: str, now: float | None = None) -> dict:
    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    adapter = common.load_json(root / "evidence/sforge-adapters/latest.json")
    if adapter.get("status") != "complete" or adapter.get("task_sha256") != task_sha256:
        raise common.PipelineError("current SForge adapter is required for rollout observation")
    task_id = adapter["adapter"]["task_id"]
    import topify_rollout

    if not topify_rollout.RUN_ID.fullmatch(run_id):
        raise common.PipelineError("invalid SForge run ID")
    logs = Path(log_dir).resolve(strict=True)
    task = logs / "runs" / run_id / task_id
    if not task.is_dir() or task.is_symlink() or not task.resolve(strict=True).is_relative_to(logs):
        raise common.PipelineError("run directory for this task is unavailable")
    config_path = task / "run_config.json"
    config = common.load_json(config_path)
    expected = metadata["request"]["rollout"]
    budget = metadata["request"]["interaction_budget_seconds"]
    if (config.get("task_id") != task_id or config.get("timeout") != budget
            or any(config.get(field) != expected[field] for field in ("model", "agent", "effort"))):
        raise common.PipelineError("run configuration does not match the frozen task and model")
    started_lines = (task / "started_at").read_text(encoding="utf-8").splitlines()
    if len(started_lines) < 2:
        raise common.PipelineError("run start timestamp is missing")
    started = float(started_lines[1])
    if not math.isfinite(started):
        raise common.PipelineError("run start timestamp is not finite")
    elapsed = max(0, (datetime.now(timezone.utc).timestamp() if now is None else now) - started)
    trajectory, invalid_rounds = _progress(task, started, budget)
    best = trajectory[-1]["best_so_far"] if trajectory else None
    reasons = []
    if invalid_rounds:
        reasons.append("submission_report_invalid")
    if not trajectory and elapsed >= budget / 3:
        reasons.append("no_valid_submission")
    if trajectory and elapsed >= budget / 3:
        upper = float(metadata["request"]["scoring"]["upper"])
        baseline = float(metadata["request"]["scoring"]["baseline"])
        if best is not None and upper > baseline and best >= upper:
            reasons.append("early_saturation_investigate_scale")
    if len(trajectory) >= 2 and elapsed >= budget * 2 / 3:
        late = [entry for entry in trajectory if entry["elapsed_seconds"] >= budget / 3]
        if not late:
            reasons.append("no_late_submissions_investigate_agent_or_feedback")
        else:
            first_late_best = max(
                (entry["score"] for entry in trajectory if entry["elapsed_seconds"] < budget / 3),
                default=float(metadata["request"]["scoring"]["baseline"]),
            )
            if max(item["score"] for item in late) <= first_late_best:
                reasons.append("no_late_growth_investigate_feedback")
    finished = (task / "final_result.json").is_file()
    if finished:
        try:
            final = common.load_json(task / "final_result.json")
            if (not common.finite_number(final.get("runtime_seconds"))
                    or final["runtime_seconds"] < budget - 5 or final.get("timed_out") is not True):
                reasons.append("run_finished_before_full_budget")
        except common.PipelineError:
            reasons.append("invalid_final_result")
    return {
        "run_id": run_id,
        "task_sha256": task_sha256,
        "task_id": task_id,
        "elapsed_seconds": round(elapsed, 1),
        "budget_seconds": budget,
        "valid_submission_count": len(trajectory),
        "best_score": best,
        "trajectory": trajectory,
        "invalid_rounds": invalid_rounds,
        "finished": finished,
        "status": "review-needed" if reasons else "observing",
        "signals": reasons,
        "note": "Diagnostics require main-agent investigation; do not stop or rewrite a running trial.",
    }


def tick(
    workspace: str | Path, log_dir: str | Path, run_id: str,
    notify_agent: bool = False, opencode: str = "opencode",
) -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    report = observe(root, log_dir, run_id)
    if report["signals"]:
        outbox = root / "evidence/monitor-outbox"
        outbox.mkdir(exist_ok=True)
        fingerprint = common.canonical_sha256({
            "task_sha256": report["task_sha256"],
            "run_id": run_id,
            "signals": report["signals"],
            "last_report": report["trajectory"][-1]["report_sha256"] if report["trajectory"] else None,
        })
        notification = outbox / f"{fingerprint}.json"
        if not notification.exists() and not notification.is_symlink():
            common.write_new_json(notification, report)
        report["notification_path"] = str(notification)
        if notify_agent:
            dispatched = root / "evidence/monitor-dispatch"
            dispatched.mkdir(exist_ok=True)
            receipt_path = dispatched / notification.name
            if not receipt_path.exists() and not receipt_path.is_symlink():
                argv = [
                    opencode, "run", "--agent", "edgebench-main", "--format", "default",
                    "You are the main reviewer. Read this immutable SForge rollout "
                    "notification: " + str(notification)
                    + ". Ask @edgebench-reviewer for an independent read-only diagnosis "
                    "if needed. Return a concrete continue/investigate/revise recommendation "
                    "with evidence to the owner. Do not stop runs or modify tasks.",
                ]
                try:
                    result = subprocess.run(
                        argv, cwd=Path(__file__).resolve().parents[2],
                        env=os.environ.copy(), capture_output=True, timeout=600, check=False,
                    )
                    raw = result.stdout[:16384]
                    text = raw.decode("utf-8", errors="replace")
                    for secret_name in ("DMX_API_KEY", "BIGMODEL_API_KEY", "LITELLM_API_KEY", "OPENAI_API_KEY", "SFORGE_AGENT_API_KEY"):
                        secret = os.environ.get(secret_name)
                        if secret:
                            text = text.replace(secret, "[REDACTED]")
                    receipt = {
                        "notification_sha256": common.sha256_file(notification),
                        "returncode": result.returncode,
                        "response_sha256": hashlib.sha256(result.stdout).hexdigest(),
                        "recommendation": text if result.returncode == 0 else None,
                        "status": "reported" if result.returncode == 0 else "dispatch-failed",
                    }
                except (OSError, subprocess.TimeoutExpired):
                    receipt = {
                        "notification_sha256": common.sha256_file(notification),
                        "status": "dispatch-failed", "recommendation": None,
                    }
                common.write_new_json(receipt_path, receipt)
            report["dispatch_path"] = str(receipt_path)
    return report, 0


def monitor_batch(
    manifest_path: str | Path, max_workers: int = 3,
    notify_agent: bool = False, opencode: str = "opencode",
) -> tuple[dict, int]:
    if type(max_workers) is not int or max_workers < 1:
        raise common.PipelineError("monitor concurrency must be a positive integer")
    manifest = common.load_json(manifest_path)
    if (set(manifest) != {"schema_version", "jobs"}
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1):
        raise common.PipelineError("monitor manifest requires schema_version and jobs")
    jobs = manifest["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise common.PipelineError("monitor manifest needs jobs")
    seen = set()
    for job in jobs:
        if not isinstance(job, dict) or set(job) != {"workspace", "log_dir", "run_id"}:
            raise common.PipelineError("each monitor job needs workspace, log_dir, run_id")
        if not all(isinstance(job[key], str) and job[key].strip() for key in job):
            raise common.PipelineError("monitor job fields must be nonempty text")
        if (job["workspace"], job["run_id"]) in seen:
            raise common.PipelineError("monitor manifest duplicates a run")
        seen.add((job["workspace"], job["run_id"]))

    def run(job: dict) -> dict:
        try:
            value, _ = tick(job["workspace"], job["log_dir"], job["run_id"], notify_agent, opencode)
            return value
        except (common.PipelineError, OSError, ValueError, TypeError) as exc:
            return {"run_id": job["run_id"], "status": "observation-failed", "reason": str(exc)}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
        results = [future.result() for future in as_completed([pool.submit(run, job) for job in jobs])]
    results.sort(key=lambda item: item["run_id"])
    status = "partial" if any(item["status"] == "observation-failed" for item in results) else "complete"
    return {"status": status, "observations": results, "max_workers": max_workers}, 0 if status == "complete" else 2
