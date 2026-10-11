#!/usr/bin/env python3
"""Read-only Harbor delivery audit; SForge is a scored rollout adapter only."""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
import statistics

import topify_common as common
import topify_completion
import topify_sforge
import topify_sforge_acceptance
import topify_profile
import topify_validate


NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def customer_name(task: Path) -> str:
    config = topify_validate._load_toml(task / "task.toml")
    name = config.get("task", {}).get("name", config.get("name"))
    if not isinstance(name, str):
        raise common.PipelineError("Harbor task.toml needs a declared name")
    parts = name.split("/")
    if not 1 <= len(parts) <= 2 or not all(NAME.fullmatch(part) for part in parts):
        raise common.PipelineError("Harbor name must be name or org/name in snake_case or kebab-case")
    return parts[-1]


def _files(folder: Path) -> dict[str, str]:
    records = topify_validate.task_records(folder)
    if not records or any(item["type"] not in {"directory", "file"} for item in records):
        raise common.PipelineError("missing or unsafe shared task context")
    return {item["path"]: item["sha256"] for item in records if item["type"] == "file"}


def _context_sha256(folder: Path) -> str:
    records = topify_validate.task_records(folder)
    if not records or any(item["type"] not in {"file", "directory"} for item in records):
        raise common.PipelineError("unsafe runtime context")
    return common.canonical_sha256([
        {**record, "mode": (folder / record["path"]).stat().st_mode & 0o777}
        for record in records
    ])


def _shared_core(root: Path, task_sha: str) -> tuple[str, dict]:
    adapter = topify_completion._latest(root / "evidence/sforge-adapters")
    if adapter.get("status") != "complete" or adapter.get("task_sha256") != task_sha:
        return "incomplete", {"reason": "No current SForge adapter"}
    try:
        details = adapter["adapter"]
        tasks = Path(details["tasks_dir"])
        relative = tasks.relative_to(root / "evidence/sforge-adapters")
        if (len(relative.parts) != 2 or relative.parts[1] != "tasks"
                or not re.fullmatch(r"attempt-[0-9]{4,}", relative.parts[0])
                or tasks.is_symlink() or tasks.parent.is_symlink()):
            raise common.PipelineError("adapter is outside its immutable attempt")
        definition_path = tasks / f"{details['task_id']}.json"
        benchmark = tasks / "BENCHMARK.yaml"
        if (common.sha256_file(definition_path) != details["task_definition_sha256"]
                or common.sha256_file(benchmark) != details["benchmark_sha256"]):
            raise common.PipelineError("SForge definition changed after preparation")
        definition = common.load_json(definition_path)
        request = common.load_json(root / "workspace.json")["request"]
        info = definition.get("metadata", {})
        if (definition.get("task_id") != details["task_id"]
                or info.get("authoring_task_sha256") != task_sha
                or info.get("scoring") != {"reward_type": "bounded_continuous", "score_direction": "maximize", **request["scoring"]}
                or info.get("resources") != request["resources"]
                or definition.get("submit_paths") != [topify_validate.artifact_relative(root / "task")]):
            raise common.PipelineError("SForge definition is not bound to this Harbor snapshot")
        wrapper = tasks / "contexts/judge/sforge_eval.py"
        if (wrapper.is_symlink() or not wrapper.is_file()
                or wrapper.read_text(encoding="utf-8") != topify_sforge.WRAPPER):
            raise common.PipelineError("SForge score mapping wrapper changed")
        expected_work = _files(root / "task/environment/workspace")
        actual_work = _files(tasks / "contexts/work/workspace")
        expected_judge = _files(root / "task/tests")
        actual_judge = _files(tasks / "contexts/judge/tests")
        if (expected_work != actual_work
                or any(actual_judge.get(key) != value for key, value in expected_judge.items())
                or set(actual_judge) - set(expected_judge) - {"sforge_eval.py"}):
            raise common.PipelineError("Harbor/SForge scorer or task-input bytes differ")
        # Work README copies the original English instruction; runtime setup
        # equivalence is a separate check, not inferred from these copies.
        if common.sha256_file(root / "task/instruction.md") != common.sha256_file(tasks / "contexts/work/README.md"):
            raise common.PipelineError("Harbor/SForge English instruction bytes differ")
        return "complete", {
            "sforge_task_sha256": common.sha256_file(definition_path),
            "benchmark_sha256": common.sha256_file(benchmark),
            "work_input_sha256": common.canonical_sha256(expected_work),
            "judge_input_sha256": common.canonical_sha256(expected_judge),
            "harbor_agent_context_sha256": _context_sha256(root / "task/environment"),
            "harbor_verifier_context_sha256": _context_sha256(root / "task/tests"),
            "sforge_work_context_sha256": _context_sha256(tasks / "contexts/work"),
            "sforge_judge_context_sha256": _context_sha256(tasks / "contexts/judge"),
        }
    except (OSError, KeyError, TypeError, ValueError, common.PipelineError):
        return "failed", {"reason": "Frozen adapter, shared scorer or input identity mismatch"}


