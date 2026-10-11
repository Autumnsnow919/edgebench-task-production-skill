#!/usr/bin/env python3
"""Package reviewed, measured SForge tasks into an isolated, reproducible batch ZIP.

Input: {"schema_version": 1, "batch_id": "lowercase-slug", "workspaces": ["/absolute/...", ...]}.
Each tasks/<namespace>/ is an independent SForge --tasks-dir; there is no shared
BENCHMARK.yaml because different tasks may use different pinned base images.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import zipfile

import topify_common as common
import topify_build
import topify_review
import topify_rollout
import topify_quality_report
import topify_sforge
import topify_sforge_acceptance
import topify_owner_arm
import topify_completion
import topify_delivery_contract
import topify_profile
import topify_validate


SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{4,80}\Z")
ADAPTER_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
ATTEMPT = re.compile(r"attempt-[0-9]{4,}\Z")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BLOCKED_NAMES = {".env", ".git", ".docker", "id_rsa", "id_ed25519", "credentials", "secrets"}
BLOCKED_SUFFIXES = (".log", ".pem", ".key", ".p12", ".zip", ".tar", ".tar.gz", ".oci", ".sif", ".img")
SENSITIVE_NAME = re.compile(r"(?:^|[-_.])(secret|secrets|credential|credentials|api_key|access_token)(?:[-_.]|$)")
OWNER_SECRET = re.compile(
    rb"(?i)(?:sk-[a-z0-9_-]{24,}|gh[pousr]_[a-z0-9]{30,}|"
    rb"github_pat_[a-z0-9_]{22,}|xox[aboprs]-[a-z0-9-]{20,}|"
    rb"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    rb"-----BEGIN\s+(?:OPENSSH|RSA|EC|DSA|ENCRYPTED)\s+PRIVATE KEY-----|"
    rb"bearer\s+[a-z0-9._-]{24,}|"
    rb"(?:api[_-]?key|authorization)\s*[:=]\s*['\"]?[a-z0-9._-]{25,})"
)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise common.PipelineError(reason)


def _directory(path: Path) -> None:
    _require(stat.S_ISDIR(path.lstat().st_mode), f"not a real directory: {path}")


def _file(path: Path) -> None:
    _require(stat.S_ISREG(path.lstat().st_mode), f"not a regular file: {path}")


def _owner_secret_check(source: Path | bytes) -> bool:
    if isinstance(source, bytes):
        if OWNER_SECRET.search(source):
            return False
        if source.startswith(b"\x1f\x8b"):
            try:
                with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
                    for member in archive.getmembers():
                        if not member.isfile() or member.size > 64 * 1024 * 1024:
                            return False
                        content = archive.extractfile(member)
                        if content is None or OWNER_SECRET.search(content.read()):
                            return False
            except (tarfile.TarError, OSError, ValueError):
                return False
        return True
    _file(source)
    if source.name.endswith(".tar.gz"):
        return _owner_secret_check(source.read_bytes())
    with source.open("rb") as handle:
        tail = b""
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if OWNER_SECRET.search(tail + chunk):
                return False
            tail = chunk[-256:]
    return True


def _children(directory: Path) -> list[Path]:
    """Never follow a link or silently drop an unsupported entry in a context."""
    _directory(directory)
    files = []
    for child in sorted(directory.iterdir()):
        lower = child.name.lower()
        _require(not lower.startswith(".") and lower not in BLOCKED_NAMES
                 and not lower.endswith(BLOCKED_SUFFIXES)
                 and SENSITIVE_NAME.search(lower) is None
                 and not any(character in child.name for character in ("\\", "\n", "\r")),
                 f"sensitive or binary context entry: {child}")
        mode = child.lstat().st_mode
        if stat.S_ISDIR(mode):
            files.extend(_children(child))
        elif stat.S_ISREG(mode):
            files.append(child)
        else:
            raise common.PipelineError(f"non-regular context entry: {child}")
    return files


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _number(value: object, low: float = 0, high: float = float("inf")) -> bool:
    return (type(value) in (int, float) and math.isfinite(value) and low <= value <= high)


def _format_seconds(value: object) -> str:
    if not _number(value):
        return "not recorded"
    seconds = int(float(value) + 0.5)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


def _selected_snapshot_rounds(run: dict) -> set[str]:
    """Keep the earliest scored best and fixed-checkpoint archives, not duplicates."""
    points = sorted(run["trajectory"], key=lambda item: (item["elapsed_seconds"], item["round"]))
    if not points:
        return set()
    chosen = {next(item["round"] for item in points if
                   math.isclose(item["score"], run["best_score"], abs_tol=1e-9, rel_tol=0))}
    for checkpoint in run.get("checkpoints", []):
        score = checkpoint.get("best_score")
        seconds = checkpoint.get("elapsed_seconds")
        if not _number(score, 0, 100) or not _number(seconds):
            continue
        candidate = next((item for item in points if item["elapsed_seconds"] <= seconds
                          and math.isclose(item["score"], score, abs_tol=1e-9, rel_tol=0)), None)
        if candidate is not None:
            chosen.add(candidate["round"])
    return chosen


def _check_scored_archive(path: Path, submit_paths: object) -> None:
    """Reject unrelated bytes in a customer snapshot, even if SForge ignored them."""
    _file(path)
    _require(isinstance(submit_paths, list) and all(isinstance(x, str) for x in submit_paths),
             "scored archive scope has no declared submit paths")
    allowed = set(submit_paths)
    names = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                raw = member.name
                parsed = PurePosixPath(raw)
                _require(not parsed.is_absolute() and ".." not in parsed.parts
                         and "\\" not in raw and "\n" not in raw and "\r" not in raw,
                         "scored archive scope contains an unsafe path")
                name = parsed.as_posix()
                if member.isdir():
                    _require(name == "." or any(item.startswith(name + "/") for item in allowed),
                             "scored archive scope contains an unrelated directory")
                else:
                    _require(member.isfile() and name in allowed and name not in names,
                             "scored archive scope contains an unrelated or linked file")
                    names.add(name)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise common.PipelineError("scored archive scope is unreadable") from exc


def _trial_rows(rollout: dict, metadata: dict, task_hash: str, adapter_id: str,
                 *, review_sample: bool = False, root: Path | None = None) -> list[dict]:
    config = rollout.get("fixed_config")
    request = metadata["request"]
    expected = request["rollout"]
    budget = request["interaction_budget_seconds"]
    profile_receipt = topify_profile.current(root, task_hash) if root is not None else {}
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    required_trials = topify_profile.threshold(profile, "frontier_trials_minimum", 3)
    _require(isinstance(config, dict) and config == {
        "agent": expected["agent"], "model": expected["model"],
        "effort": expected["effort"], "timeout": budget,
    }, "rollout config does not match the workspace request")
    _require(rollout.get("status") in ({"complete", "incomplete"} if review_sample else {"complete"})
              and rollout.get("task_sha256") == task_hash
              and rollout.get("adapter_task_id") == adapter_id, "rollout collection is not current and complete")
    runs = rollout.get("runs")
    _require(isinstance(runs, list), "rollout summaries are required")
    complete = [run for run in runs if isinstance(run, dict) and run.get("status") == "complete"]
    _require(review_sample or len(complete) >= required_trials,
             f"{required_trials} real full-budget rollout summaries are required by the selected profile")
    if root is not None and complete:
        identity = topify_rollout.provider_identity(expected["provider"])
        launches = rollout.get("run_launches")
        _require(rollout.get("provider_identity_sha256") == identity
                 and isinstance(launches, dict)
                 and set(launches) == {run["run_id"] for run in complete},
                 "rollout provider identity or launch cohort is unverified")
        for run in complete:
            launch_path = root / "evidence/rollout-launches" / run["run_id"] / "process.json"
            _file(launch_path)
            launch = common.load_json(launch_path)
            _require(launches[run["run_id"]] == common.sha256_file(launch_path)
                     and launch.get("run_id") == run["run_id"]
                     and launch.get("task_sha256") == task_hash
                     and launch.get("status") == "process-complete"
                     and launch.get("returncode") == 0
                     and launch.get("provider_identity_sha256") == identity,
                     "rollout provider differs from the fixed model cohort")
    _require(not review_sample or rollout["status"] == ("complete" if len(complete) >= required_trials else "incomplete"),
             "partial rollout status does not match completed trial count")
    _require(rollout.get("completed_run_count") == len(complete), "rollout count mismatch")
    ids = set()
    rows = []
    for run in complete:
        run_id = run.get("run_id")
        _require(isinstance(run_id, str) and RUN_ID.fullmatch(run_id) is not None and run_id not in ids,
                 "missing, unsafe or duplicate rollout ID")
        ids.add(run_id)
        _require(all(run.get(key) == config[key] for key in ("agent", "model", "effort"))
                 and run.get("budget_seconds") == budget, "rollout identity/budget mismatch")
        runtime = run.get("runtime_seconds")
        score = run.get("best_score")
        _require(_number(runtime) and runtime >= budget - 5 and _number(score, 0, 100),
                 "rollout lacks measured full-budget runtime or valid score")
        step = run.get("agent_steps")
        _require(step is None or (type(step) is int and step >= 0), "invalid reported agent_steps")
        rows.append({
            "trial_id": run_id, "model": config["model"], "agent": config["agent"],
            "effort": config["effort"], "score": score,
            "run_wall_seconds": runtime, "step": step if step is not None else "not recorded",
            "agent_submissions": run.get("agent_submissions") if type(run.get("agent_submissions")) is int else "not recorded",
        })
    rows.sort(key=lambda row: row["trial_id"])
    _require(rollout.get("best_scores") == [run["best_score"] for run in complete],
             "rollout aggregate scores do not match recorded runs")
    mean = rollout.get("mean_best_score")
    _require((mean is None if not rows else _number(mean, 0, 100)
              and math.isclose(mean, math.fsum(row["score"] for row in rows) / len(rows),
                               rel_tol=0, abs_tol=1e-9)), "rollout mean is inconsistent")
    return rows


def _csv_bytes(rows: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    fields = (list(rows[0]) if rows else
              ["trial_id", "model", "agent", "effort", "score", "run_wall_seconds", "step", "agent_submissions"])
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _task_bundle(root: Path, metadata: dict,
                 customer_language: str = "en-zh", *,
                 review_sample: bool = False, audit: dict | None = None) -> tuple[str, dict[str, Path | bytes], dict]:
    _require(metadata["request"].get("plugin") != "mini_continuous",
             "miniature diagnostic plugin cannot be delivered as a production task")
    task = root / "task"
    _directory(task)
    design = common.load_json(root / "task_design.json")
    _require(design.get("request_sha256") == metadata.get("request_sha256")
             and all(isinstance(design.get(key), str) and design[key].strip() for key in (
                 "real_work", "real_world_consequence", "long_horizon_reason",
             )), "task has no current, grounded business design")
    _require(all(record["type"] in {"directory", "file"} for record in topify_validate.task_records(task)),
             "Harbor task contains unsafe entries")
    task_hash = topify_validate.task_sha256(task)
    customer_copy = common.load_json(root / "evidence/customer-copy.json")
    copy_keys = {"schema_version", "task_sha256", "reviewer_role", "english"}
    if customer_language == "en-zh":
        copy_keys.add("chinese")
    _require((set(customer_copy) == copy_keys or
              customer_language == "en" and set(customer_copy) == copy_keys | {"chinese"})
             and customer_copy.get("schema_version") == 1
             and customer_copy.get("task_sha256") == task_hash
             and customer_copy.get("reviewer_role") == "main-agent",
             "reviewed customer copy for current task and delivery_language is required")
    for language in (("english", "chinese") if customer_language == "en-zh" else ("english",)):
        block = customer_copy.get(language)
        _require(isinstance(block, dict) and set(block) == {"scenario", "consequence", "why_long"}
                 and all(isinstance(value, str) and value.strip() for value in block.values()),
                 f"customer copy needs a complete {language} block")
    for key, design_key in (
        ("scenario", "real_work"), ("consequence", "real_world_consequence"),
        ("why_long", "long_horizon_reason"),
    ):
        _require(customer_copy["english"][key] == design[design_key],
                 f"English {key} customer copy must match the model-grounded task design")
        if customer_language == "en-zh":
            _require(any("\u4e00" <= character <= "\u9fff" for character in customer_copy["chinese"][key]),
                     f"Chinese {key} customer copy requires a genuine translation")
    _directory(root / "evidence")
    _directory(root / "evidence/sforge-adapters")
    _directory(root / "evidence/author-reviews")
    adapter = common.load_json(root / "evidence/sforge-adapters/latest.json")
    review = common.load_json(root / "evidence/author-reviews/latest.json")
    _require(adapter.get("status") == "complete" and adapter.get("task_sha256") == task_hash,
             "current complete SForge adapter is required")
    if review_sample:
        _require(review.get("status") in {"ready", "reject", "revise"}
                 and review.get("task_sha256") == task_hash
                 and review.get("reviewer_role") == "main-agent"
                 and isinstance(review.get("quality"), dict)
                 and set(topify_review.QUALITY_IDS).issubset(review["quality"]),
                 "incomplete sample needs a task-bound independent Q1–Q7 review")
    else:
        _require(review.get("status") == "ready" and review.get("task_sha256") == task_hash,
                 "current owner-provided ready rubric review is required")
        _require(topify_review.review_is_current(root, task_hash),
                 "main-agent Q1–Q7 review evidence is missing or stale")
    details = adapter.get("adapter")
    _require(isinstance(details, dict), "SForge adapter metadata missing")
    adapter_id = details.get("task_id")
    _require(isinstance(adapter_id, str) and ADAPTER_ID.fullmatch(adapter_id) is not None,
             "unsafe SForge adapter task ID")
    tasks_dir_name = details.get("tasks_dir")
    _require(isinstance(tasks_dir_name, str) and Path(tasks_dir_name).is_absolute(),
             "missing absolute SForge tasks directory")
    tasks_dir = Path(tasks_dir_name)
    expected_parent = root / "evidence/sforge-adapters"
    relative = tasks_dir.relative_to(expected_parent)
    _require(len(relative.parts) == 2 and ATTEMPT.fullmatch(relative.parts[0]) is not None
             and relative.parts[1] == "tasks" and str(tasks_dir) == str(expected_parent / relative),
             "SForge tasks directory must be an immutable workspace adapter attempt")
    _directory(root / "evidence")
    _directory(expected_parent)
    _directory(expected_parent / relative.parts[0])
    _directory(tasks_dir)
    definition_path = tasks_dir / f"{adapter_id}.json"
    benchmark_path = tasks_dir / "BENCHMARK.yaml"
    _file(definition_path)
    _file(benchmark_path)
    _require(details.get("task_definition_sha256") == common.sha256_file(definition_path)
              and details.get("benchmark_sha256") == common.sha256_file(benchmark_path),
              "SForge definition or benchmark changed after adapter preparation")
    hashes = details.get("contexts_sha256")
    _require(isinstance(hashes, dict) and set(hashes) == {"work", "judge"}
             and all(hashes[role] == topify_validate.context_sha256(tasks_dir / "contexts" / role)
                     for role in ("work", "judge")),
             "SForge Work/Judge context changed after scored adapter preparation")
    definition = common.load_json(definition_path)
    definition_metadata = definition.get("metadata")
    _require(isinstance(definition_metadata, dict) and definition.get("task_id") == adapter_id
              and definition_metadata.get("authoring_task_id") == metadata["task_id"]
              and definition_metadata.get("authoring_task_sha256") == task_hash,
              "SForge task definition does not match the current authoring task bytes")
    _require(definition.get("internet") is False and definition_metadata.get("adapter_role") == "sforge-task",
              "SForge task is not the expected offline delivery definition")
    expected_scoring = {
        "reward_type": "bounded_continuous", "score_direction": "maximize",
        **metadata["request"]["scoring"],
    }
    _require(
        definition.get("category") == metadata["request"]["category"]
        and definition_metadata.get("capability_category") == metadata["request"]["category"]
        and definition_metadata.get("scoring") == expected_scoring
        and definition_metadata.get("resources") == metadata["request"]["resources"]
        and definition_metadata.get("reference_mode") == metadata["request"]["reference_mode"]
        and definition_metadata.get("open_solution") is (
            metadata["request"]["reference_mode"] == "open-solution"
        ),
        "SForge task metadata does not carry the frozen category, reference mode, or scoring formula",
    )
    _require(isinstance(definition.get("work"), dict) and isinstance(definition.get("judge"), dict)
             and definition["work"].get("specs_dir") == "/workspace"
             and definition["judge"].get("parser") == "structured_json",
              "SForge adapter context or judge definition is inconsistent")
    _, work_image, judge_image = topify_sforge._image_names(definition)
    _directory(root / "evidence/rollout-collections")
    rollout = common.load_json(root / "evidence/rollout-collections/latest.json")
    rows = _trial_rows(rollout, metadata, task_hash, adapter_id,
                       review_sample=review_sample, root=root)
    pre_review_time = None
    if review_sample and rows:
        pre_review_time = rollout.get("pre_rollout_reviewed_at")
        _require(isinstance(pre_review_time, str) and bool(pre_review_time),
                 "review sample runs need original pre-rollout review time")
        earlier_reviews = (root / "evidence/author-reviews").glob("attempt-*/summary.json")
        _require(any(
            (earlier := common.load_json(path)).get("status") == "ready"
            and earlier.get("reviewed_at") == pre_review_time
            and earlier.get("task_sha256") == task_hash
            and earlier.get("reviewer_role") == "main-agent"
            for path in earlier_reviews),
            "original pre-rollout independent review receipt is missing")
    log_dir_value = rollout.get("log_dir")
    _require(isinstance(log_dir_value, str) and Path(log_dir_value).is_absolute(),
             "SForge raw log directory is required for delivery audit")
    log_dir = Path(log_dir_value)
    _directory(log_dir)
    _directory(log_dir / "runs")
    for stored in rollout["runs"]:
        if stored.get("status") != "complete":
            continue
        run_id = stored["run_id"]
        try:
            actual = topify_rollout.summarize_run(
                log_dir / "runs" / run_id,
                adapter_id, rollout["fixed_config"],
                pre_rollout_reviewed_at=pre_review_time if review_sample else None,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise common.PipelineError(f"raw SForge run cannot be replayed: {run_id}: {exc}") from exc
        _require(actual == stored and actual["status"] == "complete",
                 f"collected rollout differs from raw SForge report: {run_id}")
    trajectories = []
    for run in rollout["runs"]:
        if run.get("status") != "complete":
            continue
        points = run.get("trajectory")
        _require(isinstance(points, list) and points, "completed run lacks scored trajectory")
        measured = []
        previous = -1.0
        best = -1.0
        for point in points:
            _require(isinstance(point, dict), "invalid score-time point")
            elapsed, score = point.get("elapsed_seconds"), point.get("score")
            _require(_number(elapsed) and elapsed >= previous and _number(score, 0, 100),
                     "invalid score-time point or time order")
            previous, best = float(elapsed), max(best, float(score))
            measured.append({
                "round": point["round"], "elapsed_seconds": elapsed,
                "score": score, "valid": point["valid"], "best_so_far": best,
                "report_sha256": point["report_sha256"],
                "archive_sha256": point["archive_sha256"],
            })
        _require(best == run["best_score"], "trajectory best does not match rollout result")
        trajectories.append({"run_id": run["run_id"], "points": measured,
                             "archived_rounds": sorted(_selected_snapshot_rounds(run)),
                             "unscored_attempts": run.get("unscored_attempts", [])})
    trajectories.sort(key=lambda item: item["run_id"])
    namespace = metadata["task_id"].lower().replace("/", "-")
    _require(ADAPTER_ID.fullmatch(namespace) is not None, "unsafe task namespace")
    prefix = f"tasks/{namespace}"
    files: dict[str, Path | bytes] = {
        f"{prefix}/BENCHMARK.yaml": benchmark_path,
        f"{prefix}/{adapter_id}.json": definition_path,
        f"{prefix}/metadata/score-table.csv": _csv_bytes(rows),
        f"{prefix}/metadata/trajectories.json": _json_bytes({
            "schema_version": 1, "task_sha256": task_hash, "runs": trajectories,
        }),
        f"{prefix}/metadata/summary.json": _json_bytes({
            "schema_version": 1, "task_id": metadata["task_id"], "task_sha256": task_hash,
            "adapter_task_id": adapter_id, "review_status": review["status"],
            "scored_contexts_sha256": hashes,
            "rollout_status": rollout["status"], "fixed_config": rollout["fixed_config"],
            "completed_run_count": len(rows), "mean_best_score": rollout["mean_best_score"],
            "score_formula": metadata["request"]["scoring"]["formula"],
            "raw_metric": metadata["request"]["scoring"]["raw_metric"],
            "declared_baseline": metadata["request"]["scoring"]["baseline"],
            "declared_achievable_upper": metadata["request"]["scoring"]["upper"],
            "score_table": "score-table.csv",
        }),
        f"{prefix}/metadata/images.json": _json_bytes({
            "schema_version": 1,
            "work_image": work_image, "judge_image": judge_image,
            "platform": definition["platform"],
            "contexts": {"work": "contexts/work", "judge": "contexts/judge"},
        }),
    }
    # Ship only receipted scored reports and archives, never raw run configs,
    # agent transcripts, unrelated trials, or credential-bearing service logs.
    for run in rollout["runs"]:
        if run.get("status") != "complete":
            continue
        run_id = run["run_id"]
        _require(isinstance(run_id, str) and RUN_ID.fullmatch(run_id) is not None,
                 "unsafe SForge run ID")
        submissions = log_dir / "runs" / run_id / adapter_id / "submissions"
        _directory(submissions)
        archived = _selected_snapshot_rounds(run)
        for point in run["trajectory"]:
            round_id = point["round"]
            _require(isinstance(round_id, str) and
                     re.fullmatch(r"(?:agent|auto|manual)-[0-9]+", round_id) is not None,
                     "unsafe SForge submission round")
            folder = submissions / round_id
            _directory(folder)
            named_files = [("report.json", "report_sha256")]
            if round_id in archived:
                named_files.append(("submission.tar.gz", "archive_sha256"))
            for name, bound_hash in named_files:
                file = folder / name
                _file(file)
                _require(common.sha256_file(file) == point[bound_hash],
                         "scored SForge submission changed after collection")
                if name == "submission.tar.gz":
                    _check_scored_archive(file, definition.get("submit_paths"))
                files[f"{prefix}/evidence/runs/{run_id}/submissions/{round_id}/{name}"] = file
    nop_dir = task / "nop"
    if nop_dir.is_dir() and not nop_dir.is_symlink():
        for path in _children(nop_dir):
            files[f"{prefix}/nop/{path.relative_to(nop_dir).as_posix()}"] = path
    solution_dir = task / "solution"
    if metadata["request"]["reference_mode"] == "reference" and solution_dir.is_dir() and not solution_dir.is_symlink():
        for path in _children(solution_dir):
            files[f"{prefix}/solution/{path.relative_to(solution_dir).as_posix()}"] = path
    parity_scores = {item.get("name"): item.get("sforge_score_0_100") for item in adapter.get("parity", []) if isinstance(item, dict)}
    native_measure = topify_completion._latest(root / "evidence/image-measurements/linux-amd64")
    endpoints = {
        "schema_version": 1, "task_sha256": task_hash,
        "nop_sforge_score": parity_scores.get("nop"),
        "reference_sforge_score": parity_scores.get("strong") if metadata["request"]["reference_mode"] == "reference" else "open-solution",
        "reference_submission_count": native_measure.get("reference_submission_count"),
        "frontier_completed_runs": rollout["completed_run_count"],
        "frontier_mean_best_score": rollout["mean_best_score"],
    }
    files[f"{prefix}/metadata/endpoints.json"] = _json_bytes(endpoints)
    # Customer inspection conclusions are rendered from the checked audit,
    # not a copy of internal author/reviewer receipts and workspace paths.
    if review_sample:
        comparison = common.load_json(root / "evidence/owner-arm-comparison.json")
        files[f"{prefix}/evidence/owner-arm-comparison.json"] = root / "evidence/owner-arm-comparison.json"
        references = [comparison["images"]]
        for entry in comparison["parity"].values():
            references.extend(entry.values())
        for entry in references:
            path = topify_owner_arm._verified(root, entry)
            files[f"{prefix}/{entry['path']}"] = path
    for name, folder in (
        ("local-measurement", "measurements"),
        ("batch-nop", "sforge-batch-nop"),
        ("multiarch-build", "builds"),
        ("resource-agreement", "resource-agreement"),
        ("data-hashes", "data-hashes"),
    ):
        original = topify_completion._latest(root / f"evidence/{folder}")
        if original.get("task_sha256") == task_hash and original.get("status") == "complete":
            if name == "multiarch-build":
                original = {
                    "status": original["status"], "task_sha256": task_hash,
                    "base_images": original.get("base_images"),
                    "images": [{"name": image.get("name"), "platforms": image.get("platforms"),
                                "index_digest": image.get("index_digest")}
                               for image in original.get("images", []) if isinstance(image, dict)],
                    "build_network": original.get("build_network"),
                }
            elif name == "resource-agreement":
                original = {key: original[key] for key in ("status", "task_sha256", "resources")}
            elif name == "batch-nop":
                path_value = original.get("batch_report_path")
                _require(isinstance(path_value, str) and Path(path_value).is_absolute(),
                         "complete batch NOP receipt needs a real batch report")
                batch_path = Path(path_value)
                _file(batch_path)
                _require(common.sha256_file(batch_path) == original.get("batch_report_sha256"),
                         "batch NOP report changed after measurement")
                raw_batch = common.load_json(batch_path)
                matches = [item for item in raw_batch.get("results", [])
                           if isinstance(item, dict) and item.get("task_sha256") == task_hash]
                _require(raw_batch.get("status") == "complete"
                         and raw_batch.get("task_sha256s") == original.get("task_sha256s")
                         and len(matches) == 1 and matches[0].get("status") == "complete",
                         "batch NOP source does not match the selected task")
                selected = {key: matches[0].get(key) for key in (
                    "status", "sforge_nop_score", "expected_lower_bound", "nop_script_sha256",
                    "snapshot_sha256", "report_sha256", "process_sha256")}
                scoped_report = _json_bytes({
                    "schema_version": 1, "status": "complete", "task_sha256": task_hash,
                    "batch_task_count": len(original["task_sha256s"]), "result": selected,
                })
                files[f"{prefix}/metadata/evidence/sforge-batch-report.json"] = scoped_report
                original = {
                    key: original.get(key) for key in (
                        "status", "task_sha256",
                        "sforge_nop_score", "nop_script_sha256", "report_sha256")
                } | {"batch_report_member": "metadata/evidence/sforge-batch-report.json",
                     "batch_report_sha256": hashlib.sha256(scoped_report).hexdigest()}
            files[f"{prefix}/metadata/evidence/{name}.json"] = _json_bytes(original)
    for architecture in sorted(common.ARCHITECTURES):
        native = topify_completion._latest(
            root / "evidence/image-measurements" / architecture.replace("/", "-")
        )
        if native.get("status") == "complete" and native.get("task_sha256") == task_hash:
            files[f"{prefix}/metadata/evidence/native-{architecture.rsplit('/', 1)[-1]}.json"] = _json_bytes({
                "status": native["status"], "task_sha256": task_hash,
                "observed_host_architecture": native.get("observed_host_architecture"),
                "scores": native.get("scores"), "reference_repeat": native.get("reference_repeat"),
                "reference_submission_count": native.get("reference_submission_count"),
                "timing": native.get("timing"), "image": native.get("image"),
            })
    for role in ("work", "judge"):
        context = tasks_dir / "contexts" / role
        _directory(tasks_dir / "contexts")
        paths = _children(context)
        _require(paths and context / "Dockerfile" in paths, f"SForge {role} context is empty or lacks Dockerfile")
        for path in paths:
            files[f"{prefix}/contexts/{role}/{path.relative_to(context).as_posix()}"] = path
    instruction = (task / "instruction.md").read_text(encoding="utf-8")
    _require(bool(instruction.strip()), "task instruction is missing")
    return namespace, files, {
        "task_id": metadata["task_id"], "task_sha256": task_hash,
        "namespace": namespace, "category": metadata["request"]["category"],
        "adapter_id": adapter_id, "budget": metadata["request"]["interaction_budget_seconds"],
        "platform": definition["platform"], "work_image": work_image, "judge_image": judge_image,
        "build_network": topify_build.declared_build_network(task),
        "goal": next((line.strip() for line in instruction.splitlines() if line.strip()), ""),
        "scenario": customer_copy["english"]["scenario"],
        "consequence": customer_copy["english"]["consequence"],
        "why_long": customer_copy["english"]["why_long"],
        "scenario_zh": customer_copy.get("chinese", {}).get("scenario", ""),
        "consequence_zh": customer_copy.get("chinese", {}).get("consequence", ""),
        "why_long_zh": customer_copy.get("chinese", {}).get("why_long", ""),
        "raw_metric": metadata["request"]["scoring"]["raw_metric"],
        "formula": metadata["request"]["scoring"]["formula"],
        "rows": rows,
        "quality_status": {key: review["quality"][key]["status"] for key in topify_review.QUALITY_IDS},
    }


def domain_coverage(tasks: list[dict]) -> dict:
    """Keep samples honest and reject a one-domain 100-task campaign."""
    _require(bool(tasks), "domain coverage needs at least one task")
    categories = [item.get("category") for item in tasks]
    _require(all(category in common.CATEGORIES for category in categories),
             "package task category is not in the six EdgeBench categories")
    counts = Counter(categories)
    absent = sorted(common.CATEGORIES - set(counts))
    full_campaign = len(tasks) >= 100
    if full_campaign:
        _require(not absent, "a 100-task campaign must cover all six EdgeBench categories")
    return {"schema_version": 1,
            "status": "six-domain-complete" if full_campaign else "sample-only",
            "observed_task_count": len(tasks),
            "counts": {category: counts.get(category, 0)
                       for category in sorted(common.CATEGORIES)},
            "uncovered_categories": absent,
            "planning_note": "The DOCX proportions guide selection but are not an exact acceptance quota."}


def _readme(batch_id: str, tasks: list[dict], draft: bool = False,
             customer_language: str = "en-zh", *, review_sample: bool = False,
             missing: list[str] | None = None) -> bytes:
    count = len(tasks)
    title = f"【Morrow Lab】 EdgeBench — {count}-task SForge sample delivery"
    lines = [
        f"# {title}", "", "This batch contains independently runnable, offline SForge tasks for sustained",
        "agent work. Each task supplies its prepared work and judge contexts, observed model",
        "scores, and a snapshot-based verifier. The task JSON retains the original English",
        "agent instructions and runtime paths.", "", "## Recorded model executions", "",
        "| Task | Model / effort | Trial | Observed best score (0–100) | SForge run wall time | Agent Step | Evidence |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    if draft:
        lines[2:2] = [
            "**DRAFT — NOT A CUSTOMER DELIVERY. Required DOCX non-format evidence is incomplete or unverified.**",
            "This draft uses image_tag-only tasks; stock SForge cannot run `sforge build --task` from these definitions.",
            "",
        ]
    if review_sample:
        lines[0] = "# 【Morrow Lab】 EdgeBench — 1-task SForge review sample"
        lines[2:7] = [
            "**INCOMPLETE REVIEW SAMPLE — not an accepted production handoff.**",
            "The recorded model scores describe only their archived snapshots; several technical",
            "requirements, including runtime isolation and resource evidence, are not established",
            "by the material in this package. The ZIP contains the frozen SForge definition,",
            "the original English instructions and the scored evidence actually retained.", "",
        ]
    for item in tasks:
        prefix = f"tasks/{item['namespace']}"
        if not item["rows"]:
            lines.append(f"| {_cell(item['task_id'])} | Not measured | — | — | not recorded | not recorded | "
                         f"[empty measured trial table]({prefix}/metadata/score-table.csv) |")
            continue
        row = item["rows"][0]  # Stable representative, never reinterpreted as success.
        lines.append(
            f"| {_cell(item['task_id'])} | {_cell(row['model'])} / {_cell(row['effort'])} | {_cell(row['trial_id'])} "
            f"| {row['score']} | {_format_seconds(row['run_wall_seconds'])} | {row['step']} "
            f"| [all measured trials]({prefix}/metadata/score-table.csv) |"
        )
    lines.extend([
        "No completed model trials are claimed for a task whose table says Not measured."
        if review_sample and any(not item["rows"] for item in tasks) else "",
        "", "Scores are continuous; no binary pass/fail threshold or successful trajectory is",
        "asserted. The displayed trial is the first in trial-ID order, not an inferred success.",
        "Wall time is the recorded SForge `runtime_seconds` for that full-budget run, rounded",
        "to the nearest second (HH:MM:SS); it is not a separately measured Agent-execution",
        "interval. Agent Step is shown only when that trial reports `agent_steps`; otherwise",
        "it is 'not recorded'. No Step count is derived from submissions or tool calls.",
        "", "## Why these tasks", "",
        "I designed this batch around iterative work: an agent can edit a solution, request",
        "feedback, and improve the retained best snapshot within a fixed time budget.",
        "A score indicates the verifier's measured outcome for that snapshot, not a claim",
        "that a business requirement is fully satisfied.", "", "## Task guide", "",
    ])
    for item in tasks:
        prefix = f"tasks/{item['namespace']}"
        lines.extend([
            f"### {_cell(item['task_id'])}", "",
            f"The English task starts: {_cell(item['goal'])}",
            f"Work: {_cell(item['scenario'])}",
            f"Consequence of a bad result: {_cell(item['consequence'])}",
            f"Why iterative work matters: {_cell(item['why_long'])}",
            f"Domain: `{_cell(item['category'])}`. Raw metric: {_cell(item['raw_metric'])}.",
            f"Verifier's declared score formula: `{_cell(item['formula'])}`.",
            f"Open the [English task JSON]({prefix}/{item['adapter_id']}.json) and "
            f"[BENCHMARK.yaml]({prefix}/BENCHMARK.yaml). The CSV lists each measured trial.", "",
        ])
    lines.extend([
        "## Scoring and reproducibility", "",
        "The SForge judge evaluated the archived submissions; offline isolation is unverified."
        if review_sample or draft else "The offline judge runs the task's prepared SForge verifier against submitted",
        "The score table records each complete full-budget run's best measured"
        if review_sample or draft else "snapshots. The score table records each complete full-budget run's best measured",
        "score; `metadata/summary.json` in each task directory records the task hash,",
        "configuration, review status and mean best score; `metadata/trajectories.json`",
        "contains each retained run's scored submissions over time. A missing Step is not a zero.",
        "Every scored report and the best/checkpoint snapshot archives live under",
        "`evidence/runs/<run-id>/submissions/`; remaining archive hashes stay in the curve.",
        "Unscored infrastructure rounds are indexed separately by log hash; raw host logs and",
        "credential-bearing `run_config.json` files are not included.",
        "The SForge Judge's continuous score is separate from task-definition",
        "review; it is not a Q1–Q7 rubric total or a binary success claim.",
        "`metadata/endpoints.json` separates the executed SForge NOP, measured reference",
        "(or open-solution), and frontier-run mean. The reference is a measured",
        "achieved snapshot, not a claim that every legal solution was tested.",
        "`metadata/domain-coverage.json` records observed counts and missing DOCX",
        "categories. A small sample does not claim to be the full six-domain campaign.",
        "The package inventory and included calibration metadata show their actual evidence scope.",
        "SForge run wall time includes framework overhead; the collection does not expose",
        "a separate Agent-execution interval. Scores describe these retained trials only.",
        "", "## Package layout and use", "",
        "`tasks/<namespace>/` is an independent SForge tasks directory, containing",
        "`BENCHMARK.yaml`, one task JSON, and `contexts/work/` and `contexts/judge/`.",
        "The paths inside each task JSON remain the original runtime `/workspace` paths;",
        "ZIP paths are packaging locations, not locations to edit during a run.",
        "When present, `nop/nop.sh` and `solution/solve.sh` are delivered authoring",
        "scripts; they are not copied into the work image or shown to the agent.",
        "Point SForge at one `tasks/<namespace>/` directory at a time, build its prepared",
        "contexts/images for your host, then run that task with its recorded model/budget",
        "configuration. Start with the score CSV, read the JSON task instruction, inspect",
        "the contexts and verifier, and compare scores with the summary. `metadata/sha256.json`",
        "checks the bytes of every other ZIP file. Image binaries and raw agent logs are",
        "not included; image builds and model runs require your own infrastructure.",
        "Build-time network access follows each task's frozen policy; public builds may fetch",
        "the declared base and dependencies, while legacy no-network builds remain restricted.",
        "the scored Work and Judge runtime must be independently checked for no-network isolation.",
        "After building images, start a judge endpoint separately, for example",
        "`sforge --tasks-dir tasks/<namespace> serve --port 8080`; use its reachable",
        "container URL as `--judge-url`. Do not restart a service serving live runs.",
        "Example build and run commands for each task:", "",
    ])
    for item in tasks:
        if not item["rows"]:
            continue
        row = item["rows"][0]
        lines.extend([
            "```sh",
            f"docker buildx build --platform {item['platform']} --network {item['build_network']} --load --tag {item['work_image']} "
            f"tasks/{item['namespace']}/contexts/work",
            f"docker buildx build --platform {item['platform']} --network {item['build_network']} --load --tag {item['judge_image']} "
            f"tasks/{item['namespace']}/contexts/judge",
            f"sforge --tasks-dir tasks/{item['namespace']} run --task {item['adapter_id']} "
            f"--agent {row['agent']} --model {row['model']} --effort {row['effort']} "
            f"--timeout {item['budget']} --judge-url <your-judge-endpoint> "
            "--disable-internet --run-id <new-run-id>",
            "```", "",
        ])
    lines.extend([
        "Use the recorded budget for comparisons; the endpoint and image builds must be",
        "supplied locally. A new run is not part of the retained score evidence.",
    ])
    if customer_language == "en":
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    lines.extend([
        "", "## 中文翻译", "", f"## 【Morrow Lab】 EdgeBench — {count} 题 SForge 小样交付包", "",
        "本批任务供智能体在离线 SForge 环境中反复修改、提交快照和读取反馈。每题单独提供",
        "工作与判分上下文、真实模型成绩以及机器可读的评分摘要；模型看到的英文题面和运行路径保持原样。",
        "", "## 已记录的模型执行", "",
        "| 任务 | 模型 / 推理档位 | Trial | 实测最高分 (0–100) | SForge 运行墙钟时间 | Agent Step | 证据 |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for item in tasks:
        row = item["rows"][0]
        prefix = f"tasks/{item['namespace']}"
        step = row["step"] if row["step"] != "not recorded" else "未记录"
        lines.append(f"| {_cell(item['task_id'])} | {_cell(row['model'])} / {_cell(row['effort'])} "
                     f"| {_cell(row['trial_id'])} | {row['score']} | {_format_seconds(row['run_wall_seconds'])} "
                     f"| {step} | [全部实测执行]({prefix}/metadata/score-table.csv) |")
    lines.extend([
        "", "这里是连续得分，未设定二元通过线，也不把最高分当成成功轨迹。表中按 trial ID",
        "排序展示第一条记录。墙钟时间直接取该次 SForge 运行的 `runtime_seconds`，四舍五入",
        "到秒（时:分:秒），不是单独测得的 Agent 执行时间；只有该条记录含 `agent_steps`",
        "时才展示 Step，否则标记“未记录”，不从提交次数或工具调用推算。",
        "", "## 为什么设计这组任务", "",
        "我设计这组任务，是为了检验智能体能否在固定预算内修改方案、获取反馈并改善",
        "保留的最佳快照。分数仅反映判分器对具体快照的测量，不等于业务目标全部完成。",
        "", "## 任务一览", "",
    ])
    for item in tasks:
        lines.extend([
            f"### {_cell(item['task_id'])}", "",
            f"英文题面开头：{_cell(item['goal'])}",
            f"工作场景：{_cell(item['scenario_zh'])}",
            f"错误结果的后果：{_cell(item['consequence_zh'])}",
            f"为何需要多轮改进：{_cell(item['why_long_zh'])}",
            f"领域：`{_cell(item['category'])}`；原始指标：{_cell(item['raw_metric'])}。",
            f"Verifier 声明的分数公式：`{_cell(item['formula'])}`。",
            f"阅读[英文任务 JSON](tasks/{item['namespace']}/{item['adapter_id']}.json) 与 "
            f"[BENCHMARK.yaml](tasks/{item['namespace']}/BENCHMARK.yaml)；",
            "CSV 包含每次真实运行的得分与耗时。", "",
        ])
    lines.extend([
        "## 判分与复现", "",
        "离线 judge 对提交的快照运行配套 SForge 判分器。每题的 `metadata/summary.json`",
        "记录任务哈希、模型配置、审阅就绪状态和最高分均值；CSV 为逐次实测结果，",
        "`metadata/trajectories.json` 保存逐次评分随时间的轨迹；",
        "Q1–Q7 题目审阅不属于 SForge 连续分或模型通过率。",
        "`evidence/runs/<run-id>/submissions/` 收录全部真实评分报告，以及最佳分和时间检查点的快照；",
        "其余快照保留 SHA-256，未评分基础设施事件另记日志哈希，",
        "不打包带密钥的原始 `run_config.json` 或宿主日志。缺失的 Step 不等于零。",
        "`metadata/endpoints.json` 分列 NOP、参考解（或 open-solution）和前沿模型均分；",
        "`metadata/domain-coverage.json` 如实记录六类题目的实际数量与小样缺口；",
        "ZIP 内的哈希清单和实际收录的校准数据说明可复查范围。",
        "SForge 运行墙钟时间包含框架开销，不能充当单独的 Agent 执行耗时。",
        "这些分数只适用于保留的具体运行。", "", "## 目录与使用顺序", "",
        "`tasks/<namespace>/` 各自是独立的 SForge tasks 目录，包含 `BENCHMARK.yaml`、",
        "任务 JSON、`contexts/work/` 和 `contexts/judge/`。JSON 中 `/workspace` 是运行路径，",
        "ZIP 内路径是交付位置。逐题将 SForge 指向相应目录，在本机构建所需镜像，按记录的",
        "模型和预算运行；先读 CSV，再读英文题面、上下文及判分器，最后核对摘要。",
        "英文部分的逐题命令给出 SForge 调用模板；判分服务地址、镜像及新 run ID",
        "须由使用方提供，新运行不属于本包留存的实测成绩。",
        "`metadata/sha256.json` 可校验其他所有 ZIP 文件字节。包内不含镜像二进制或原始模型日志；",
        "构建和运行需自行准备基础设施。", "",
    ])
    return ("\n".join(lines)).encode("utf-8")


def package_batch(manifest_path: Path, output_path: Path, *, draft: bool = False,
                  review_dir: Path | None = None) -> tuple[dict, int]:
    """Build a SForge-only ZIP; return (report, 0), or (incomplete report, 2)."""
    created = False
    customer_files: list[Path] = []
    created_review = False
    output = Path(output_path).absolute()
    private = Path(review_dir).absolute() if review_dir is not None else None
    formal_name = None
    try:
        import topify_customer_safety

        manifest = common.load_json(manifest_path)
        topify_delivery_contract.manifest_format(manifest, "sforge-only")
        review_sample = manifest.get("delivery_profile") == "owner-sample-2026-09-29"
        _require(set(manifest) in ({"schema_version", "batch_id", "workspaces"},
                                   {"schema_version", "batch_id", "workspaces", "delivery_language"},
                                   {"schema_version", "batch_id", "workspaces", "delivery_language", "delivery_profile"},
                                   {"schema_version", "batch_id", "workspaces", "delivery_format"},
                                   {"schema_version", "batch_id", "workspaces", "delivery_format", "delivery_language"})
                  and type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
                  "batch manifest schema must have version, ID, workspaces and optional delivery_language")
        language = manifest.get("delivery_language", "en")
        _require(language in {"en-zh", "en"}, "delivery_language must be en-zh or en")
        _require("delivery_profile" not in manifest or review_sample, "unsupported delivery profile")
        if not draft:
            _require(private is not None, "Meta handoff requires --review-dir for isolated Chinese owner documents")
            _require(language == "en", "Meta customer ZIP and QC report must be English-only")
        _require(not review_sample or (language == "en" and not draft),
                 "owner review sample requires English and cannot use draft mode")
        if review_sample:
            _require(output.name == "【Morrow lab】edgebench sample.zip", "wrong owner sample ZIP name")
        elif not draft:
            pattern = (r"【Morrow Lab】 EdgeBench 小样 (\d{4}-\d{2}-\d{2})\.zip" if language == "en-zh"
                       else r"【Morrow Lab】EdgeBench Sample (\d{4}-\d{2}-\d{2})\.zip")
            formal_name = re.fullmatch(pattern, output.name)
            _require(formal_name is not None, "formal ZIP name does not match the selected delivery_language")
        batch_id = manifest.get("batch_id")
        _require(isinstance(batch_id, str) and SLUG.fullmatch(batch_id) is not None, "unsafe batch_id")
        workspaces = manifest.get("workspaces")
        _require(isinstance(workspaces, list) and len(workspaces) > 0, "batch requires workspaces")
        _require(not review_sample or len(workspaces) == 1, "owner sample has exactly one task")
        _require(not output.exists() and not output.is_symlink(), "refusing to overwrite output")
        _directory(output.parent)
        _require(output.parent == output.parent.resolve(strict=True), "output parent must be canonical")
        policy_path = PROJECT_ROOT / "delivery-policy.json"
        if policy_path.exists() or policy_path.is_symlink():
            policy = common.load_json(policy_path)
            _require(set(policy) == {"schema_version", "customer_dir", "private_review_root", "workspace_root"}
                     and type(policy.get("schema_version")) is int and policy["schema_version"] == 1
                     and all(isinstance(policy.get(key), str) and Path(policy[key]).is_absolute()
                             for key in ("customer_dir", "private_review_root", "workspace_root")),
                     "delivery policy is malformed")
            customer_root = Path(policy["customer_dir"])
            private_root = Path(policy["private_review_root"])
            workspace_root = Path(policy["workspace_root"])
            _directory(customer_root)
            _directory(private_root)
            _directory(workspace_root)
            _require(customer_root == customer_root.resolve(strict=True)
                     and private_root == private_root.resolve(strict=True)
                     and workspace_root == workspace_root.resolve(strict=True)
                     and not customer_root.is_relative_to(private_root)
                     and not private_root.is_relative_to(customer_root),
                     "delivery policy directories must be canonical and separate")
            if draft:
                _require(not output.parent.is_relative_to(customer_root),
                         "delivery policy forbids draft artifacts in customer output")
            else:
                _require(output.parent == customer_root and private is not None
                         and private.parent == private_root,
                         "delivery policy fixes customer output and isolated Chinese review paths")
        if private is not None:
            _require(not private.is_relative_to(output.parent)
                     and not private.exists() and not private.is_symlink()
                     and private.parent.is_dir() and not private.parent.is_symlink()
                     and private.parent == private.parent.resolve(strict=True),
                     "Chinese review files need a new isolated directory with a canonical parent")
        if review_sample:
            _require(not any(output.parent.iterdir()), "owner sample needs an empty output directory")
        files: dict[str, Path | bytes] = {}
        tasks = []
        seen_roots = set()
        seen_names = set()
        seen_hashes = set()
        for workspace in workspaces:
            _require(isinstance(workspace, str) and Path(workspace).is_absolute(), "workspace path must be absolute")
            root, metadata = common.load_workspace(workspace)
            if not draft and policy_path.exists():
                _require(root != workspace_root and root.is_relative_to(workspace_root),
                         "delivery policy requires task workspace within the installed workspace root")
            _require(root not in seen_roots, "duplicate workspace")
            seen_roots.add(root)
            audit = topify_sforge_acceptance.audit_task(root, manifest=manifest if review_sample else None)
            if manifest.get("delivery_format") is not None:
                contract = topify_delivery_contract.current(root, audit["task_sha256"])
                _require(contract.get("format") == "sforge-only",
                         "selected task delivery format does not match sforge-only manifest")
            if not draft and not review_sample:
                _require(audit["status"] == "complete",
                          "missing non-format SForge delivery evidence: " + ", ".join(audit["missing"]))
            namespace, payload, item = _task_bundle(
                root, metadata, language, review_sample=review_sample, audit=audit)
            item["audit"] = audit
            _require(namespace not in seen_names and item["task_sha256"] not in seen_hashes,
                     "batch tasks must be distinct in namespace and content")
            seen_names.add(namespace)
            seen_hashes.add(item["task_sha256"])
            files.update(payload)
            tasks.append(item)
        tasks.sort(key=lambda item: item["namespace"])
        files["metadata/domain-coverage.json"] = _json_bytes(domain_coverage(tasks))
        files["README.md"] = _readme(batch_id, tasks, draft, language,
                                      review_sample=review_sample,
                                      missing=audit["missing"] if review_sample else None)
        hashes = {name: hashlib.sha256(source).hexdigest() if isinstance(source, bytes)
                  else common.sha256_file(source)
                   for name, source in files.items()}
        files["metadata/sha256.json"] = _json_bytes({
            "schema_version": 1, "batch_id": batch_id, "files": dict(sorted(hashes.items())),
        })
        for name, source in files.items():
            topify_customer_safety.check(name, source,
                                         allow_chinese=draft and language == "en-zh" and name == "README.md")
        # Exclusive creation prevents a concurrent writer from being overwritten.
        with output.open("xb") as handle:
            created = True
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
                for name in sorted(files):
                    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    source = files[name]
                    permission = source.stat().st_mode & 0o777 if isinstance(source, Path) else 0o644
                    info.external_attr = (stat.S_IFREG | permission) << 16
                    digest = hashlib.sha256()
                    with bundle.open(info, "w") as destination:
                        if isinstance(source, bytes):
                            destination.write(source)
                            digest.update(source)
                        else:
                            _file(source)
                            with source.open("rb") as input_file:
                                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                                    digest.update(chunk)
                                    destination.write(chunk)
                    if name != "metadata/sha256.json":
                        _require(digest.hexdigest() == hashes[name], f"source changed during packaging: {name}")
        with zipfile.ZipFile(output) as archive:
            _require(archive.testzip() is None, "customer ZIP CRC check failed")
            _require(set(archive.namelist()) == set(files), "customer ZIP inventory changed")
            _require(all(hashlib.sha256(archive.read(name)).hexdigest() == digest
                         for name, digest in hashes.items()), "customer ZIP SHA-256 inventory mismatch")
        inspection = None
        if review_sample or not draft and formal_name is not None:
            report_name = ("EdgeBench Sample Quality Inspection Report.md" if review_sample else
                           f"【Morrow Lab】EdgeBench 小样质检报告 {formal_name.group(1)}.md"
                           if language == "en-zh" else
                           f"【Morrow Lab】EdgeBench Sample Quality Inspection Report {formal_name.group(1)}.md")
            report_file = output.parent / report_name
            inspection, inspection_code = topify_quality_report.quality_report(manifest_path, report_file)
            customer_files.append(report_file)
            topify_customer_safety.check(report_file.name, report_file)
            _require(review_sample or inspection_code == 0,
                     "independent customer quality report is not complete")
        review_files = None
        if private is not None:
            import topify_customer_documents

            readme_zh = topify_customer_documents.review_readme(tasks)
            qc_zh = topify_customer_documents.review_qc(tasks)
            date_part = formal_name.group(1) if formal_name is not None else common.utc_now()[:10]
            readme_name = f"【Morrow Lab】EdgeBench README.zh-CN {date_part}.md"
            qc_name = f"【Morrow Lab】EdgeBench 小样质检报告 {date_part}.md"
            topify_customer_safety.check(readme_name, readme_zh, allow_chinese=True)
            topify_customer_safety.check(qc_name, qc_zh, allow_chinese=True)
            private.mkdir(mode=0o700)
            created_review = True
            review_files = {}
            for name, content in ((readme_name, readme_zh), (qc_name, qc_zh)):
                target = private / name
                with target.open("xb") as handle:
                    handle.write(content)
                target.chmod(0o600)
                review_files["readme" if name == readme_name else "quality_report"] = str(target)
        return {"status": "review_sample_incomplete" if review_sample else "draft" if draft else "complete",
                "batch_id": batch_id, "task_count": len(tasks),
                 "tasks": [{"task_id": item["task_id"], "task_sha256": item["task_sha256"],
                            "namespace": item["namespace"]} for item in tasks],
                 "archive_sha256": common.sha256_file(output), "output": str(output),
                  "quality_report": inspection, "owner_review_documents": review_files,
                  "formal_eligible": not (draft or review_sample)}, 0
    except (common.PipelineError, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        if created_review:
            for file in private.iterdir():
                file.unlink()
            private.rmdir()
        for customer_file in customer_files:
            customer_file.unlink(missing_ok=True)
        if created:
            output.unlink(missing_ok=True)
        return {"status": "incomplete", "reason": str(exc)}, 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--draft", action="store_true", help="structural ZIP only; never formal delivery")
    parser.add_argument("--review-dir", type=Path, help="new private directory for Chinese owner-review documents")
    args = parser.parse_args()
    report, code = package_batch(args.manifest, args.output, draft=args.draft,
                                 review_dir=args.review_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
