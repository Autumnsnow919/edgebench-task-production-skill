#!/usr/bin/env python3
"""Validate and aggregate full-budget SForge frontier-model rollouts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import statistics
import subprocess
import time
from urllib.parse import urlsplit

import topify_profile


RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{4,80}\Z")
PROVIDERS = {
    "dmx": ("https://www.dmxapi.cn/v1", "DMX_API_KEY"),
    "bigmodel": ("https://open.bigmodel.cn/api/paas/v4", "BIGMODEL_API_KEY"),
    "litellm": (os.environ.get("LITELLM_API_BASE_URL", "http://localhost:4000/v1"), "LITELLM_API_KEY"),
}
DMX_DOTENV = Path(__file__).resolve().parents[2] / ".env"


def _provider_key(provider: str) -> str | None:
    config = PROVIDERS.get(provider)
    if config is None:
        return None
    key = os.environ.get(config[1])
    if key or provider != "dmx" or not DMX_DOTENV.exists():
        return key or None
    if (DMX_DOTENV.is_symlink() or not DMX_DOTENV.is_file()
            or DMX_DOTENV.stat().st_mode & 0o077
            or DMX_DOTENV.stat().st_size > 16384):
        raise ValueError("local DMX .env must be a small, regular, owner-only file")
    matches = []
    for line in DMX_DOTENV.read_text(encoding="utf-8").splitlines():
        found = re.fullmatch(r"\s*(?:export\s+)?DMX_API_KEY\s*=\s*(.*?)\s*", line)
        if found:
            candidate = found.group(1)
            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
                candidate = candidate[1:-1]
            if not re.fullmatch(r"[A-Za-z0-9._~-]+", candidate):
                raise ValueError("invalid DMX credential assignment in local .env (value suppressed)")
            matches.append(candidate)
    if len(matches) > 1:
        raise ValueError("duplicate DMX credential assignment in local .env")
    return matches[0] if matches else None


def provider_identity(provider: str) -> str:
    """Private stable cohort identity without exporting a provider endpoint."""
    configured = PROVIDERS.get(provider)
    if configured is None:
        raise ValueError("unknown rollout provider")
    return hashlib.sha256(f"{provider}\n{configured[0]}".encode("utf-8")).hexdigest()


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_secret(path: Path, secret: bytes) -> bool:
    if not secret:
        return False
    overlap = max(0, len(secret) - 1)
    previous = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value = previous + chunk
            if secret in value:
                return True
            previous = value[-overlap:] if overlap else b""
    return False


def _secret_paths(root: Path, secret: str) -> list[str]:
    encoded = secret.encode("utf-8")
    matches = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if _contains_secret(path, encoded):
                matches.append(str(path.relative_to(root)))
        except OSError:
            matches.append(str(path.relative_to(root)) + ":unreadable")
    return matches


def summarize_run(run_directory: str | Path, task_id: str, expected: dict,
                  *, pre_rollout_reviewed_at: str | None = None) -> dict:
    run = Path(run_directory).resolve(strict=True)
    task = run / task_id
    config = _load(task / "run_config.json")
    result = _load(task / "final_result.json")
    history = _load(task / "run_history.json")
    started_lines = (task / "started_at").read_text(encoding="utf-8").splitlines()
    if len(started_lines) < 2:
        raise ValueError("SForge started_at is missing its epoch timestamp")
    started_at = float(started_lines[1])
    if not math.isfinite(started_at):
        raise ValueError("SForge start timestamp is not finite")
    review_before_start = True
    if pre_rollout_reviewed_at is not None:
        try:
            review_time = datetime.fromisoformat(pre_rollout_reviewed_at)
            review_before_start = (review_time.tzinfo is not None
                                   and review_time.astimezone(timezone.utc).timestamp() <= started_at)
        except (TypeError, ValueError, OverflowError):
            review_before_start = False
    config_match = all(config.get(key) == value for key, value in expected.items())
    config_match = config_match and config.get("task_id") == task_id and config.get("internet") is False
    credential_redacted = config.get("api_key") in {None, "<redacted>"}
    result_match = all(result.get(key) == expected.get(key) for key in ("agent", "model", "effort"))
    result_match = result_match and result.get("task") == task_id and result.get("run_id") == run.name
    budget = expected["timeout"]
    runtime = result.get("runtime_seconds")
    full_budget = (
        isinstance(runtime, (int, float))
        and not isinstance(runtime, bool)
        and math.isfinite(runtime)
        and runtime >= budget - 5
        and result.get("timed_out") is True
    )
    trajectory = []
    submissions = task / "submissions"
    for report_path in sorted(submissions.glob("*/report.json")):
        report = _load(report_path)
        score = report.get("score_0_100")
        submitted_at = report.get("submitted_at")
        archive = report_path.parent / "submission.tar.gz"
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
            or not 0 <= score <= 100
            or type(report.get("valid")) is not bool
            or not isinstance(submitted_at, (int, float))
            or isinstance(submitted_at, bool)
            or not math.isfinite(submitted_at)
            or not archive.is_file()
            or archive.is_symlink()
        ):
            raise ValueError(f"invalid completed submission evidence: {report_path}")
        elapsed = max(0.0, float(submitted_at) - started_at)
        trajectory.append({
            "round": report_path.parent.name,
            "submission_id": report.get("submission_id"),
            "elapsed_seconds": elapsed,
            "score": float(score),
            "valid": report["valid"],
            "report_sha256": _sha256(report_path),
            "archive_sha256": _sha256(archive),
        })
    trajectory.sort(key=lambda item: (item["elapsed_seconds"], item["round"]))
    best = None
    for item in trajectory:
        best = item["score"] if best is None else max(best, item["score"])
        item["best_so_far"] = best
    declared_best = result.get("best_score")
    score_match = (
        best is not None
        and isinstance(declared_best, (int, float))
        and not isinstance(declared_best, bool)
        and math.isfinite(declared_best)
        and math.isclose(best, float(declared_best), rel_tol=0, abs_tol=1e-9)
    )
    history_entries = history.get("entries")
    scored_by_round = {item["round"]: item for item in trajectory}
    history_ok = isinstance(history_entries, list) and len(scored_by_round) == len(trajectory)
    unscored_attempts = []
    seen_rounds = set()
    if history_ok:
        for entry in history_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("round"), str):
                history_ok = False
                break
            round_id = entry["round"]
            if round_id in seen_rounds or entry.get("type") != "submission":
                history_ok = False
                break
            seen_rounds.add(round_id)
            scored = scored_by_round.get(round_id)
            if scored is not None:
                value = entry.get("score")
                if (entry.get("status") != "completed"
                        or not isinstance(value, (int, float)) or isinstance(value, bool)
                        or not math.isfinite(value)
                        or not math.isclose(float(value), scored["score"], abs_tol=1e-9, rel_tol=0)
                        or entry.get("submission_id") != scored["submission_id"]
                        or entry.get("valid") is not scored["valid"]):
                    history_ok = False
                    break
                continue
            # A history round with no scored report is never a zero score.
            # Admit only a known Docker API timeout backed by its own raw log.
            log = submissions / round_id / "run_instance.log"
            if (entry.get("status") != "completed" or entry.get("score") is not None
                    or log.is_symlink() or not log.is_file() or log.stat().st_size > 512_000):
                history_ok = False
                break
            raw = log.read_bytes()
            if not (b"UnixHTTPConnectionPool" in raw and b"Read timed out" in raw):
                history_ok = False
                break
            unscored_attempts.append({
                "round": round_id, "submission_id": entry.get("submission_id"),
                "error_class": "docker_api_timeout", "log_sha256": _sha256(log),
                "log_path": f"submissions/{round_id}/run_instance.log",
            })
        history_ok = history_ok and len(seen_rounds) == len(scored_by_round) + len(unscored_attempts)
    checkpoints = []
    for seconds in (budget / 3, 2 * budget / 3, float(budget)):
        available = [item["score"] for item in trajectory if item["elapsed_seconds"] <= seconds]
        checkpoints.append({
            "elapsed_seconds": seconds,
            "best_score": max(available) if available else None,
        })
    complete = all((config_match, credential_redacted, result_match, full_budget,
                    score_match, history_ok, review_before_start))
    reason = []
    if not config_match:
        reason.append("model/agent/effort/budget/task config mismatch")
    if not credential_redacted:
        reason.append("credential was not redacted")
    if not result_match:
        reason.append("final result identity mismatch")
    if not full_budget:
        reason.append("run did not consume the fixed full budget")
    if not score_match:
        reason.append("declared best score does not match retained submissions")
    if not history_ok:
        reason.append("run history does not match scored reports and evidenced unscored events")
    if not review_before_start:
        reason.append("pre-rollout Q1–Q7 review was absent or signed after this run started")
    return {
        "run_id": run.name,
        "status": "complete" if complete else "incomplete",
        "reason": "complete full-budget run" if complete else "; ".join(reason),
        "agent": result.get("agent"),
        "model": result.get("model"),
        "effort": result.get("effort"),
        "budget_seconds": budget,
        "pre_rollout_review_before_start": review_before_start,
        "runtime_seconds": runtime,
        "best_score": float(declared_best) if score_match else None,
        "agent_submissions": result.get("agent_submissions"),
        "auto_submissions": result.get("auto_submissions"),
        "total_rounds": result.get("total_rounds"),
        "trajectory": trajectory,
        "unscored_attempts": unscored_attempts,
        "checkpoints": checkpoints,
    }


def aggregate_runs(runs: list[dict], required_run_count: int = 3) -> dict:
    complete = [run for run in runs if run.get("status") == "complete"]
    scores = [float(run["best_score"]) for run in complete]
    enough = len(complete) >= required_run_count
    return {
        "status": "complete" if enough else "incomplete",
        "completed_run_count": len(complete),
        "required_run_count": required_run_count,
        "best_scores": scores,
        "mean_best_score": statistics.fmean(scores) if scores else None,
        "runs": runs,
        "reason": (
            f"At least {required_run_count} fixed-config full-budget runs were measured."
            if enough
            else f"At least {required_run_count} fixed-config full-budget runs are required."
        ),
    }


def preflight(workspace: str | Path) -> tuple[dict, int]:
    import topify_candidate_audit
    import topify_common as common
    import topify_delivery_contract
    import topify_profile
    import topify_review
    import topify_validate

    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    checks = []

    def check(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"id": check_id, "status": "complete" if passed else "incomplete", "detail": detail})

    selected_format = topify_delivery_contract.current(root, task_sha256)
    selected_profile = topify_profile.current(root, task_sha256)
    # Legacy miniature fixtures remain usable for unit-level plumbing tests.
    # Every real production task must choose the final customer format before
    # any frontier trajectory is started.
    format_required = metadata["request"].get("plugin") != "mini_continuous"
    check(
        "rollout.delivery_format_selected",
        bool(selected_format) or not format_required,
        "Select sforge-only or harbor and bind it to the frozen task before rollout.",
    )
    check(
        "rollout.benchmark_profile_selected",
        bool(selected_profile) or not format_required,
        "A benchmark profile must bind the user requirements and thresholds before rollout.",
    )
    pre_qc = common.load_json(root / "evidence/pre-rollout-qc/latest.json") if (
        root / "evidence/pre-rollout-qc/latest.json"
    ).is_file() else {}
    pre_qc_ok = (
        not format_required
        or pre_qc.get("status") == "ready"
        and pre_qc.get("task_sha256") == task_sha256
        and isinstance(pre_qc.get("report_path"), str)
        and Path(pre_qc["report_path"]).is_file()
        and common.sha256_file(pre_qc["report_path"]) == pre_qc.get("report_sha256")
    )
    check(
        "rollout.pre_rollout_qc",
        pre_qc_ok,
        "The task-bound pre-rollout process QC receipt must be ready before model hours.",
    )
    freeze = common.load_json(root / "evidence/freeze/latest.json") if (
        root / "evidence/freeze/latest.json"
    ).is_file() else {}
    freeze_ok = (
        not format_required
        or freeze.get("status") == "FROZEN"
        and freeze.get("subject", {}).get("task_sha256") == task_sha256
        and freeze.get("subject", {}).get("profile_id") == selected_profile.get("profile_id")
        and freeze.get("subject", {}).get("delivery_format") == selected_format.get("format")
    )
    check(
        "rollout.freeze_subject",
        freeze_ok,
        "A current FROZEN subject receipt must bind task, profile and delivery format before rollout.",
    )

    adapter_path = root / "evidence/sforge-adapters/latest.json"
    adapter = common.load_json(adapter_path) if adapter_path.is_file() else {}
    adapter_ok = adapter.get("status") == "complete" and adapter.get("task_sha256") == task_sha256
    check("rollout.adapter_parity", adapter_ok, "Current SForge adapter must pass NOP, starter, interior, and strong parity.")
    if metadata["request"]["plugin"] != "mini_continuous":
        candidate = topify_candidate_audit.current_diagnostic(root, task_sha256)
        check("rollout.generic_control_screen", candidate.get("status") == "review-needed",
              "Receipt-backed generic baselines must be reviewed; cheap near-reference saturation requires a new task identity.")
    check(
        "rollout.main_agent_rubric",
        topify_review.review_is_current(root, task_sha256),
        "Main-agent Q1–Q7 quality and difficulty review must be current and rollout-ready.",
    )
    architecture_evidence = {}
    for architecture in sorted(common.ARCHITECTURES):
        path = root / "evidence/image-measurements" / architecture.replace("/", "-") / "latest.json"
        value = common.load_json(path) if path.is_file() else {}
        architecture_evidence[architecture] = value
        passed = (
            value.get("status") == "complete"
            and value.get("task_sha256") == task_sha256
            and value.get("requested_architecture") == architecture
            and value.get("observed_host_architecture") == architecture
        )
        check(
            f"rollout.native_{architecture.rsplit('/', 1)[-1]}",
            passed,
            f"A native final-image measurement is required on {architecture}.",
        )
    architecture_scores = [
        architecture_evidence[architecture].get("scores")
        for architecture in sorted(common.ARCHITECTURES)
    ]
    check(
        "rollout.cross_arch_score_parity",
        all(architecture_scores) and architecture_scores[0] == architecture_scores[1],
        "The same fixed score ladder must agree across native amd64 and arm64 measurements.",
    )
    rollout = metadata["request"]["rollout"]
    provider = rollout["provider"].lower()
    provider_config = PROVIDERS.get(provider)
    provider_ok = provider_config is not None
    check("rollout.provider", provider_ok, "Provider must have an explicit endpoint and credential variable mapping.")
    credential_name = provider_config[1] if provider_config else None
    credential_ok = bool(credential_name and _provider_key(provider))
    check("rollout.credential", credential_ok, "Provider credential must be present only in the owner process environment.")
    complete = all(item["status"] == "complete" for item in checks)
    report = {
        "schema_version": 1,
        "status": "complete" if complete else "incomplete",
        "state": "ROLLOUT_READY" if complete else "SFORGE_READY" if adapter_ok else "BUILT",
        "task_sha256": task_sha256,
        "checks": checks,
        "fixed_config": {
            **rollout,
            "timeout_seconds": metadata["request"]["interaction_budget_seconds"],
            "api_base_url": provider_config[0] if provider_config else None,
            "credential_variable": credential_name,
            "credential_in_argv": False,
        },
        "adapter_task_id": adapter.get("adapter", {}).get("task_id") if adapter_ok else None,
        "tasks_dir": adapter.get("adapter", {}).get("tasks_dir") if adapter_ok else None,
        "delivery_format": selected_format.get("format") if selected_format else None,
        "benchmark_profile": selected_profile.get("profile_id") if selected_profile else None,
    }
    return report, 0 if complete else 2


def start(
    workspace: str | Path,
    run_id: str,
    log_dir: str | Path,
    judge_url: str,
    sforge_executable: str = "sforge",
) -> tuple[dict, int]:
    import topify_common as common

    if not RUN_ID.fullmatch(run_id):
        raise common.PipelineError("run_id must be a lowercase hyphenated identifier")
    parsed_judge = urlsplit(judge_url)
    if (
        parsed_judge.scheme not in {"http", "https"}
        or not parsed_judge.netloc
        or parsed_judge.username
        or parsed_judge.password
        or parsed_judge.query
        or parsed_judge.fragment
    ):
        raise common.PipelineError("judge_url must be a credential-free HTTP(S) endpoint")
    readiness, code = preflight(workspace)
    if code != 0:
        raise common.PipelineError("rollout preflight is incomplete")
    root, metadata = common.load_workspace(workspace)
    logs = Path(log_dir).absolute()
    if not logs.is_dir() or logs.is_symlink() or logs.resolve(strict=True) != logs:
        raise common.PipelineError("SForge log directory must be an existing canonical real directory")
    if (logs / "runs" / run_id).exists() or (logs / "runs" / run_id).is_symlink():
        raise common.PipelineError("run_id already exists; refusing to overwrite rollout evidence")
    tasks_dir = Path(readiness["tasks_dir"])
    if not tasks_dir.is_dir() or tasks_dir.is_symlink():
        raise common.PipelineError("prepared SForge tasks directory is unavailable")
    config = readiness["fixed_config"]
    key = _provider_key(config["provider"])
    if not key:
        raise common.PipelineError("provider credential is unavailable (value suppressed)")
    contaminated_assets = _secret_paths(tasks_dir, key)
    if contaminated_assets:
        raise common.PipelineError("provider credential appears in prepared SForge assets (value suppressed)")
    resources = metadata["request"]["resources"]
    argv = [
        sforge_executable,
        "--tasks-dir",
        str(tasks_dir),
        "--log-dir",
        str(logs),
        "run",
        "--task",
        readiness["adapter_task_id"],
        "--agent",
        config["agent"],
        "--model",
        config["model"],
        "--effort",
        config["effort"],
        "--timeout",
        str(config["timeout_seconds"]),
        "--eval-interval",
        "300",
        "--judge-url",
        judge_url,
        "--disable-internet",
        "--work-cpu-limit",
        str(resources["agent_cpu"]),
        "--work-mem-limit",
        f"{resources['agent_memory_mb']}m",
        "--judge-cpu-limit",
        str(resources["verifier_cpu"]),
        "--judge-mem-limit",
        f"{resources['verifier_memory_mb']}m",
        "--run-id",
        run_id,
    ]
    launch = root / "evidence/rollout-launches" / run_id
    launch.mkdir(parents=True)
    environment = common.sanitized_env()
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "LITELLM_API_KEY", "BIGMODEL_API_KEY", "DMX_API_KEY",
    ):
        environment.pop(name, None)
    environment.update({
        "SFORGE_AGENT_API_KEY": key,
        "SFORGE_AGENT_API_BASE_URL": config["api_base_url"],
        "SFORGE_AGENT_MODEL": config["model"],
        "SFORGE_AGENT_EFFORT": config["effort"],
    })
    started_at = common.utc_now()
    started = time.monotonic()
    with (launch / "stdout.log").open("xb") as stdout, (launch / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(
            argv,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
        )
        returncode = process.wait()
    leaked_paths = _secret_paths(launch, key)
    run_path = logs / "runs" / run_id
    if run_path.is_dir() and not run_path.is_symlink():
        leaked_paths.extend(f"sforge-run/{path}" for path in _secret_paths(run_path, key))
    observation = {
        "schema_version": 1,
        "run_id": run_id,
        "task_sha256": readiness["task_sha256"],
        "argv": argv,
        "pid": process.pid,
        "started_at": started_at,
        "finished_at": common.utc_now(),
        "elapsed_seconds": time.monotonic() - started,
        "returncode": returncode,
        "stdout_sha256": common.sha256_file(launch / "stdout.log"),
        "stderr_sha256": common.sha256_file(launch / "stderr.log"),
        "credential_variable": config["credential_variable"],
        "provider_identity_sha256": provider_identity(config["provider"]),
        "credential_in_argv": False,
        "status": (
            "credential-leak-detected"
            if leaked_paths
            else "process-complete" if returncode == 0 else "process-failed"
        ),
        "credential_leak_paths": leaked_paths,
        "note": "Use rollout-collect to determine whether this was a complete full-budget run.",
    }
    common.write_new_json(launch / "process.json", observation)
    common.append_event(root, "rollout_process_finished", run_id=run_id, returncode=returncode)
    return observation, 0 if returncode == 0 and not leaked_paths else 2


def _current_review_time(root: Path, task_sha256: str) -> str:
    """Only a currently ready main-agent Q1–Q7 review can predate a run."""
    import topify_common as common
    import topify_review

    if not topify_review.review_is_current(root, task_sha256):
        return ""
    path = root / "evidence/author-reviews/latest.json"
    review = common.load_json(path)
    value = review.get("reviewed_at")
    return value if isinstance(value, str) else ""


def collect(workspace: str | Path, log_dir: str | Path) -> tuple[dict, int]:
    import topify_common as common
    import topify_validate

    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    adapter = common.load_json(root / "evidence/sforge-adapters/latest.json")
    if adapter.get("status") != "complete" or adapter.get("task_sha256") != task_sha256:
        raise common.PipelineError("current complete SForge adapter evidence is required")
    task_id = adapter["adapter"]["task_id"]
    reviewed_at = _current_review_time(root, task_sha256)
    request = metadata["request"]
    expected = {
        "agent": request["rollout"]["agent"],
        "model": request["rollout"]["model"],
        "effort": request["rollout"]["effort"],
        "timeout": request["interaction_budget_seconds"],
    }
    logs = Path(log_dir).resolve(strict=True)
    runs_root = logs / "runs"
    if not runs_root.is_dir() or runs_root.is_symlink():
        raise common.PipelineError("SForge log directory has no canonical runs directory")
    runs = []
    launch_hashes = {}
    identity = provider_identity(request["rollout"]["provider"])
    for run_path in sorted(runs_root.iterdir()):
        if not run_path.is_dir() or run_path.is_symlink() or not (run_path / task_id).is_dir():
            continue
        try:
            summary = summarize_run(run_path, task_id, expected,
                                    pre_rollout_reviewed_at=reviewed_at)
            if summary.get("status") == "complete":
                launch_path = root / "evidence/rollout-launches" / run_path.name / "process.json"
                launch = common.load_json(launch_path)
                if (launch.get("run_id") != run_path.name
                        or launch.get("task_sha256") != task_sha256
                        or launch.get("provider_identity_sha256") != identity
                        or launch.get("returncode") != 0
                        or launch.get("status") != "process-complete"):
                    summary = {"run_id": run_path.name, "status": "incomplete",
                               "reason": "provider identity is not bound to a successful launch"}
                else:
                    launch_hashes[run_path.name] = common.sha256_file(launch_path)
            runs.append(summary)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            runs.append({"run_id": run_path.name, "status": "failed", "reason": str(exc)})
    profile_receipt = topify_profile.current(root, task_sha256)
    required_runs = 3
    if profile_receipt:
        profile = topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
        required_runs = topify_profile.threshold(profile, "frontier_trials_minimum", 3)
    aggregate = aggregate_runs(runs, required_runs)
    attempt_number, attempt = common.next_attempt(root / "evidence/rollout-collections")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "task_sha256": task_sha256,
        "adapter_task_id": task_id,
        "fixed_config": expected,
        "provider_identity_sha256": identity,
        "run_launches": launch_hashes,
        "pre_rollout_reviewed_at": reviewed_at,
        "log_dir": str(logs),
        "collected_at": common.utc_now(),
        **aggregate,
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(
        root,
        "rollouts_collected",
        attempt=attempt_number,
        completed_run_count=report["completed_run_count"],
    )
    return report, 0 if report["status"] == "complete" else 2
