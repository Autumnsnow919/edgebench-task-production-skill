#!/usr/bin/env python3
"""Read-only DOCX non-format evidence audit for an SForge-delivered task."""

from __future__ import annotations

import math
import json
import re
import shlex
from pathlib import Path

import topify_common as common
import topify_candidate_audit
import topify_build
import topify_completion
import topify_owner_arm
import topify_owner_profile
import topify_profile
import topify_review
import topify_validate


def _row(key: str, passed: bool, detail: str, evidence: object = None) -> dict:
    return {"id": key, "status": "complete" if passed else "incomplete", "detail": detail, "evidence": evidence}


def _latest(root: Path, path: str) -> dict:
    return topify_completion._latest(root / "evidence" / path)


def _safe_json(path: Path) -> dict:
    try:
        return common.load_json(path)
    except common.PipelineError:
        return {}


def _verified_file(root: Path, value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        return False
    relative = value.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return False
    path = root / relative
    try:
        return (not path.is_symlink() and path.resolve(strict=True).is_relative_to(root)
                and common.sha256_file(path) == value.get("sha256"))
    except (common.PipelineError, OSError):
        return False


def _starter_baseline_ok(task: Path, submit_paths: object,
                         parity: dict, baseline: float) -> bool:
    if (not isinstance(submit_paths, list) or len(submit_paths) != 1
            or not isinstance(submit_paths[0], str)
            or Path(submit_paths[0]).is_absolute() or ".." in Path(submit_paths[0]).parts):
        return False
    starter = task / "environment/workspace" / submit_paths[0]
    if not starter.is_file() or starter.is_symlink():
        return False
    digest = common.sha256_file(starter)
    return all(isinstance(parity.get(name), dict)
               and parity[name].get("match") is True
               and parity[name].get("snapshot_sha256") == digest
               and parity[name].get("sforge_score_0_100") == baseline
               for name in ("starter", "nop"))


def _pinned_online_apt(contexts: Path) -> bool:
    """Reject floating apt indexes/packages before accepting rebuild claims."""
    for role in ("work", "judge"):
        try:
            dockerfile = (contexts / role / "Dockerfile").read_text(encoding="utf-8")
        except OSError:
            return False
        active = "\n".join(line for line in dockerfile.splitlines()
                           if not line.lstrip().startswith("#"))
        commands = list(re.finditer(
            r"(?<![\w.-])(?:(?:/[A-Za-z0-9_-]+)*/)?(?:apt-get|apt)(?=\s)[^;&\n]*", active))
        if not commands:
            continue
        context = contexts / role
        lock_path = context / "apt-lock.json"
        checker_path = context / "verify_apt_index.py"
        if (lock_path.is_symlink() or checker_path.is_symlink()
                or not lock_path.is_file() or not checker_path.is_file()):
            return False
        lock = _safe_json(lock_path)
        try:
            checker_matches = (checker_path.read_bytes()
                               == Path(__file__).with_name("topify_apt_index.py").read_bytes())
        except OSError:
            return False
        packages_lock = lock.get("packages")
        indexes = lock.get("package_index_sha256")
        if (not checker_matches or lock.get("schema_version") != 1
                or not isinstance(packages_lock, dict) or not packages_lock
                or not isinstance(indexes, dict)
                or set(indexes) != {"linux/amd64", "linux/arm64"}
                or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                           for value in indexes.values())
                or not re.search(r"(?m)^FROM\s+" + re.escape(str(lock.get("base_reference"))) + r"\s*$", active)
                or "ARG TARGETARCH" not in active
                or "COPY apt-lock.json verify_apt_index.py /tmp/" not in active
                or 'python3 /tmp/verify_apt_index.py "$TARGETARCH"' not in active
                or active.index('python3 /tmp/verify_apt_index.py "$TARGETARCH"') > commands[0].start()):
            return False
        snapshot = re.search(r"snapshot\.debian\.org/archive/(?:debian|debian-security)/\d{8}T\d{6}Z", active)
        if not snapshot or snapshot.group(0) != f"snapshot.debian.org/archive/debian/{lock.get('snapshot')}":
            return False
        source_file = re.search(r">\s*(/[^\s;&]+).*?\bapt-get\b", active, re.S)
        if not source_file or source_file.start() < snapshot.start():
            return False
        actions = set()
        installed = {}
        for match in commands:
            try:
                words = shlex.split(match.group(0))
            except ValueError:
                return False
            found = [(index, word) for index, word in enumerate(words)
                     if word in {"update", "install"}]
            if len(found) != 1:
                return False
            position, action = found[0]
            actions.add(action)
            if any(value not in words or words.index(value) == 0 or words[words.index(value) - 1] != "-o"
                   for value in (f"Dir::Etc::sourcelist={source_file.group(1)}", "Dir::Etc::sourceparts=-")):
                return False
            if action != "install":
                continue
            packages = []
            skip_next = False
            for word in words[position + 1:]:
                if skip_next:
                    skip_next = False
                elif word == "-o":
                    skip_next = True
                elif not word.startswith("-"):
                    packages.append(word)
            if not packages or not all(re.fullmatch(r"[a-zA-Z0-9.+-]+=[a-zA-Z0-9:~.+-]+", word)
                                       for word in packages):
                return False
            installed.update(word.split("=", 1) for word in packages)
        if actions != {"update", "install"}:
            return False
        if installed != packages_lock:
            return False
    return True


