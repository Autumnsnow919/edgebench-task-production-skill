#!/usr/bin/env python3
"""Check owner-scoped ARM score parity against original report/process bytes."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import topify_common as common
import topify_sforge


REPORT_FIELDS = ("valid", "score", "score_0_100", "passed", "failed",
                 "errors", "total_tests", "pass_rate", "metrics")
SNAPSHOTS = ("nop", "reference", "interior")


def verify_native_prepares(root: Path, task_sha: str, adapter_task_id: str) -> bool:
    """Replay captured SForge process stdout, archives and image-task identities.

    A handwritten summary without the process output bytes cannot qualify the
    owner's ARM score. Live host origin still requires main-review inspection.
    """
    seen = set()
    roles = ("parity-nop", "parity-strong", *(f"strong-repeat-{index}" for index in range(2, 6)))
    for summary in sorted((root / "evidence/sforge-adapters").glob("attempt-*/summary.json")):
        attempt = summary.parent
        if summary.is_symlink() or attempt.is_symlink():
            continue
        try:
            report = json.loads(summary.read_text(encoding="utf-8"))
            arch = report["architecture"]
            if (arch not in {"linux/amd64", "linux/arm64"} or arch in seen
                    or report.get("status") != "complete" or report.get("task_sha256") != task_sha
                    or report.get("observed_host_architecture") != arch):
                continue
            adapter = report["adapter"]
            tasks = Path(adapter["tasks_dir"])
            if (tasks != attempt / "tasks" or tasks.is_symlink()
                    or adapter["task_id"] != adapter_task_id):
                continue
            definition = tasks / f"{adapter_task_id}.json"
            if (definition.is_symlink() or not definition.is_file()
                    or common.sha256_file(definition) != adapter["task_definition_sha256"]):
                continue
            task = json.loads(definition.read_text(encoding="utf-8"))
            if task.get("platform") != arch or task.get("task_id") != adapter_task_id:
                continue
            parity = {item["name"]: item for item in report["parity"]}
            if not {"nop", "strong"}.issubset(parity):
                continue
            repeat = report["sforge_reference_repeat"]["scores"]
            if len(repeat) != 5 or repeat[0] != parity["strong"]["sforge_score_0_100"]:
                continue
            processes = {item.get("role"): item for item in report["processes"]
                         if isinstance(item, dict)}
            for role in roles:
                process = processes[role]
                stdout = attempt / "sforge-evals" / f"{role}.stdout.json"
                stderr = attempt / "sforge-evals" / f"{role}.stderr.txt"
                if (stdout.is_symlink() or stderr.is_symlink() or not stdout.is_file()
                        or not stderr.is_file() or process.get("returncode") != 0
                        or common.sha256_file(stdout) != process.get("stdout_sha256")
                        or common.sha256_file(stderr) != process.get("stderr_sha256")):
                    raise ValueError("missing SForge process stdout/stderr")
                argv = process.get("argv")
                name = "nop" if role == "parity-nop" else "strong"
                archive = attempt / "parity-archives" / f"{name}.tar.gz"
                if (not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv)
                        or "eval" not in argv or "--json" not in argv
                        or "--task" not in argv or "--archive" not in argv
                        or "--tasks-dir" not in argv
                        or argv.index("--tasks-dir") + 1 >= len(argv)
                        or argv[argv.index("--tasks-dir") + 1] != str(tasks)
                        or argv.index("--task") + 1 >= len(argv)
                        or argv[argv.index("--task") + 1] != adapter_task_id
                        or argv.index("--archive") + 1 >= len(argv)
                        or argv[argv.index("--archive") + 1] != str(archive)
                        or not any(Path(arg).name == "sforge" for arg in argv)
                        or archive.is_symlink() or not archive.is_file()
                        or common.sha256_file(archive) != parity[name]["archive_sha256"]):
                    raise ValueError("SForge command/archive/task identity differs")
                raw = topify_sforge._extract_json_report(stdout.read_bytes())
                measured_score = raw.get("score_0_100")
                if raw.get("task_id") != adapter_task_id:
                    raise ValueError("SForge stdout task identity differs")
                if role.startswith("parity-") and measured_score != parity[name]["sforge_score_0_100"]:
                    raise ValueError("SForge parity stdout differs from summary")
                if role.startswith("strong-repeat-") and measured_score != repeat[int(role.rsplit("-", 1)[-1]) - 1]:
                    raise ValueError("SForge repeat stdout differs from summary")
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, IndexError,
                json.JSONDecodeError, common.PipelineError):
            continue
        seen.add(arch)
    return seen == {"linux/amd64", "linux/arm64"}


def _verified(root: Path, entry: object) -> Path:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        raise ValueError("missing hash-bound ARM evidence")
    relative = entry["path"]
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts or not relative.startswith("evidence/owner-arm64/")):
        raise ValueError("unsafe ARM evidence path")
    target = root / relative
    if (target.is_symlink() or not target.is_file()
            or not target.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or common.sha256_file(target) != entry["sha256"]):
        raise ValueError("ARM evidence changed or escaped workspace")
    return target


def verify_comparison(root: Path, task_sha: str, sforge_sha: str,
                      adapter_task_id: str) -> dict:
    """A receipt is insufficient without three original same-archive SForge evals."""
    source = root / "evidence/owner-arm-comparison.json"
    if source.is_symlink() or not source.is_file():
        return {"status": "incomplete", "detail": "ARM process receipts absent"}
    try:
        comparison = json.loads(source.read_text(encoding="utf-8"))
        mode = comparison["execution_mode"]
        if (set(comparison) != {"schema_version", "task_sha256", "sforge_task_sha256",
                                 "adapter_task_id", "execution_mode", "images", "parity"}
                or comparison["schema_version"] != 1
                or comparison["task_sha256"] != task_sha
                or comparison["sforge_task_sha256"] != sforge_sha
                or comparison["adapter_task_id"] != adapter_task_id
                or mode != "native_arm64"):
            raise ValueError("ARM receipt is stale or has unknown fields")
        images = json.loads(_verified(root, comparison["images"]).read_text(encoding="utf-8"))
        if (set(images) != {"base", "work", "judge"}
                or not all(images[k].get("Architecture") == "arm64"
                           and images[k].get("Os") == "linux" for k in images)):
            raise ValueError("base/Work/Judge images are not inspected as arm64")
        parity = comparison["parity"]
        if set(parity) != set(SNAPSHOTS):
            raise ValueError("ARM comparison lacks fixed snapshots")
        snapshots = set()
        for name in SNAPSHOTS:
            entry = parity[name]
            if set(entry) != {"snapshot", "amd64_report", "arm64_report",
                              "amd64_process", "arm64_process", "amd64_stdout",
                              "arm64_stdout", "amd64_stderr", "arm64_stderr"}:
                raise ValueError("ARM snapshot evidence incomplete")
            snapshot = _verified(root, entry["snapshot"])
            snapshots.add(entry["snapshot"]["sha256"])
            with tarfile.open(snapshot, "r:gz") as archive:
                if not archive.getmembers() or any(not member.isfile() for member in archive):
                    raise ValueError("scored snapshot is not a file-only archive")
            amd64 = json.loads(_verified(root, entry["amd64_report"]).read_text(encoding="utf-8"))
            arm64 = json.loads(_verified(root, entry["arm64_report"]).read_text(encoding="utf-8"))
            if (not all(key in amd64 and key in arm64 and amd64[key] == arm64[key]
                        for key in REPORT_FIELDS)
                    or amd64.get("task_id") != adapter_task_id
                    or arm64.get("task_id") != adapter_task_id
                    or not common.finite_number(arm64["score_0_100"])):
                raise ValueError("same-snapshot SForge report metrics disagree")
            for prefix, measured_mode, host in (
                ("amd64", "native_amd64", "x86_64"),
                ("arm64", "native_arm64", "aarch64"),
            ):
                process = json.loads(_verified(root, entry[prefix + "_process"]).read_text(encoding="utf-8"))
                command = process.get("command")
                stdout = json.loads(_verified(root, entry[prefix + "_stdout"]).read_text(encoding="utf-8"))
                stderr = _verified(root, entry[prefix + "_stderr"])
                stored_report = amd64 if prefix == "amd64" else arm64
                if (not isinstance(command, list) or not all(isinstance(value, str) for value in command)
                        or "--task" not in command or "--archive" not in command
                        or "--json" not in command):
                    raise ValueError("SForge process lacks task/archive/JSON arguments")
                task_arg = command.index("--task")
                archive_arg = command.index("--archive")
                if (process.get("returncode") != 0
                        or process.get("task_id") != adapter_task_id
                        or process.get("execution_mode") != measured_mode
                        or process.get("observed_host_architecture") != host
                        or process.get("snapshot_sha256") != entry["snapshot"]["sha256"]
                        or process.get("report_sha256") != entry[prefix + "_report"]["sha256"]
                        or process.get("image_inspection_sha256") != comparison["images"]["sha256"]
                        or process.get("stdout_sha256") != entry[prefix + "_stdout"]["sha256"]
                        or process.get("stderr_sha256") != entry[prefix + "_stderr"]["sha256"]
                        or stdout != stored_report or not stderr.is_file()
                        or task_arg + 1 >= len(command) or command[task_arg + 1] != adapter_task_id
                        or archive_arg + 1 >= len(command)
                        or command[archive_arg + 1] != entry["snapshot"]["path"]
                        or "eval" not in command
                        or not any(Path(value).name == "sforge" for value in command
                                   if isinstance(value, str))):
                    raise ValueError("SForge evaluation process is not bound to report/snapshot/images")
        if len(snapshots) != len(SNAPSHOTS):
            raise ValueError("ARM comparisons reused a snapshot")
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError,
            tarfile.TarError, common.PipelineError) as exc:
        return {"status": "incomplete", "detail": str(exc)}
    return {"status": "receipt_consistent", "execution_mode": mode,
            "receipt_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def scoped_audit(base: dict, profile: dict, comparison: dict, *,
                 native_provenance: bool = False) -> dict:
    """Keep default audit bytes intact; make a separate owner-facing matrix."""
    if profile.get("profile_id") != "owner-sample-2026-09-29":
        raise common.PipelineError("unsupported owner profile")
    rows = [dict(item) for item in base["rows"]
            if item["id"] != "sforge.native_build_and_prebuilt"]
    for item in rows:
        if item["id"] == "sforge.provenance_rights_dedup":
            item.update(status="not_required_by_owner",
                        detail="Source declaration, dedup and redistribution confirmation not requested for this sample.")
    for key, detail in (
        ("source_declaration", "Source-description dossier is not requested for this sample."),
        ("four_catalog_dedup", "Four-catalog dedup result is not requested for this sample."),
        ("redistribution_confirmation", "Written redistribution confirmation is not requested for this sample."),
    ):
        rows.append({"id": "sforge." + key, "status": "not_required_by_owner",
                     "detail": detail})
    native_measured = any(item["id"] == "sforge.native_cross_architecture"
                          and item["status"] == "complete" for item in rows)
    comparison_pass = (comparison["status"] == "receipt_consistent"
                       and native_measured and native_provenance)
    rows.append({"id": "sforge.arm64_owner_comparison",
                 "status": "complete" if comparison_pass else "incomplete",
                 "detail": ("Same-snapshot raw reports match; require separately executed native measure-image receipts. "
                            + comparison.get("detail", "Process origin not independently established by these files.")),
                 "evidence": comparison.get("receipt_sha256")})
    missing = [item["id"] for item in rows if item["status"] in {"incomplete", "fail"}]
    return {**base, "owner_profile": profile["profile_id"], "rows": rows,
            "missing": missing, "status": "incomplete" if missing else "complete"}