def _receipt(root: Path, kind: str, task_sha: str, fields: dict,
             *, count: int = 1) -> bool:
    value = topify_completion._latest(root / "evidence" / kind)
    files = value.get("process_evidence")
    return bool(value.get("status") == "complete" and value.get("task_sha256") == task_sha
                and value.get("reviewer_role") == "main-agent"
                and isinstance(value.get("reviewer_id"), str) and value["reviewer_id"].strip()
                and all(value.get(key) == expected for key, expected in fields.items())
                and isinstance(files, list) and len(files) >= count
                and all(topify_sforge_acceptance._verified_file(root, entry) for entry in files))


def record_evidence(workspace: str | Path, kind: str, input_path: str | Path) -> tuple[dict, int]:
    """Bind an independently reviewed technical probe to the exact Harbor task.

    This records checked process files; it does not execute the network/rebuild
    probes or establish their conclusions from a Boolean without main review.
    """
    root, metadata = common.load_workspace(workspace)
    task_sha = topify_validate.task_sha256(root / "task")
    if kind == "runtime":
        folder = "harbor-runtime"
        fields = {"agent_offline": True, "verifier_offline": True,
                  "fresh_verifier_per_submission": True, "environment_scrubbed": True}
        minimum = 3
    elif kind == "reproducibility":
        folder = "harbor-reproducibility"
        fields = {"platforms": sorted(common.ARCHITECTURES), "dependencies_pinned": True,
                  "contexts": {role: topify_validate.task_sha256(root / "task" / path)
                               for role, path in (("agent", "environment"), ("verifier", "tests"))}}
        minimum = 2
    elif kind == "equivalence":
        folder = "harbor-sforge-equivalence"
        state, fields = _shared_core(root, task_sha)
        if state != "complete":
            raise common.PipelineError("runtime equivalence needs current shared-core byte parity first")
        minimum = 2
    elif kind == "determinism":
        folder = "harbor-determinism"
        headroom = abs(float(metadata["request"]["scoring"]["upper"]) - float(metadata["request"]["scoring"]["baseline"]))
        artifact = topify_validate.artifact_relative(root / "task")
        native = {arch: _native(root, task_sha, artifact, arch, headroom) for arch in sorted(common.ARCHITECTURES)}
        fields = _noise_fields(native, headroom)
        if fields is None:
            raise common.PipelineError("noise review requires checked native fixed-snapshot measurements on both hosts")
        minimum = 2
    else:
        raise common.PipelineError("unknown Harbor evidence kind")
    value = common.load_json(input_path)
    base = {"schema_version", "task_sha256", "reviewer_role", "reviewer_id", "reviewed_at", "process_evidence"}
    if (set(value) != base | set(fields) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("task_sha256") != task_sha
            or value.get("reviewer_role") != "main-agent"
            or not isinstance(value.get("reviewer_id"), str) or not value["reviewer_id"].strip()
            or any(value.get(key) != expected for key, expected in fields.items())):
        raise common.PipelineError("Harbor probe review does not match the current task/contexts")
    try:
        reviewed = datetime.fromisoformat(value["reviewed_at"])
        if reviewed.tzinfo is None:
            raise ValueError("missing timezone")
    except (KeyError, TypeError, ValueError):
        raise common.PipelineError("Harbor review requires a timezone-aware reviewed_at")
    attachments = value.get("process_evidence")
    if (not isinstance(attachments, list) or len(attachments) < minimum
            or not all(topify_sforge_acceptance._verified_file(root, entry) for entry in attachments)
            or len({entry["path"] for entry in attachments}) != len(attachments)):
        raise common.PipelineError("Harbor review needs distinct unchanged hash-bound process files")
    number, attempt = common.next_attempt(root / "evidence" / folder)
    receipt = {**value, "status": "complete", "attempt": number,
               "input_sha256": common.sha256_file(input_path), "recorded_at": common.utc_now()}
    common.write_new_json(attempt / "summary.json", receipt)
    common.write_latest_json(attempt.parent / "latest.json", receipt)
    common.append_event(root, "harbor_technical_review", kind=kind, attempt=number, task_sha256=task_sha)
    return receipt, 0