def _verified_rebuilds(root: Path, receipt: dict, task_sha: str,
                       contexts_sha256: dict, build_network: str) -> bool:
    reference = receipt.get("build_evidence")
    if not _verified_file(root, reference):
        return False
    evidence = _safe_json(root / reference["path"])
    rows = evidence.get("rebuilds")
    if (evidence.get("schema_version") != 1 or evidence.get("task_sha256") != task_sha
            or evidence.get("contexts_sha256") != contexts_sha256
            or not isinstance(rows, list) or len(rows) != 8):
        return False
    identities = {}
    for entry in rows:
        if not isinstance(entry, dict):
            return False
        role, arch, round_index = (entry.get(key) for key in ("role", "architecture", "round"))
        image_id = entry.get("image_id")
        if (role not in {"work", "judge"} or arch not in common.ARCHITECTURES
                or type(round_index) is not int or round_index not in {1, 2}
                or not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                or (role, arch, round_index) in identities
                or not _verified_file(root, entry.get("process"))):
            return False
        process = _safe_json(root / entry["process"]["path"])
        argv = process.get("argv")
        if (process.get("returncode") != 0 or process.get("image_id") != image_id
                or process.get("observed_host_architecture") != arch
                or not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv)
                or len(argv) < 3 or argv[1:3] != ["buildx", "build"]
                or argv[-1] != str(root / "evidence/sforge-adapters" / receipt["adapter_attempt"]
                                    / "tasks/contexts" / role)):
            return False
        for flag, expected in (("--network", build_network), ("--platform", arch)):
            if flag not in argv or argv.index(flag) + 1 >= len(argv) or argv[argv.index(flag) + 1] != expected:
                return False
        if ("--tag" not in argv or argv.index("--tag") + 1 >= len(argv)
                or not _verified_file(root, entry.get("inspect_process"))
                or not _verified_file(root, entry.get("inspect_stdout"))):
            return False
        inspect = _safe_json(root / entry["inspect_process"]["path"])
        stdout_path = root / entry["inspect_stdout"]["path"]
        if (inspect.get("returncode") != 0
                or inspect.get("argv") != [argv[0], "image", "inspect", argv[argv.index("--tag") + 1]]
                or inspect.get("stdout_sha256") != common.sha256_file(stdout_path)):
            return False
        try:
            inspected = json.loads(stdout_path.read_text(encoding="utf-8"))
            details = inspected[0]
        except (OSError, UnicodeError, ValueError, IndexError, TypeError):
            return False
        if (not isinstance(details, dict) or details.get("Id") != image_id
                or f"{str(details.get('Os')).lower()}/{str(details.get('Architecture')).lower()}" != arch):
            return False
        identities[role, arch, round_index] = image_id
    return (len(identities) == 8 and all(identities[role, arch, 1] == identities[role, arch, 2]
                                         for role in ("work", "judge") for arch in common.ARCHITECTURES))


