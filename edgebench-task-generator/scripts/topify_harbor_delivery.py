#!/usr/bin/env python3
"""Export unpacked Harbor tasks with independently checked SForge run evidence."""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import math
from pathlib import Path
import re
import shutil
import tempfile

import topify_common as common
import topify_completion
import topify_delivery_contract
import topify_harbor_acceptance
import topify_review
import topify_rollout
import topify_sforge_delivery as sforge_files
import topify_sforge_acceptance
import topify_validate


audit_task = topify_harbor_acceptance.audit_task


def _manifest(path: Path) -> dict:
    value = common.load_json(path)
    required = {"schema_version", "batch_id", "workspaces", "vendor_name", "dataset_name", "delivery_date"}
    if (set(value) not in (required, required | {"delivery_language"},
                           required | {"delivery_format"},
                           required | {"delivery_format", "delivery_language"})
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("delivery_language", "en") != "en"
            or not isinstance(value.get("workspaces"), list) or not value["workspaces"]
            or not all(isinstance(root, str) and Path(root).is_absolute() for root in value["workspaces"])
            or not all(isinstance(value.get(key), str) and topify_harbor_acceptance.NAME.fullmatch(value[key])
                       for key in ("batch_id", "vendor_name", "dataset_name"))):
        raise common.PipelineError("Harbor manifest requires one or more absolute workspaces, vendor/dataset/date and English language")
    try:
        parsed = date.fromisoformat(value["delivery_date"])
        if parsed.isoformat() != value["delivery_date"]:
            raise ValueError("noncanonical date")
    except (TypeError, ValueError):
        raise common.PipelineError("Harbor delivery date must be YYYY-MM-DD")
    topify_delivery_contract.manifest_format(value, "harbor")
    return value


def _task_files(task: Path) -> dict[str, Path]:
    files = {}
    records = topify_validate.task_records(task)
    for record in records:
        if record["type"] not in {"file", "directory"}:
            raise common.PipelineError("Harbor task contains a symlink or unsupported entry")
        relative = Path(record["path"])
        if (any(part in {".git", ".env", "__pycache__", ".ssh"} for part in relative.parts)
                or relative.suffix == ".pyc" or relative.name == "run_config.json"):
            raise common.PipelineError("Harbor task contains a cache, credential or raw run configuration")
        if record["type"] == "directory":
            continue
        files[relative.as_posix()] = task / relative
    return files


def _validation_files(root: Path, task_sha: str) -> dict[str, Path | bytes]:
    """Include current scoped score/technical receipts, not whole host log trees."""
    files = {}
    folders = ("measurements", "harbor-runtime", "harbor-reproducibility",
               "harbor-determinism",
               "harbor-sforge-equivalence", "resource-agreement", "data-hashes", "sforge-batch-nop",
               "image-measurements/linux-amd64", "image-measurements/linux-arm64")
    for kind in folders:
        value = topify_completion._latest(root / "evidence" / kind)
        if value.get("task_sha256") != task_sha:
            continue
        files[f"receipts/{kind}/summary.json"] = sforge_files._json_bytes(value)
        for entry in value.get("process_evidence", []):
            if not topify_sforge_acceptance._verified_file(root, entry):
                raise common.PipelineError("reviewed Harbor process evidence changed before export")
            relative = Path(entry["path"])
            if (not entry["path"].startswith("evidence/") or relative.suffix not in {".json", ".txt"}
                    or relative.name == "run_config.json"
                    or any(part.startswith(".") for part in relative.parts)):
                raise common.PipelineError("technical evidence is not a scoped JSON/text process artifact")
            files[f"workspace-evidence/{relative.as_posix()}"] = root / relative
        number = value.get("attempt")
        if isinstance(number, int):
            attempt = root / "evidence" / kind / f"attempt-{number:04d}"
        elif isinstance(number, str):
            attempt = root / number
        else:
            continue
        if not attempt.is_dir() or attempt.is_symlink() or not attempt.resolve().is_relative_to(root):
            raise common.PipelineError("technical measurement attempt is unavailable or unsafe")
        for record in topify_validate.task_records(attempt / "runs"):
            if record["type"] == "directory":
                continue
            if record["type"] != "file":
                # Other evidence kinds legitimately have no runs directory.
                if record["type"] == "missing-or-unsafe" and not (attempt / "runs").exists():
                    continue
                raise common.PipelineError("measurement evidence contains unsafe entries")
            path = Path(record["path"])
            artifact = topify_validate.artifact_relative(root / "task")
            staged_snapshot = (len(path.parts) >= 3 and path.parts[1] == "workspace"
                               and Path(*path.parts[2:]).as_posix() == artifact)
            if path.name in {"report.json", "process.json", "stdout.json", "stderr.txt"} or staged_snapshot:
                files[f"receipts/{kind}/runs/{path.as_posix()}"] = attempt / "runs" / path
    return files