def _native(root: Path, task_sha: str, artifact: str, architecture: str,
            headroom: float, reference_repeats: int = 5) -> dict:
    folder = root / "evidence/image-measurements" / architecture.replace("/", "-")
    summary = topify_completion._latest(folder)
    if (summary.get("status") != "complete" or summary.get("task_sha256") != task_sha
            or summary.get("requested_architecture") != architecture
            or summary.get("observed_host_architecture") != architecture
            or type(summary.get("attempt")) is not int):
        return {}
    attempt = folder / f"attempt-{summary['attempt']:04d}"
    try:
        if common.load_json(attempt / "summary.json") != summary:
            return {}
        snapshots = {}
        scores = {}
        proof = []
        timing_verified = True
        durations = []
        for role in ("nop", "starter", "interior", *(f"reference-{i}" for i in range(1, reference_repeats + 1))):
            run = attempt / "runs" / role
            process = common.load_json(run / "process.json")
            report = common.load_json(run / "report.json")
            argv = process.get("argv", [])
            snapshot_sha = common.sha256_file(run / "workspace" / artifact)
            if (process.get("exit_code") != 0 or process.get("timed_out") is not False
                    or process.get("architecture") != architecture
                    or process.get("snapshot_sha256") != snapshot_sha
                    or process.get("image") != summary.get("image", {}).get("tag")
                    or "--network" not in argv or argv[argv.index("--network") + 1] != "none"
                    or not common.finite_number(report.get("total_score"))
                    or report.get("valid") is not True):
                return {}
            scores[role] = report["total_score"]
            snapshots[role] = snapshot_sha
            timing_verified = (timing_verified
                               and process.get("measurement_scope") == "snapshot-staging-and-container-grading"
                               and common.finite_number(process.get("elapsed_seconds"))
                               and process["elapsed_seconds"] > 0)
            if common.finite_number(process.get("elapsed_seconds")):
                durations.append(process["elapsed_seconds"])
            proof.append({"process": common.sha256_file(run / "process.json"),
                          "report": common.sha256_file(run / "report.json"), "snapshot": snapshot_sha})
        repeated = [scores[f"reference-{i}"] for i in range(1, 6)]
        span = max(repeated) - min(repeated)
        repeat = summary.get("reference_repeat", {})
        if (summary.get("reference_repeat", {}).get("scores") != repeated
                or len({snapshots[f"reference-{i}"] for i in range(1, 6)}) != 1
                or repeat.get("snapshot_sha256") != snapshots["reference-1"]
                or not common.finite_number(repeat.get("range"))
                or not math.isclose(repeat["range"], span, rel_tol=0, abs_tol=1e-12)
                or not common.finite_number(repeat.get("relative_range"))
                or not math.isclose(repeat["relative_range"], span / headroom, rel_tol=0, abs_tol=1e-12)
                or any(summary.get("scores", {}).get(role) != scores[role]
                       for role in ("nop", "starter", "interior"))):
            return {}
        timing = summary.get("timing", {})
        timing_verified = (timing_verified and len(durations) == 8
                           and common.finite_number(timing.get("cold_start_seconds"))
                           and math.isclose(timing["cold_start_seconds"], durations[0], rel_tol=0, abs_tol=1e-12)
                           and common.finite_number(timing.get("typical_end_to_end_seconds"))
                           and math.isclose(timing["typical_end_to_end_seconds"], statistics.median(durations),
                                            rel_tol=0, abs_tol=1e-12))
        return {"scores": scores, "snapshots": snapshots,
                "reference_repeat": summary["reference_repeat"], "timing": summary.get("timing", {}),
                "timing_verified": timing_verified,
                "evidence_sha256": common.canonical_sha256({"summary": common.sha256_file(attempt / "summary.json"),
                                                            "runs": proof})}
    except (OSError, KeyError, TypeError, IndexError, common.PipelineError):
        return {}