def _model_replays_match(root: Path, task_sha: str, adapter_id: str,
                         rollout: dict) -> bool:
    """Check model submissions, not just stable reference snapshots."""
    receipt = _latest(root, "sforge-model-replay")
    completed = [run for run in rollout.get("runs", [])
                 if isinstance(run, dict) and run.get("status") == "complete"]
    rows = receipt.get("runs")
    if (receipt.get("status") != "complete" or receipt.get("task_sha256") != task_sha
            or receipt.get("adapter_task_id") != adapter_id
            or not completed or not isinstance(rows, list) or len(rows) != len(completed)
            or {row.get("run_id") for row in rows if isinstance(row, dict)}
            != {run.get("run_id") for run in completed}):
        return False
    by_run = {row["run_id"]: row for row in rows}
    for run in completed:
        entry = by_run[run["run_id"]]
        points = run.get("trajectory")
        if not isinstance(points, list) or not points:
            return False
        original_point = next((point for point in points if isinstance(point, dict)
                               and point.get("round") == entry.get("round")
                               and point.get("score") == run.get("best_score")
                               and point.get("archive_sha256") == entry.get("archive_sha256")), None)
        if original_point is None or not _verified_file(root, entry.get("original_report")):
            return False
        if entry["original_report"]["sha256"] != original_point.get("report_sha256"):
            return False
        original = _safe_json(root / entry["original_report"]["path"])
        compared = (original.get("valid"), original.get("score_0_100"),
                    original.get("metrics"), original.get("checks"))
        if (original.get("score_0_100") != original_point["score"]
                or original.get("valid") is not original_point.get("valid")
                or not isinstance(original.get("metrics"), dict)
                or not isinstance(original.get("checks"), list)):
            return False
        replays = entry.get("replays")
        if not isinstance(replays, dict) or set(replays) != common.ARCHITECTURES:
            return False
        for architecture, item in replays.items():
            if not isinstance(item, dict) or any(not _verified_file(root, item.get(key))
                                                  for key in ("snapshot", "report", "process")):
                return False
            if item["snapshot"]["sha256"] != entry["archive_sha256"]:
                return False
            report = _safe_json(root / item["report"]["path"])
            process = _safe_json(root / item["process"]["path"])
            argv = process.get("argv")
            if ((report.get("valid"), report.get("score_0_100"),
                 report.get("metrics"), report.get("checks")) != compared
                    or process.get("returncode") != 0
                    or process.get("observed_host_architecture") != architecture
                    or not isinstance(argv, list)
                    or not all(isinstance(argument, str) for argument in argv)
                    or "eval" not in argv or "--archive" not in argv
                    or argv[argv.index("--archive") + 1:] == []
                    or argv[argv.index("--archive") + 1] != str(root / item["snapshot"]["path"])
                    or "--task" not in argv or argv[argv.index("--task") + 1:] == []
                    or argv[argv.index("--task") + 1] != adapter_id):
                return False
    return True