def _runs(root: Path, metadata: dict, task_sha: str, *, draft: bool) -> tuple[list[dict], dict[str, Path | bytes]]:
    """Only raw-replayed complete trials enter the customer tables."""
    collection = topify_completion._latest(root / "evidence/rollout-collections")
    if not collection:
        if draft:
            return [], {}
        raise common.PipelineError("SForge rollout collection is missing")
    adapter = topify_completion._latest(root / "evidence/sforge-adapters")
    adapter_id = adapter.get("adapter", {}).get("task_id")
    if adapter.get("task_sha256") != task_sha or not isinstance(adapter_id, str):
        raise common.PipelineError("SForge rollout does not identify the frozen Harbor task")
    rows = sforge_files._trial_rows(collection, metadata, task_sha, adapter_id, review_sample=draft)
    if rows and topify_harbor_acceptance._shared_core(root, task_sha)[0] != "complete":
        raise common.PipelineError("Cannot associate SForge trials with a changed or unbound Harbor scorer/input")
    logs = Path(collection.get("log_dir", ""))
    if not logs.is_absolute() or logs.is_symlink() or not (logs / "runs").is_dir():
        raise common.PipelineError("SForge raw runs directory is unavailable")
    reviewed_at = collection.get("pre_rollout_reviewed_at")
    if rows and (not isinstance(reviewed_at, str) or not reviewed_at):
        raise common.PipelineError("SForge cohort lacks original pre-rollout review timestamp")
    original_review = None
    if rows:
        earlier = [(path, common.load_json(path)) for path in
                   (root / "evidence/author-reviews").glob("attempt-*/summary.json")]
        original_review = next((path for path, item in earlier
                                if item.get("reviewed_at") == reviewed_at and item.get("task_sha256") == task_sha
                                and item.get("status") == "ready" and item.get("reviewer_role") == "main-agent"), None)
        if original_review is None:
            raise common.PipelineError("SForge cohort lacks its original task-bound ready review")
    artifact = topify_validate.artifact_relative(root / "task")
    files = {}
    if original_review is not None:
        files["pre-rollout-review.json"] = original_review
    for stored in collection.get("runs", []):
        if stored.get("status") != "complete":
            continue
        run_id = stored.get("run_id")
        if not isinstance(run_id, str) or not sforge_files.RUN_ID.fullmatch(run_id):
            raise common.PipelineError("unsafe SForge run ID")
        raw = logs / "runs" / run_id
        actual = topify_rollout.summarize_run(raw, adapter_id, collection["fixed_config"],
                                             pre_rollout_reviewed_at=reviewed_at)
        if actual != stored or actual["status"] != "complete":
            raise common.PipelineError("SForge raw scores differ from collected task cohort")
        folder = raw / adapter_id
        files[f"{run_id}/final_result.json"] = folder / "final_result.json"
        files[f"{run_id}/started_at"] = folder / "started_at"
        files[f"{run_id}/run_history.json"] = folder / "run_history.json"
        files[f"{run_id}/trajectory.json"] = sforge_files._json_bytes(actual)
        for unscored in actual.get("unscored_attempts", []):
            log = folder / unscored["log_path"]
            if common.sha256_file(log) != unscored["log_sha256"]:
                raise common.PipelineError("evidenced unscored evaluation log changed")
            files[f"{run_id}/{unscored['log_path']}"] = log
        for point in actual["trajectory"]:
            round_id = point["round"]
            if not isinstance(round_id, str) or not re.fullmatch(r"(?:agent|auto|manual)-[0-9]+", round_id):
                raise common.PipelineError("unsafe SForge submission round")
            scored = folder / "submissions" / round_id
            archive = scored / "submission.tar.gz"
            sforge_files._check_scored_archive(archive, [artifact])
            for name, key in (("report.json", "report_sha256"), ("submission.tar.gz", "archive_sha256")):
                source = scored / name
                if common.sha256_file(source) != point[key]:
                    raise common.PipelineError("scored report/archive changed during Harbor export")
                if name == "report.json" and common.load_json(source).get("task_id") != adapter_id:
                    raise common.PipelineError("scored report does not identify this frozen SForge task")
                files[f"{run_id}/submissions/{round_id}/{name}"] = source
    return rows, files