def _noise_fields(native: dict, headroom: float) -> dict | None:
    if not native or not all(native.values()):
        return None
    values = list(native.values())
    first = values[0]
    if len(values) > 1:
        second = values[1]
        if first["snapshots"] != second["snapshots"]:
            return None
        paired = [abs(first["scores"][key] - second["scores"][key]) for key in first["scores"]]
    else:
        paired = []
    reference = [score for value in values for score in value["reference_repeat"]["scores"]]
    maximum_spread = max([*paired, max(reference) - min(reference)])
    return {"noise_acceptable": True, "headroom": headroom,
            "maximum_observed_spread": maximum_spread, "relative_spread": maximum_spread / headroom,
            "native_measurements_sha256": common.canonical_sha256(
                {arch: value["evidence_sha256"] for arch, value in native.items()})}


def audit_task(workspace: str | Path) -> dict:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha = topify_validate.task_sha256(task)
    request = metadata["request"]
    profile_receipt = topify_profile.current(root, task_sha)
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    minimum_budget = topify_profile.threshold(profile, "interaction_budget_seconds_minimum", 10800)
    reference_repeats = topify_profile.threshold(profile, "reference_repeats", 5)
    required_architectures = set(profile["thresholds"]["architectures_required"])
    validation = topify_validate.evaluate(root)  # Current bytes, not a stale Boolean.
    rows = []

    def add(key: str, status: str | bool, detail: str, evidence=None):
        if isinstance(status, bool):
            status = "complete" if status else "incomplete"
        rows.append({"id": key, "status": status, "detail": detail, "evidence": evidence})

    add("harbor.static_contract", validation["verdict"] == "complete",
        "Fresh Harbor structure, sibling contexts, network declarations and scoring contract check.",
        validation["checks"])
    try:
        customer_name(task)
        name_ok = True
    except common.PipelineError:
        name_ok = False
    add("harbor.task_name", name_ok, "Export directory matches the leaf of the declared name; Meta V3 permits snake/kebab case.")
    add("harbor.budget", request["interaction_budget_seconds"] >= minimum_budget,
        f"{profile['benchmark']} profile requires an interaction budget of at least {minimum_budget} seconds.")
    add("harbor.production_family", request["plugin"] != "mini_continuous",
        "Miniature regression fixtures are not production tasks.")
    # Reuse relevant scoring/calibration audit rows; omit SForge-only delivery
    # constraints and replace them with checks on the actual Harbor contexts.
    retained = {
        "sforge.generic_baseline_screen", "sforge.raw_to_normalized_scale",
        "sforge.local_feedback_and_repeat", "sforge.score_parity",
        "sforge.nop_per_task", "sforge.batch_nop", "sforge.reference_or_open_solution",
        "sforge.multiarch_build_and_base", "sforge.resource_agreement",
        "sforge.gpu_envelope", "sforge.data_hashes", "sforge.frontier_three_runs", "sforge.main_review",
    }
    rows.extend(item for item in topify_sforge_acceptance.audit_task(root)["rows"]
                if item["id"] in retained)
    state, bridge = _shared_core(root, task_sha)
    add("harbor.sforge_shared_core", state,
        "SForge must copy this frozen Harbor instruction, public inputs and complete scorer inputs byte-for-byte.", bridge)
    add("harbor.sforge_runtime_equivalence", state == "complete" and _receipt(
        root, "harbor-sforge-equivalence", task_sha, bridge, count=2),
        "Source parity alone does not prove equivalent Work/Judge dependencies, constraints and runtime scoring.")
    add("harbor.runtime_isolation", _receipt(root, "harbor-runtime", task_sha, {
        "agent_offline": True, "verifier_offline": True,
        "fresh_verifier_per_submission": True, "environment_scrubbed": True,
    }, count=3), "Executed Harbor Work/verifier no-network and stateless grading; separate from removed Q8 review.")
    add("harbor.reproducible_contexts", _receipt(root, "harbor-reproducibility", task_sha, {
        "platforms": sorted(required_architectures), "dependencies_pinned": True,
        "contexts": {role: topify_validate.task_sha256(task / path)
                     for role, path in (("agent", "environment"), ("verifier", "tests"))},
    }, count=2), "Rebuild delivered Harbor contexts from fixed dependencies; SForge image rebuilds are not substitutes.")
    try:
        artifact = topify_validate.artifact_relative(task)
        headroom = abs(float(request["scoring"]["upper"]) - float(request["scoring"]["baseline"]))
        native = {arch: _native(root, task_sha, artifact, arch, headroom, reference_repeats)
                  for arch in sorted(required_architectures)}
    except common.PipelineError:
        native = {}
    noise_fields = _noise_fields(native, headroom) if native else None
    noise_reviewed = noise_fields is not None and _receipt(
        root, "harbor-determinism", task_sha, noise_fields, count=2)
    parity = bool(native) and all(native.values())
    if parity and len(native) > 1:
        first, second = list(native.values())[:2]
        parity = (first["snapshots"] == second["snapshots"]
                  and (first["scores"] == second["scores"] or noise_reviewed))
    add("harbor.native_cross_architecture", parity,
        "Native Harbor final-verifier NOP/interior/reference snapshots and scores must agree; ARM skips rollout and Q1–Q7 review.")
    nop = bool(native) and all(value and value["scores"]["nop"] == request["scoring"]["baseline"]
                              for value in native.values())
    add("harbor.nop_per_task", nop, "Native Harbor NOP must land on the declared floor on each tested architecture.")
    repeats = bool(native) and all(value and len(value["reference_repeat"].get("scores", [])) == reference_repeats
                                  and (value["reference_repeat"]["range"] == 0 or noise_reviewed)
                                  for value in native.values())
    add("harbor.five_reference_scores", repeats,
        "Five fixed snapshots and verified spread/headroom per native architecture; nonzero noise requires independent acceptance review.",
        {"native": {arch: value.get("reference_repeat") for arch, value in native.items()},
         "acceptance_review_fields": noise_fields})
    timed = bool(native) and all(value and value["timing_verified"]
                                and all(common.finite_number(value["timing"].get(key))
                                              and value["timing"][key] > 0
                                              for key in ("cold_start_seconds", "typical_end_to_end_seconds"))
                                for value in native.values())
    add("harbor.score_timing", timed, "Native verifier cold startup and submission-inclusive wall-clock measurements.")
    for key in ("source_declaration", "catalog_dedup", "redistribution_authorization", "sforge_dual_entry", "q8_review"):
        add("owner." + key, "not_required_by_owner", "Excluded by the owner's explicit scope; not a verified pass.")
    missing = [row["id"] for row in rows if row["status"] not in {"complete", "not_required_by_owner"}]
    return {"schema_version": 1, "format": "Harbor", "rollout_harness": "SForge",
            "task_id": metadata["task_id"], "task_sha256": task_sha,
            "status": "incomplete" if missing else "complete", "rows": rows, "missing": missing,
            "platform_accepted": False}