def record_provenance(workspace: str | Path, input_path: str | Path) -> tuple[dict, int]:
    """Bind owner-reviewed rights and six catalog comparisons to source bytes."""
    root, metadata = common.load_workspace(workspace)
    task_sha = topify_validate.task_sha256(root / "task")
    source = common.load_json(input_path)
    required = {
        "schema_version", "task_sha256", "source", "reviewer_role", "reviewer_id",
        "written_rights", "catalog_evidence",
    }
    if set(source) != required or type(source.get("schema_version")) is not int or source["schema_version"] != 1:
        raise common.PipelineError(f"provenance input keys must be exactly {sorted(required)}")
    if source["task_sha256"] != task_sha or source["source"] != metadata["request"]["source"]:
        raise common.PipelineError("provenance must match current task and declared source")
    if source["reviewer_role"] != "main-agent" or not isinstance(source["reviewer_id"], str) or not source["reviewer_id"].strip():
        raise common.PipelineError("provenance needs an identified main-agent reviewer")
    required_catalogs = {
        "EdgeBench", "Topify-retained", "MLS-Bench", "Terminal-Bench", "GDPval", "local-candidates",
    }
    comparisons = source["catalog_evidence"]
    if not isinstance(comparisons, dict) or set(comparisons) != required_catalogs:
        raise common.PipelineError("provenance must compare all six public, retained and local catalogs")
    attachments = [source["written_rights"]]
    for catalog in sorted(required_catalogs):
        value = comparisons[catalog]
        if not isinstance(value, dict) or set(value) != {"status", "evidence"} or value["status"] != "clear":
            raise common.PipelineError(f"unresolved dedup comparison: {catalog}")
        attachments.append(value["evidence"])
    for value in attachments:
        if (not _verified_file(root, value)
                or not str(value["path"]).startswith("evidence/source-docs/")):
            raise common.PipelineError("provenance requires actual hashed rights/dedup source-docs inside the workspace")
    number, attempt = common.next_attempt(root / "evidence/provenance")
    report = {
        **source,
        "attempt": number,
        "status": "complete",
        "input_sha256": common.sha256_file(input_path),
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "provenance_review_recorded", attempt=number)
    return report, 0