def _cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def _readme(manifest: dict, tasks: list[dict], draft: bool) -> bytes:
    lines = [f"# EdgeBench — {len(tasks)}-Task Harbor {'Diagnostic Draft' if draft else 'Customer Sample'}", "",
             "These tasks measure sustained improvement on concrete work artifacts, with",
             "continuous feedback and a best-submission score. Each task includes its original",
             "English instruction, separate Work/verifier contexts and the fixed scoring inputs.", ""]
    if draft:
        lines += ["**DRAFT — NOT A FORMALLY VERIFIED DELIVERY. Missing evidence is recorded in each task audit.**", ""]
    lines += ["## Recorded model results", "",
              "SForge is the rollout harness; the delivered tasks are Harbor. No separate upper/lower",
              "model roles are inferred from the scores. NOP and oracle are calibration programs, not models.", "",
              "| Task | Model / effort | Trial | Best score (0–100) | SForge run wall time | Agent Step | Evidence |",
              "|---|---|---|---:|---|---|---|"]
    for task in tasks:
        if not task["runs"]:
            lines.append(f"| {_cell(task['name'])} | No completed model trials | — | — | not recorded | not recorded | — |")
        for run in task["runs"]:
            link = f"trajectories/{task['name']}/{run['trial_id']}/trajectory.json"
            lines.append(f"| {_cell(task['name'])} | {_cell(run['model'])} / {_cell(run['effort'])} | "
                         f"{run['trial_id']} | {run['score']} | {sforge_files._format_seconds(run['run_wall_seconds'])} | "
                         f"{run['step']} | [scored snapshots]({link}) |")
    lines += ["", "Scores are continuous outcomes, not binary successes. SForge `runtime_seconds` includes",
              "framework overhead; it is not separately timed Agent execution. Steps remain 'not recorded'",
              "unless the same trial contains a verified step field. No steps are inferred from submissions.", "",
              "## Why I designed these tasks", "",
              "I designed these tasks around work that benefits from repeated diagnosis, lawful changes and",
              "validation of a retained artifact. The specific consequence and improvement loop for each task follow.", ""]
    for task in tasks:
        design = task["design"]
        lines += [f"## {task['name']}", "", _cell(design["real_work"]), "",
                  "Consequence: " + _cell(design["real_world_consequence"]), "",
                  "Why sustained iteration matters: " + _cell(design["long_horizon_reason"]), "",
                  f"Read [the English instruction](tasks/{task['name']}/instruction.md).",
                  f"Submit the artifact declared in [task.toml](tasks/{task['name']}/task.toml).",
                  "Use the public development split and local scorer before replacing an incumbent.", "",
                  "Scoring formula: `" + _cell(task["request"]["scoring"]["formula"]) + "`.", "",
                  f"[Inspection scope and evidence](validation/{task['name']}/audit.json) record this task's actual checks.", ""]
    lines += ["## Directory and reproduction", "", "```text", "README.md",
              "tasks/<task-name>/        # unpacked Harbor task, original task.toml and separate environment/tests",
              "validation/<task-name>/   # task-bound inspection findings",
              "metadata/sha256.json      # SHA-256 of each other delivered file", "```", "",
              "`trajectories/` exists only when real, completed SForge runs are included. Runtime `/workspace`",
              "paths refer to the task container, not the delivery folder. Do not edit archived trial evidence.", "",
              "Start with the task instruction, then its public inputs, scorer and archived results. Install",
              "the supported Harbor runtime and use each `tasks/<task-name>/` as the task directory; both",
              "Dockerfiles and contexts are included. Inspect pinned dependencies before rebuilding.", "",
              "Local export is not a Smoldata/S3 upload or recipient acceptance. This folder contains no",
              "credentials or raw model chat logs; model results apply only to the retained frozen identity.", ""]
    return ("\n".join(lines)).encode()


def _qc(tasks: list[dict], draft: bool) -> bytes:
    lines = ["# 【Morrow Lab】 EdgeBench Harbor Sample Quality Inspection Report", "",
             "## 1. Summary", "", "| Item | Result |", "|---|---|",
             f"| Delivered format | Unpacked Harbor; {len(tasks)} tasks |",
             "| Rollout harness | SForge; only raw-replayed complete runs are listed |",
             f"| Local inspection | {'DRAFT / incomplete' if draft else 'Applicable local evidence checks complete'} |",
             "| External platform acceptance | Not performed |",
             "| Task-quality scope | Q1–Q7; no eight-category 100-point quality score is assigned |", "",
             "## 2. Task Summary", ""]
    for task in tasks:
        lines += [f"### {task['name']}", "", _cell(task["design"]["real_work"]), "",
                  "Artifact: `" + topify_validate.artifact_relative(task["root"] / "task") + "`.",
                  "Consequence: " + _cell(task["design"]["real_world_consequence"]), ""]
    lines += ["## 3. Checks and Recorded Results", "",
              "### 3.1 Recorded model runs", "",
              "A single model cohort is not an upper-versus-lower model experiment. No binary success rate is inferred.", "",
              "| Task | Trial | Model / effort | Best score | SForge run wall time (s) | Agent Step |",
              "|---|---|---|---:|---:|---|"]
    for task in tasks:
        for run in task["runs"]:
            lines.append(f"| {task['name']} | {run['trial_id']} | {_cell(run['model'])} / {_cell(run['effort'])} | "
                         f"{run['score']} | {run['run_wall_seconds']} | {run['step']} |")
        if not task["runs"]:
            lines.append(f"| {task['name']} | No completed model trials | — | — | — | not recorded |")
    lines += ["", "### 3.2 Task-bound inspection matrix", "",
              "Static inspection, captured process evidence and model results have different scopes. Missing",
              "required evidence is not a measured pass, even if no implementation defect was observed.", "",
              "| Task | Check | Result | Basis |", "|---|---|---|---|"]
    for task in tasks:
        for row in task["audit"]["rows"]:
            lines.append(f"| {task['name']} | {_cell(row['id'])} | {row['status']} | {_cell(row['detail'])} |")
    lines += ["", "## 4. Task-definition review", "",
              "| Task | Criterion | Recorded status | Finding |", "|---|---|---|---|"]
    for task in tasks:
        review = topify_completion._latest(task["root"] / "evidence/author-reviews")
        quality = review.get("quality", {}) if review.get("task_sha256") == task["audit"]["task_sha256"] else {}
        for key in topify_review.QUALITY_IDS:
            entry = quality.get(key, {})
            lines.append(f"| {task['name']} | {key} | {_cell(entry.get('status', 'not recorded'))} | "
                         f"{_cell(entry.get('finding', 'No current observation'))} |")
    return ("\n".join(lines) + "\n").encode()