def audit_task(workspace: str | Path, *, manifest: dict | None = None) -> dict:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha = topify_validate.task_sha256(task)
    request = metadata["request"]
    profile_receipt = topify_profile.current(root, task_sha)
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    minimum_budget = topify_profile.threshold(profile, "interaction_budget_seconds_minimum", 10800)
    minimum_trials = topify_profile.threshold(profile, "frontier_trials_minimum", 3)
    reference_repeats = topify_profile.threshold(profile, "reference_repeats", 5)
    required_architectures = set(profile["thresholds"]["architectures_required"])
    adapter = _latest(root, "sforge-adapters")
    adapter_ok = adapter.get("status") == "complete" and adapter.get("task_sha256") == task_sha
    definition = {}
    tasks_dir = None
    if adapter_ok and isinstance(adapter.get("adapter"), dict):
        pointer = adapter["adapter"].get("tasks_dir")
        task_id = adapter["adapter"].get("task_id")
        if isinstance(pointer, str) and isinstance(task_id, str):
            tasks_dir = Path(pointer)
            definition = _safe_json(tasks_dir / f"{task_id}.json")
    rows = []

    def add(key: str, passed: bool, detail: str, evidence: object = None) -> None:
        rows.append(_row(key, bool(passed), detail, evidence))

    add("sforge.category", definition.get("category") == request["category"],
        "The delivered SForge task must declare one of the six selected capability categories.")
    add("sforge.minimum_three_hour_budget", request["interaction_budget_seconds"] >= minimum_budget,
        f"{profile['benchmark']} profile requires an interaction budget of at least {minimum_budget} seconds.")
    add("sforge.production_task_family", request.get("plugin") != "mini_continuous",
        "The miniature continuous-allocation plugin is a regression fixture, not a sourced EdgeBench production task.")
    candidate = topify_candidate_audit.current_diagnostic(root, task_sha)
    add("sforge.generic_baseline_screen", candidate.get("status") == "review-needed",
        "A current receipt-backed cheap generic solver diagnostic must leave unresolved headroom; saturated or unmeasured candidates cannot be delivered.")
    contexts_ok = (
        adapter_ok and tasks_dir is not None
        and all((tasks_dir / "contexts" / name / "Dockerfile").is_file() for name in ("work", "judge"))
        and (tasks_dir / "contexts/work").parent == (tasks_dir / "contexts/judge").parent
        and (tasks_dir / "BENCHMARK.yaml").is_file()
        and isinstance(adapter.get("adapter", {}).get("contexts_sha256"), dict)
        and set(adapter["adapter"]["contexts_sha256"]) == {"work", "judge"}
        and all(adapter["adapter"]["contexts_sha256"][role] == topify_validate.context_sha256(
            tasks_dir / "contexts" / role) for role in ("work", "judge"))
    )
    add("sforge.separate_contexts", contexts_ok,
        "Independent Work/Judge contexts, both Dockerfiles and BENCHMARK.yaml must match the scored adapter bytes.")
    runtime = _latest(root, "sforge-runtime")
    process_evidence = runtime.get("process_evidence")
    runtime_files_ok = (isinstance(process_evidence, list) and len(process_evidence) >= 3
                        and all(_verified_file(root, entry) for entry in process_evidence))
    add("sforge.runtime_isolation", runtime.get("status") == "complete"
        and runtime.get("task_sha256") == task_sha
        and runtime.get("work_offline") is True
        and runtime.get("judge_offline") is True
        and runtime.get("fresh_judge_per_submission") is True
        and runtime_files_ok,
        "The delivered SForge images need executed work/judge no-network and fresh one-shot judge evidence.")
    static = _safe_json(root / "evidence/harbor_validation.json")
    static_ok = static.get("verdict") == "complete" and static.get("task_sha256") == task_sha
    scoring = definition.get("metadata", {}).get("scoring", {}) if isinstance(definition.get("metadata"), dict) else {}
    scoring_ok = (
        static_ok and definition.get("internet") is False
        and definition.get("judge", {}).get("parser") == "structured_json"
        and scoring == {"reward_type": "bounded_continuous", "score_direction": "maximize", **request["scoring"]}
    )
    add("sforge.score_and_offline_contract", scoring_ok,
        "Task scoring metadata, normalized direction, structured parser, and offline declaration must match the current task.")
    add("sforge.raw_to_normalized_scale", request["scoring"]["direction"] == "maximize",
        "Raw minimization is declared but the present NOP/reference ladder compares raw anchors to a 0–100 score; it needs an end-to-end proven mapping before delivery.")
    try:
        declared_snapshot = [topify_validate.artifact_relative(task)]
    except common.PipelineError:
        declared_snapshot = None
    add("sforge.snapshot_submission", static_ok
        and definition.get("submit_paths") == declared_snapshot,
        "SForge must submit the one declared workspace file, independently of live process state.")

    measured = _latest(root, "measurements")
    required = {"measure.nop_script", "measure.process_observation", "measure.nop", "measure.interior_ladder", "measure.reference_repeat", "measure.timing"}
    if request["reference_mode"] == "reference":
        required.add("measure.reference_script")
    checks = {item.get("id"): item.get("status") for item in measured.get("checks", []) if isinstance(item, dict)}
    local_ok = measured.get("task_sha256") == task_sha and all(checks.get(key) == "complete" for key in required)
    add("sforge.local_feedback_and_repeat", local_ok,
        "Executed NOP, local feedback, interior ladder, reference script where applicable, and five fixed-snapshot scores are needed.",
        measured.get("attempt"))

    parity = adapter.get("parity", []) if adapter_ok else []
    parity_map = {item.get("name"): item for item in parity if isinstance(item, dict)}
    sforge_parity_ok = (
        adapter_ok and {"nop", "starter", "interior", "strong"}.issubset(parity_map)
        and all(item.get("match") is True for item in parity_map.values())
    )
    add("sforge.score_parity", sforge_parity_ok,
        "Four actual SForge judge snapshots must agree with the internal one-shot scorer.")
    add("sforge.original_starter_nop", sforge_parity_ok and _starter_baseline_ok(
        task, definition.get("submit_paths"), parity_map, float(request["scoring"]["baseline"])),
        "NOP and unchanged model-visible starter must be the identical snapshot and score the declared baseline.")
    repeat = adapter.get("sforge_reference_repeat", {}) if adapter_ok else {}
    scores = repeat.get("scores", []) if isinstance(repeat, dict) else []
    repeat_ok = (
        isinstance(scores, list) and len(scores) >= reference_repeats
        and all(common.finite_number(item) for item in scores)
        and repeat.get("snapshot_sha256") == parity_map.get("strong", {}).get("snapshot_sha256")
        and common.finite_number(repeat.get("range"))
        and repeat["range"] == max(scores) - min(scores)
        and common.finite_number(repeat.get("relative_range"))
        and math.isclose(repeat["relative_range"], repeat["range"] / abs(float(request["scoring"]["upper"]) - float(request["scoring"]["baseline"])), abs_tol=1e-12)
    )
    if repeat_ok and repeat["range"] > 0:
        noise = _latest(root, "sforge-noise-review")
        files = noise.get("process_evidence")
        repeat_ok = (
            noise.get("status") == "complete" and noise.get("accepted") is True
            and noise.get("task_sha256") == task_sha
            and noise.get("reviewer_role") == "main-agent"
            and isinstance(noise.get("reviewer_id"), str) and bool(noise["reviewer_id"].strip())
            and isinstance(noise.get("rationale"), str) and bool(noise["rationale"].strip())
            and noise.get("scores") == scores and noise.get("range") == repeat["range"]
            and common.finite_number(noise.get("relative_range"))
            and math.isclose(noise["relative_range"], repeat["relative_range"], abs_tol=1e-12)
            and isinstance(files, list) and bool(files)
            and all(_verified_file(root, entry) for entry in files)
        )
    add("sforge.five_reference_scores", repeat_ok,
        "Five same-snapshot SForge judge scores and spread/headroom are required; nonzero spread needs task-bound reviewer acceptance.",
        repeat if repeat_ok else None)
    timing = adapter.get("sforge_timing", {}) if adapter_ok else {}
    timing_ok = isinstance(timing, dict) and all(
        common.finite_number(timing.get(key)) and timing[key] > 0
        for key in ("cold_start_end_to_end_seconds", "typical_end_to_end_seconds")
    )
    add("sforge.cold_start_and_score_time", timing_ok,
        "Record SForge cold startup and startup/transfer-inclusive typical end-to-end scoring time.")

    attempts = []
    for candidate in sorted((root / "evidence/sforge-adapters").glob("attempt-*/summary.json")):
        report = _safe_json(candidate)
        if report.get("status") == "complete" and report.get("task_sha256") == task_sha:
            attempts.append(report)
    native = {}
    for report in attempts:
        architecture = report.get("architecture")
        if architecture not in common.ARCHITECTURES or architecture in native:
            continue
        report_repeat = report.get("sforge_reference_repeat")
        if not isinstance(report_repeat, dict):
            continue
        raw_parity = report.get("parity")
        if not isinstance(raw_parity, list):
            continue
        report_parity = {item.get("name"): item for item in raw_parity if isinstance(item, dict)}
        raw_processes = report.get("processes")
        if not isinstance(raw_processes, list):
            continue
        process_roles = {
            process.get("role")
            for process in raw_processes if isinstance(process, dict)
            and process.get("returncode") == 0
        }
        expected_roles = {
            "work", "judge", "parity-nop", "parity-starter", "parity-interior", "parity-strong",
            *(f"strong-repeat-{index}" for index in range(2, reference_repeats + 1)),
        }
        if (len(report_repeat.get("scores", [])) >= reference_repeats
                and all(common.finite_number(value) for value in report_repeat["scores"])
                and common.finite_number(report_repeat.get("relative_range"))
                and expected_roles.issubset(process_roles)
                and {"nop", "strong"}.issubset(report_parity)
                and all(report_parity[key].get("match") is True for key in ("nop", "strong"))):
            native[architecture] = {
                "nop": report_parity["nop"], "strong": report_parity["strong"],
                "attempt": report.get("attempt"),
            }
    arch_ok = set(native) >= required_architectures
    if arch_ok:
        first, second = [native[key] for key in sorted(native)]
        arch_ok = all(
            first[key].get(field) == second[key].get(field)
            for key in ("nop", "strong")
            for field in ("snapshot_sha256", "sforge_score_0_100")
        )
    add("sforge.native_cross_architecture", arch_ok,
        "The delivered SForge judge must score identical fixed snapshots natively on amd64 and arm64.",
        {key: item["attempt"] for key, item in native.items()})

    baseline = float(request["scoring"]["baseline"])
    nop_path = task / "nop/nop.sh"
    nop_ok = nop_path.is_file() and not nop_path.is_symlink() and sforge_parity_ok and parity_map["nop"].get("sforge_score_0_100") == baseline
    add("sforge.nop_per_task", nop_ok,
        "Deliver the genuine NOP script and observe its lower-bound score through SForge.")
    batch = _latest(root, "sforge-batch-nop")
    batch_path = batch.get("batch_report_path")
    batch_valid = False
    if isinstance(batch_path, str) and Path(batch_path).is_absolute():
        try:
            batch_document = common.load_json(batch_path)
            batch_valid = (
                common.sha256_file(batch_path) == batch.get("batch_report_sha256")
                and batch_document.get("status") == "complete"
                and batch_document.get("task_sha256s") == batch.get("task_sha256s")
                and any(item.get("task_sha256") == task_sha and item.get("status") == "complete"
                        for item in batch_document.get("results", []) if isinstance(item, dict))
            )
        except (common.PipelineError, OSError):
            batch_valid = False
    add("sforge.batch_nop", batch_valid and batch.get("status") == "complete"
        and batch.get("task_sha256") == task_sha
        and task_sha in batch.get("task_sha256s", [])
        and batch.get("sforge_nop_score") == baseline
        and batch.get("nop_script_sha256") == (common.sha256_file(nop_path) if nop_path.is_file() and not nop_path.is_symlink() else None),
        "Every selected task needs a batch-bound, executed SForge NOP score receipt; an internal Harbor batch is not equivalent.")
    reference = task / "solution/solve.sh"
    mode_ok = (
        definition.get("metadata", {}).get("open_solution") is (request["reference_mode"] == "open-solution")
        and ((reference.is_file() and not reference.is_symlink() and local_ok)
             if request["reference_mode"] == "reference" else not reference.exists())
    )
    add("sforge.reference_or_open_solution", mode_ok,
        "Ship an executable reference and its score/count, or explicitly mark open-solution without a fake reference.")

    build = _latest(root, "builds")
    build_ok = build.get("status") == "complete" and build.get("task_sha256") == task_sha
    base_ok = build_ok and bool(build.get("base_images")) and all(
        isinstance(item, dict) and set(item.get("platforms", [])) >= common.ARCHITECTURES
        for item in build["base_images"]
    )
    add("sforge.multiarch_build_and_base", base_ok,
        "Both contexts need multi-platform build evidence and bases pinned to dual-architecture manifest lists.")
    reproduction = _latest(root, "sforge-reproducibility")
    reproducible = (
        contexts_ok and reproduction.get("status") == "complete"
        and _pinned_online_apt(tasks_dir / "contexts")
        and reproduction.get("task_sha256") == task_sha
        and reproduction.get("platforms") == sorted(common.ARCHITECTURES)
        and reproduction.get("dependencies_pinned") is True
        and _verified_rebuilds(root, {**reproduction, "adapter_attempt": tasks_dir.parent.name},
                               task_sha, adapter["adapter"]["contexts_sha256"],
                               topify_build.declared_build_network(task))
    )
    add("sforge.reproducible_contexts", reproducible,
        "Online builds require pinned package indexes/versions and task-bound independently reviewed rebuild evidence; offline images or wheels are not required.")
    resources = _latest(root, "resource-agreement")
    add("sforge.resource_agreement", resources.get("status") == "complete"
        and resources.get("task_sha256") == task_sha
        and resources.get("resources") == request["resources"]
        and definition.get("metadata", {}).get("resources") == request["resources"],
        "External resource/GPU agreement for the selected task version is required.")
    gpu = request["resources"]["gpu_types"]
    gpu_validation = _latest(root, "sforge-gpu")
    add("sforge.gpu_envelope", not gpu or (
        gpu_validation.get("status") == "complete"
        and gpu_validation.get("task_sha256") == task_sha
        and gpu_validation.get("gpu_types") == gpu
        and gpu_validation.get("scoring_uses_gpu") == request["resources"]["scoring_uses_gpu"]
    ), "Tasks with declared accelerators need validation on those actual GPU types and explicit scoring-GPU use.")
    hashed = _latest(root, "data-hashes")
    hashed_ok = hashed.get("status") == "complete" and hashed.get("task_sha256") == task_sha
    if hashed_ok:
        records = hashed.get("files", [])
        try:
            hashed_ok = isinstance(records, list) and records and len(records) == hashed.get("file_count") and all(
                common.sha256_file(task / item["path"]) == item["sha256"] for item in records
            )
        except (common.PipelineError, OSError, KeyError, TypeError):
            hashed_ok = False
    add("sforge.data_hashes", hashed_ok,
        "Each delivered data file needs its own SHA-256 and current task bytes.")

    rows.append({"id": "sforge.provenance_rights_dedup",
                 "status": "not_required_by_owner",
                 "detail": "Source declaration, catalog dedup and redistribution rights are not required by this owner.",
                 "evidence": None})
    rollout = _latest(root, "rollout-collections")
    add("sforge.frontier_three_runs", rollout.get("status") == "complete"
        and rollout.get("task_sha256") == task_sha
        and rollout.get("completed_run_count", 0) >= minimum_trials,
        f"The selected profile requires {minimum_trials} same-config full-budget SForge run(s) and their arithmetic mean.")
    add("sforge.model_snapshot_replay", adapter_ok and rollout.get("status") == "complete"
        and rollout.get("task_sha256") == task_sha
        and _model_replays_match(root, task_sha, adapter["adapter"]["task_id"], rollout),
        "Replay a retained best real-model archive from every complete trial on both native architectures: validity, raw metrics, score and named checks must agree with original reports.")
    add("sforge.main_review", topify_review.review_is_current(root, task_sha),
        "Q1–Q7 and a causal difficulty review must refer to this frozen task version.")
    complete = all(item["status"] in {"complete", "not_required_by_owner"} for item in rows)
    result = {
        "schema_version": 1, "task_id": metadata["task_id"], "task_sha256": task_sha,
        "status": "complete" if complete else "incomplete", "rows": rows,
        "missing": [item["id"] for item in rows if item["status"] not in {"complete", "not_required_by_owner"}],
    }
    if manifest is None or "delivery_profile" not in manifest:
        return result
    if not adapter_ok or tasks_dir is None:
        raise common.PipelineError("owner profile needs a frozen SForge definition")
    definition_path = tasks_dir / f"{adapter['adapter']['task_id']}.json"
    sforge_sha = common.sha256_file(definition_path)
    profile = topify_owner_profile.load_profile(
        manifest, root, task_sha, sforge_sha, metadata["task_id"])
    comparison = topify_owner_arm.verify_comparison(
        root, task_sha, sforge_sha, adapter["adapter"]["task_id"])
    native_provenance = topify_owner_arm.verify_native_prepares(
        root, task_sha, adapter["adapter"]["task_id"])
    return topify_owner_arm.scoped_audit(
        result, profile, comparison,
        native_provenance=native_provenance)