def export_batch(manifest_path: str | Path, output_path: str | Path, *, draft: bool = False) -> tuple[dict, int]:
    output = Path(output_path).absolute()
    stage = None
    created = False
    report_created = False
    report_file = output.parent / f"{output.name} Quality Report.md"
    try:
        manifest = _manifest(Path(manifest_path))
        if (output.exists() or output.is_symlink() or not output.parent.is_dir()
                or output.parent.resolve(strict=True) != output.parent
                or report_file.exists() or report_file.is_symlink()):
            raise common.PipelineError("Harbor export requires new output/report paths in an existing canonical parent")
        tasks = []
        files: dict[str, Path | bytes] = {}
        modes = {}
        directories = {}
        names = set()
        workspaces = set()
        missing = []
        for workspace in manifest["workspaces"]:
            root, metadata = common.load_workspace(workspace)
            if root in workspaces or output.is_relative_to(root):
                raise common.PipelineError("duplicate workspace or export destination inside task workspace")
            workspaces.add(root)
            name = topify_harbor_acceptance.customer_name(root / "task")
            if name in names or len(f"{manifest['vendor_name']}_{manifest['dataset_name']}_{name}") > 255:
                raise common.PipelineError("duplicate Harbor task leaf name or Meta 255-character naming limit exceeded")
            names.add(name)
            audit = audit_task(root)
            if manifest.get("delivery_format") is not None:
                contract = topify_delivery_contract.current(root, audit["task_sha256"])
                if contract.get("format") != "harbor":
                    raise common.PipelineError("selected task delivery format does not match harbor manifest")
            missing.extend(f"{name}:{key}" for key in audit["missing"])
            task_files = _task_files(root / "task")
            for record in topify_validate.task_records(root / "task"):
                if record["type"] == "directory":
                    directories[f"tasks/{name}/{record['path']}"] = (
                        root / "task" / record["path"]).stat().st_mode & 0o777
            for relative, source in task_files.items():
                destination = f"tasks/{name}/{relative}"
                files[destination] = source
                modes[destination] = source.stat().st_mode & 0o777
            design = common.load_json(root / "task_design.json")
            if design.get("request_sha256") != metadata["request_sha256"]:
                raise common.PipelineError("Harbor design belongs to another task request")
            if not draft and audit["status"] != "complete":
                continue
            runs, scored = _runs(root, metadata, audit["task_sha256"], draft=draft)
            files.update({f"trajectories/{name}/{key}": source for key, source in scored.items()})
            files.update({f"validation/{name}/{key}": source for key, source in
                          _validation_files(root, audit["task_sha256"]).items()})
            files[f"validation/{name}/audit.json"] = sforge_files._json_bytes(audit)
            tasks.append({"name": name, "root": root, "audit": audit, "design": design,
                          "request": metadata["request"], "runs": runs,
                          "category": metadata["request"]["category"]})
        if missing and not draft:
            return {"status": "incomplete", "format": "Harbor", "missing": [key.split(":", 1)[1] for key in missing],
                    "task_missing": missing, "formal_eligible": False, "platform_accepted": False}, 2
        coverage = sforge_files.domain_coverage(tasks)
        files["README.md"] = _readme(manifest, tasks, draft)
        files["metadata/domain-coverage.json"] = sforge_files._json_bytes(coverage)
        files["metadata/delivery.json"] = sforge_files._json_bytes({
            "schema_version": 1, "format": "Harbor", "rollout_harness": "SForge",
            "vendor_name": manifest["vendor_name"], "dataset_name": manifest["dataset_name"],
            "delivery_date": manifest["delivery_date"], "status": "draft" if draft else "local_complete",
            "platform_accepted": False, "external_delivery": "not_performed",
            "tasks": [{"name": task["name"], "task_sha256": task["audit"]["task_sha256"]} for task in tasks],
        })
        results = []
        trial_rows = []
        for task in tasks:
            measured = topify_completion._latest(task["root"] / "evidence/measurements")
            current = measured.get("task_sha256") == task["audit"]["task_sha256"]
            model_scores = [run["score"] for run in task["runs"]]
            results.append({
                "task_name": task["name"], "task_sha256": task["audit"]["task_sha256"],
                "nop_score": measured.get("scores", {}).get("nop") if current else None,
                "reference_score": measured.get("scores", {}).get("reference") if current else None,
                "reference_mode": task["request"]["reference_mode"],
                "reference_submission_count": measured.get("reference_submission_count") if current else None,
                "reference_repeat": measured.get("reference_repeat") if current else None,
                "completed_model_trials": len(model_scores),
                "mean_best_score": math.fsum(model_scores) / len(model_scores) if model_scores else None,
                "scoring": task["request"]["scoring"],
            })
            for run in task["runs"]:
                trial_rows.append({"task_name": task["name"], **run})
        files["metadata/results.json"] = sforge_files._json_bytes({"schema_version": 1, "tasks": results})
        fields = ["task_name", "trial_id", "model", "agent", "effort", "score", "run_wall_seconds", "step", "agent_submissions"]
        table = io.StringIO(newline="")
        writer = csv.DictWriter(table, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(trial_rows)
        files["metadata/trial_metrics.csv"] = table.getvalue().encode()
        files["metadata/file_modes.json"] = sforge_files._json_bytes({
            "schema_version": 1, "task_files": {name: oct(mode) for name, mode in modes.items()},
            "task_directories": {name: oct(mode) for name, mode in directories.items()},
        })
        report = _qc(tasks, draft)
        if not sforge_files._owner_secret_check(report) or not all(sforge_files._owner_secret_check(source) for source in files.values()):
            raise common.PipelineError("Harbor export contains credential-like content")
        hashes = {name: common.sha256_file(source) if isinstance(source, Path) else hashlib.sha256(source).hexdigest()
                  for name, source in files.items()}
        files["metadata/sha256.json"] = sforge_files._json_bytes({"schema_version": 1, "files": hashes})
        stage = Path(tempfile.mkdtemp(prefix=".edgebench-harbor-", dir=output.parent))
        for name in directories:
            (stage / name).mkdir(parents=True, exist_ok=True)
        for name, source in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(source, Path):
                shutil.copy2(source, target)
            else:
                target.write_bytes(source)
            target.chmod(modes.get(name, 0o644))
            if name in hashes and common.sha256_file(target) != hashes[name]:
                raise common.PipelineError("source changed during Harbor export")
        for name, mode in directories.items():
            (stage / name).chmod(mode)
        for task in tasks:
            if topify_validate.task_sha256(stage / "tasks" / task["name"]) != task["audit"]["task_sha256"]:
                raise common.PipelineError("exported Harbor task differs from frozen source")
        output.mkdir()  # Exclusive claim: never delete another writer's folder.
        created = True
        shutil.copytree(stage, output, dirs_exist_ok=True)
        if not all(common.sha256_file(output / name) == sha for name, sha in hashes.items()):
            raise common.PipelineError("published Harbor file hashes do not match staging")
        with report_file.open("xb") as handle:
            report_created = True
            handle.write(report)
        return {"status": "draft" if draft else "local_complete", "format": "Harbor",
                "task_count": len(tasks), "output": str(output), "quality_report": str(report_file),
                "formal_eligible": not draft, "platform_accepted": False,
                "inventory_sha256": common.sha256_file(output / "metadata/sha256.json")}, 0
    except (common.PipelineError, OSError, ValueError, TypeError, KeyError) as exc:
        if report_created:
            report_file.unlink(missing_ok=True)
        if created:
            shutil.rmtree(output)
        return {"status": "incomplete", "format": "Harbor", "reason": str(exc),
                "formal_eligible": False, "platform_accepted": False}, 2
    finally:
        if stage is not None:
            shutil.rmtree(stage)
