#!/usr/bin/env python3
"""Record an evidence-bound main-agent task review before SForge rollout."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

import topify_common as common
import topify_validate


QUALITY_IDS = tuple(f"Q{i}" for i in range(1, 8))
QUALITY_NAMES = (
    "clear-task-definition", "instruction-verifier-alignment",
    "lawful-solvability", "independent-truth", "detect-core-errors",
    "fair-alternatives", "scoring-reliability",
)


def probe_difficulty(
    workspace: str | Path, integrated_path: str | Path,
    ablated_path: str | Path, causal_claim: str,
) -> tuple[dict, int]:
    """Score two lawful snapshots through the actual verifier; preserve receipts."""
    import topify_measure

    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    validation = common.load_json(root / "evidence/harbor_validation.json")
    if validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256:
        raise common.PipelineError("difficulty probe needs current verifier validation")
    if not _text(causal_claim):
        raise common.PipelineError("difficulty probe needs a testable causal claim")
    snapshots = []
    for value in (integrated_path, ablated_path):
        path = Path(value)
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(task.resolve(strict=True)):
            raise common.PipelineError("difficulty snapshots must be regular files in the task workspace")
        snapshots.append((path, common.sha256_file(path)))
    if snapshots[0][1] == snapshots[1][1]:
        raise common.PipelineError("integrated and ablated snapshots are identical")
    attempt_number, attempt = common.next_attempt(root / "evidence/difficulty-probes")
    scores = {}
    for name, (path, sha) in zip(("integrated", "ablated"), snapshots):
        result, observation = topify_measure._run(
            task, path, attempt / name, metadata["request"]["verifier_timeout_seconds"],
        )
        scores[name] = {
            "snapshot_sha256": sha,
            "score": result["total_score"],
            "valid": result.get("valid"),
            "report_sha256": common.sha256_file(attempt / name / "report.json"),
            "process_sha256": common.sha256_file(attempt / name / "process.json"),
            "elapsed_seconds": observation.get("elapsed_seconds"),
        }
    effect = float(scores["integrated"]["score"]) - float(scores["ablated"]["score"])
    complete = all(item["valid"] is True for item in scores.values()) and effect > 0
    report = {
        "schema_version": 1, "attempt": attempt_number,
        "task_sha256": task_sha256,
        "status": "complete" if complete else "revise",
        "causal_claim": causal_claim,
        "snapshots": scores,
        "score_effect": effect,
        "interpretation": "The main reviewer must independently verify what the changed snapshot ablates.",
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    return report, 0 if complete else 2


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _checked_evidence(root: Path, paths: object) -> list[dict]:
    if not isinstance(paths, list) or not paths:
        raise common.PipelineError("review evidence must be a nonempty list of workspace-relative files")
    checked = []
    for value in paths:
        if not _text(value):
            raise common.PipelineError("review evidence path must be nonempty")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or str(path) != value:
            raise common.PipelineError("review evidence must be a confined relative path")
        candidate = root / path
        try:
            if candidate.is_symlink() or not candidate.resolve(strict=True).is_relative_to(root):
                raise common.PipelineError("review evidence escapes task workspace")
            digest = common.sha256_file(candidate)
        except OSError as exc:
            raise common.PipelineError(f"review evidence is unavailable: {value}") from exc
        checked.append({"path": value, "sha256": digest})
    return checked


def review_task(workspace: str | Path, input_path: str | Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    source = common.load_json(input_path)
    required = {
        "schema_version", "task_sha256", "reviewer_role", "reviewer_id",
        "decision", "rationale", "quality", "difficulty",
    }
    if set(source) != required or type(source.get("schema_version")) is not int or source["schema_version"] != 1:
        raise common.PipelineError(f"task review keys must be exactly {sorted(required)}")
    if source["task_sha256"] != task_sha256:
        raise common.PipelineError("task review is not bound to current task bytes")
    if source["reviewer_role"] != "main-agent" or not _text(source["reviewer_id"]):
        raise common.PipelineError("task review must identify the main-agent reviewer")
    if source["decision"] not in {"ready", "revise", "reject"} or not _text(source["rationale"]):
        raise common.PipelineError("task review needs an explicit decision and rationale")
    quality = source["quality"]
    if (not isinstance(quality, dict) or not set(QUALITY_IDS).issubset(quality)
            or set(quality) - set(QUALITY_IDS) - {"Q8"}):
        raise common.PipelineError("task review must cover Q1–Q7 exactly (legacy Q8 is ignored)")
    quality = {key: quality[key] for key in QUALITY_IDS}
    quality_evidence = {}
    for key in QUALITY_IDS:
        item = quality[key]
        if not isinstance(item, dict) or set(item) != {"status", "finding", "evidence"}:
            raise common.PipelineError(f"{key} must contain status, finding, evidence")
        if item["status"] not in {"pass", "fail", "pending"} or not _text(item["finding"]):
            raise common.PipelineError(f"{key} needs a finding and pass/fail/pending status")
        quality_evidence[key] = _checked_evidence(root, item["evidence"])
    difficulty = source["difficulty"]
    if not isinstance(difficulty, dict) or set(difficulty) != {
        "mechanism", "structure", "feedback_loop", "baseline_comparison",
        "causal_probe", "revision_strategy", "evidence",
    }:
        raise common.PipelineError("difficulty review must name mechanism, structure, loop, baseline, probe, revision, evidence")
    if difficulty["structure"] not in {"interleaved-graph", "domain-specific"}:
        raise common.PipelineError("difficulty structure must identify an interleaved graph or a domain-specific mechanism")
    if not all(_text(difficulty.get(key)) for key in (
        "mechanism", "feedback_loop", "baseline_comparison", "causal_probe", "revision_strategy",
    )):
        raise common.PipelineError("difficulty review must explain each required mechanism and probe")
    difficulty_evidence = _checked_evidence(root, difficulty["evidence"])
    ready = source["decision"] == "ready"
    if ready and any(quality[key]["status"] != "pass" for key in QUALITY_IDS):
        raise common.PipelineError("rollout-ready review needs all Q1–Q7 confirmed pass")
    if ready:
        _current_probe(root, task_sha256)
    if ready and not any(
        item["path"] == "evidence/difficulty-probes/latest.json" for item in difficulty_evidence
    ):
        raise common.PipelineError("rollout-ready review needs a current, measured causal difficulty probe")
    if ready:
        pack_summary, pack_code = difficulty_summary(root)
        if (pack_code != 0 or pack_summary.get("status") != "complete"
                or not any(item["path"] == "evidence/difficulty-packs/latest.json"
                           for item in difficulty_evidence)):
            raise common.PipelineError("rollout-ready review needs a current mechanism-bound difficulty pack")
        _current_difficulty_card(root, task_sha256)
        if not any(item["path"] == "evidence/difficulty-cards/latest.json" for item in difficulty_evidence):
            raise common.PipelineError("rollout-ready review needs a current evidence-bound difficulty card")
    validation = common.load_json(root / "evidence/harbor_validation.json")
    if ready and (validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256):
        raise common.PipelineError("rollout-ready review requires current static validation")
    adapter_path = root / "evidence/sforge-adapters/latest.json"
    adapter = common.load_json(adapter_path) if adapter_path.is_file() else {}
    if ready and (adapter.get("status") != "complete" or adapter.get("task_sha256") != task_sha256):
        raise common.PipelineError("rollout-ready review requires current SForge score parity")
    import topify_completion

    measurement = topify_completion._latest(root / "evidence/measurements")
    if ready and measurement.get("task_sha256") != task_sha256:
        raise common.PipelineError("rollout-ready review requires measured score ladder")
    attempt_number, attempt = common.next_attempt(root / "evidence/author-reviews")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": "ready" if ready else source["decision"],
        "task_sha256": task_sha256,
        "task_id": metadata["request"]["task_id"],
        "reviewer_role": source["reviewer_role"],
        "reviewer_id": source["reviewer_id"],
        "rationale": source["rationale"],
        "quality": quality,
        "quality_evidence": quality_evidence,
        "difficulty": difficulty,
        "difficulty_evidence": difficulty_evidence,
        "source_sha256": common.sha256_file(input_path),
        "reviewed_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    common.append_event(root, "task_reviewed", attempt=attempt_number, decision=report["status"])
    return report, 0 if ready else 2


MECHANISM_KINDS = frozenset({
    "objective-decomposition", "dependency-chain", "parallel-branch-join",
    "shared-resource", "conflicting-objective", "state-coupling",
    "identifiability", "generalization", "sequential-interleave", "error-recovery",
})
MECHANISM_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
CARD_CATEGORIES = ("traps", "dependencies", "constraints", "interleavings")


def _card_evidence(root: Path, item: dict, item_id: str) -> list[dict]:
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise common.PipelineError(f"{item_id} needs deterministic text evidence")
    checked = []
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) != {"path", "contains"}:
            raise common.PipelineError(f"{item_id} evidence needs path and contains")
        contains = entry["contains"]
        if not isinstance(contains, str) or len(contains.strip()) < 10:
            raise common.PipelineError(f"{item_id} evidence contains too little text")
        verified = _checked_evidence(root, [entry["path"]])[0]
        text = (root / entry["path"]).read_text(encoding="utf-8")
        if contains not in text:
            raise common.PipelineError(f"{item_id} evidence text is absent from {entry['path']}")
        checked.append(verified)
    return checked


def _card_items(source: dict, category: str) -> list[dict]:
    values = source.get(category)
    if not isinstance(values, list):
        raise common.PipelineError(f"difficulty card needs a {category} list")
    common_required = {"id", "statement", "evidence"}
    specific = {
        "traps": set(),
        "dependencies": {"from", "to"},
        "constraints": {"enforced_by"},
        "interleavings": {"dependency", "constraint"},
    }[category]
    items = []
    for item in values:
        if not isinstance(item, dict) or set(item) != common_required | specific:
            raise common.PipelineError(f"{category} entries have incorrect fields")
        item_id = item["id"]
        if not isinstance(item_id, str) or MECHANISM_ID.fullmatch(item_id) is None:
            raise common.PipelineError(f"{category} id must be a lowercase slug")
        if not _text(item.get("statement")):
            raise common.PipelineError(f"{item_id} needs a concrete statement")
        for key in specific:
            if not _text(item.get(key)):
                raise common.PipelineError(f"{item_id} needs {key}")
        items.append(item)
    return items


def difficulty_card(workspace: str | Path, input_path: str | Path) -> tuple[dict, int]:
    """Count only traps/dependencies/constraints/interleavings bound to exact text."""
    root, _ = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    source = common.load_json(input_path)
    if (not isinstance(source, dict) or set(source) != {"schema_version", "task_sha256", *CARD_CATEGORIES}
            or type(source.get("schema_version")) is not int or source["schema_version"] != 1):
        raise common.PipelineError("difficulty card must contain schema_version, task_sha256, and four evidence lists")
    if source["task_sha256"] != task_sha256:
        raise common.PipelineError("difficulty card is not bound to current task bytes")
    categorized = {category: _card_items(source, category) for category in CARD_CATEGORIES}
    all_items = [item for category in CARD_CATEGORIES for item in categorized[category]]
    ids = [item["id"] for item in all_items]
    if len(ids) != len(set(ids)):
        raise common.PipelineError("difficulty card IDs must be globally unique")
    dependency_ids = {item["id"] for item in categorized["dependencies"]}
    constraint_ids = {item["id"] for item in categorized["constraints"]}
    evidence = {}
    for category in CARD_CATEGORIES:
        for item in categorized[category]:
            evidence[item["id"]] = _card_evidence(root, item, item["id"])
    for item in categorized["interleavings"]:
        if item["dependency"] not in dependency_ids or item["constraint"] not in constraint_ids:
            raise common.PipelineError("interleaving must reference declared dependency and constraint IDs")
    counts = {category: len(categorized[category]) for category in CARD_CATEGORIES}
    denominator = counts["dependencies"] * counts["constraints"]
    attempt_number, attempt = common.next_attempt(root / "evidence/difficulty-cards")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "task_sha256": task_sha256,
        "status": "complete",
        "counts": counts,
        "interleaving_density": counts["interleavings"] / denominator if denominator else 0.0,
        "items": categorized,
        "item_evidence": evidence,
        "input_sha256": common.sha256_file(input_path),
        "recorded_at": common.utc_now(),
        "note": "Counts are deterministic text-bound structure diagnostics, not a quality score.",
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    return report, 0


def _current_difficulty_card(root: Path, task_sha256: str) -> dict:
    path = root / "evidence/difficulty-cards/latest.json"
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("current difficulty card is unavailable")
    card = common.load_json(path)
    if card.get("task_sha256") != task_sha256 or card.get("status") != "complete":
        raise common.PipelineError("difficulty card is stale")
    attempt = card.get("attempt")
    if type(attempt) is not int or attempt <= 0:
        raise common.PipelineError("difficulty card attempt is malformed")
    stored = common.load_json(root / f"evidence/difficulty-cards/attempt-{attempt:04d}/summary.json")
    if stored != card:
        raise common.PipelineError("difficulty card latest receipt does not match its attempt")
    for group in card.get("item_evidence", {}).values():
        _checked_evidence(root, [item["path"] for item in group])
        for item in group:
            current = _checked_evidence(root, [item["path"]])[0]
            if current["sha256"] != item["sha256"]:
                raise common.PipelineError("difficulty card evidence changed")
    return card



def _workspace_file(root: Path, value: object, label: str, prefix: str) -> tuple[Path, str]:
    if not isinstance(value, str):
        raise common.PipelineError(f"{label} must be a workspace-relative path")
    parsed = PurePosixPath(value)
    if (parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts
            or str(parsed) != value):
        raise common.PipelineError(f"{label} path is unsafe")
    try:
        relative = parsed.relative_to(prefix)
    except ValueError as exc:
        raise common.PipelineError(f"{label} must stay under {prefix}/") from exc
    base = root / prefix
    path = base.joinpath(*relative.parts)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise common.PipelineError(f"{label} is unavailable") from exc
    if path.is_symlink() or not path.is_file() or not canonical.is_relative_to(base.resolve(strict=True)):
        raise common.PipelineError(f"{label} must be a regular file under {prefix}/")
    return canonical, common.sha256_file(canonical)


def _task_file(root: Path, value: object, label: str) -> tuple[Path, str]:
    return _workspace_file(root, value, label, "task")


def _ablation_snapshot(root: Path, value: object) -> tuple[Path, str]:
    """Keep reviewer-only counterfactual sources outside delivered task bytes."""
    if isinstance(value, str) and value.startswith("authoring/ablations/"):
        return _workspace_file(root, value, "ablated", "authoring/ablations")
    return _task_file(root, value, "ablated")


def _current_probe(root: Path, task_sha256: str) -> dict:
    path = root / "evidence/difficulty-probes/latest.json"
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("current difficulty probe receipt is unavailable")
    probe = common.load_json(path)
    if (probe.get("status") != "complete" or probe.get("task_sha256") != task_sha256
            or not common.finite_number(probe.get("score_effect"))
            or probe["score_effect"] <= 0):
        raise common.PipelineError("current, measured causal difficulty probe is required")
    attempt = probe.get("attempt")
    if type(attempt) is not int or attempt <= 0:
        raise common.PipelineError("difficulty probe attempt is malformed")
    stored_path = root / f"evidence/difficulty-probes/attempt-{attempt:04d}/summary.json"
    if common.load_json(stored_path) != probe:
        raise common.PipelineError("difficulty probe latest receipt does not match its attempt")
    snapshots = probe.get("snapshots")
    if not isinstance(snapshots, dict) or set(snapshots) != {"integrated", "ablated"}:
        raise common.PipelineError("difficulty probe snapshots are malformed")
    for side in ("integrated", "ablated"):
        entry = snapshots[side]
        if not isinstance(entry, dict) or entry.get("valid") is not True:
            raise common.PipelineError("difficulty probe snapshot is invalid")
        receipt_prefix = f"evidence/difficulty-probes/attempt-{attempt:04d}/{side}"
        _, report_hash = _workspace_file(root, f"{receipt_prefix}/report.json",
                                       f"{side} probe report", "evidence/difficulty-probes")
        _, process_hash = _workspace_file(root, f"{receipt_prefix}/process.json",
                                        f"{side} probe process", "evidence/difficulty-probes")
        if entry.get("report_sha256") != report_hash or entry.get("process_sha256") != process_hash:
            raise common.PipelineError("difficulty probe raw receipt hash mismatch")
    return probe


def _pack_mechanisms(source: dict) -> list[dict]:
    if (not isinstance(source, dict) or set(source) != {"schema_version", "mechanisms"}
            or type(source.get("schema_version")) is not int or source["schema_version"] != 1):
        raise common.PipelineError("difficulty pack must contain schema_version 1 and mechanisms")
    mechanisms = source["mechanisms"]
    if not isinstance(mechanisms, list) or not 1 <= len(mechanisms) <= 10:
        raise common.PipelineError("difficulty pack needs 1..10 mechanisms")
    seen = set()
    checked = []
    for item in mechanisms:
        required = {
            "mechanism_id", "kind", "statement", "evidence_question",
            "integrated_snapshot", "ablated_snapshot", "causal_claim",
            "failure_observable", "anti_gaming_check",
        }
        if not isinstance(item, dict) or set(item) != required:
            raise common.PipelineError("each mechanism needs the exact declared evidence fields")
        mechanism_id = item["mechanism_id"]
        if (not isinstance(mechanism_id, str) or mechanism_id in seen
                or MECHANISM_ID.fullmatch(mechanism_id) is None):
            raise common.PipelineError("mechanism_id must be a unique lowercase slug")
        seen.add(mechanism_id)
        if item["kind"] not in MECHANISM_KINDS:
            raise common.PipelineError("mechanism kind must be one of the declared causal dimensions")
        for key in ("statement", "evidence_question", "causal_claim",
                    "failure_observable", "anti_gaming_check"):
            if not _text(item.get(key)):
                raise common.PipelineError(f"{mechanism_id} needs a testable {key}")
        checked.append(item)
    return checked


def difficulty_pack(workspace: str | Path, input_path: str | Path,
                    required_mechanisms: int = 3) -> tuple[dict, int]:
    """Measure declared causal dimensions; this is a review diagnostic, not a score."""
    if type(required_mechanisms) is not int or not 1 <= required_mechanisms <= 10:
        raise common.PipelineError("required_mechanisms must be an integer in 1..10")
    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    validation = common.load_json(root / "evidence/harbor_validation.json")
    if validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256:
        raise common.PipelineError("difficulty pack needs current verifier validation")
    source = common.load_json(input_path)
    declared = _pack_mechanisms(source)
    resolved = []
    seen_pairs = set()
    for item in declared:
        integrated, integrated_hash = _task_file(root, item["integrated_snapshot"], "integrated")
        ablated, ablated_hash = _ablation_snapshot(root, item["ablated_snapshot"])
        if integrated_hash == ablated_hash:
            raise common.PipelineError("mechanism probe compares identical snapshots")
        pair = (integrated_hash, ablated_hash)
        if pair in seen_pairs:
            raise common.PipelineError("duplicate mechanism snapshot pair")
        seen_pairs.add(pair)
        resolved.append((integrated, integrated_hash, ablated, ablated_hash))
    attempt_number, attempt = common.next_attempt(root / "evidence/difficulty-packs")
    mechanisms = []
    for item, (integrated, integrated_hash, ablated, ablated_hash) in zip(declared, resolved):
        probe_number, probe_attempt = common.next_attempt(root / "evidence/difficulty-probes")
        scores = {}
        for name, path, digest in (
            ("integrated", integrated, integrated_hash),
            ("ablated", ablated, ablated_hash),
        ):
            import topify_measure

            result, observation = topify_measure._run(
                root / "task", path, probe_attempt / name,
                metadata["request"]["verifier_timeout_seconds"],
            )
            scores[name] = {
                "snapshot_sha256": digest,
                "score": result["total_score"],
                "valid": result.get("valid"),
                "report_sha256": common.sha256_file(probe_attempt / name / "report.json"),
                "process_sha256": common.sha256_file(probe_attempt / name / "process.json"),
                "elapsed_seconds": observation.get("elapsed_seconds"),
            }
        effect = float(scores["integrated"]["score"]) - float(scores["ablated"]["score"])
        complete = all(entry["valid"] is True for entry in scores.values()) and effect > 0
        probe = {
            "schema_version": 1, "attempt": probe_number,
            "task_sha256": task_sha256,
            "status": "complete" if complete else "revise",
            "causal_claim": item["causal_claim"],
            "snapshots": scores,
            "score_effect": effect,
            "interpretation": "The main reviewer must independently verify what the changed snapshot ablates.",
            "recorded_at": common.utc_now(),
        }
        common.write_new_json(probe_attempt / "summary.json", probe)
        common.write_latest_json(probe_attempt.parent / "latest.json", probe)
        mechanisms.append({
            **item,
            "integrated_snapshot_sha256": integrated_hash,
            "ablated_snapshot_sha256": ablated_hash,
            "probe_attempt": probe_number,
            "probe_receipt": str((probe_attempt / "summary.json").relative_to(root)),
            "probe_receipt_sha256": common.sha256_file(probe_attempt / "summary.json"),
            "score_effect": effect,
            "measured": complete,
        })
    headroom = None
    measurement_root = root / "evidence/measurements"
    measurement = measurement_root / "latest.json"
    if not measurement.is_file() or measurement.is_symlink():
        attempts = sorted(measurement_root.glob("attempt-*/summary.json"))
        measurement = attempts[-1] if attempts else None
    if measurement is not None and measurement.is_file() and not measurement.is_symlink():
        ladder = common.load_json(measurement).get("scores", {})
        nop, anchor = ladder.get("nop"), ladder.get("reference")
        if (metadata["request"]["reference_mode"] == "reference"
                and common.finite_number(nop) and common.finite_number(anchor)
                and float(anchor) > float(nop)):
            headroom = float(anchor) - float(nop)
    measured = [item for item in mechanisms if item["measured"]]
    for item in mechanisms:
        item["headroom_effect_fraction"] = (
            item["score_effect"] / headroom if headroom is not None and item["measured"] else None
        )
    combined = sum(item["score_effect"] for item in measured)
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "task_sha256": task_sha256,
        "status": "complete" if len(measured) >= required_mechanisms else "partial",
        "measured_headroom": headroom,
        "declared_mechanisms": len(mechanisms),
        "measured_mechanisms": len(measured),
        "required_mechanisms": required_mechanisms,
        "combined_score_effect": combined,
        "combined_headroom_fraction": combined / headroom if headroom is not None else None,
        "mechanisms": mechanisms,
        "interpretation": "Mechanism counts and effect fractions are diagnostics; they do not certify task difficulty or quality.",
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    return report, 0 if report["status"] == "complete" else 2


def difficulty_summary(workspace: str | Path, required_mechanisms: int = 3) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    latest_path = root / "evidence/difficulty-packs/latest.json"
    if not latest_path.is_file() or latest_path.is_symlink():
        report = {
            "schema_version": 1, "task_sha256": task_sha256,
            "status": "incomplete", "reasons": ["missing difficulty pack"],
            "required_mechanisms": required_mechanisms,
            "note": "Difficulty evidence is diagnostic and never a model score.",
        }
        return report, 2
    pack = common.load_json(latest_path)
    reasons = []
    if pack.get("task_sha256") != task_sha256:
        reasons.append("difficulty pack is stale for current task bytes")
    pack_attempt = pack.get("attempt")
    if type(pack_attempt) is not int or pack_attempt <= 0:
        reasons.append("difficulty pack attempt is malformed")
    else:
        attempt_path = root / f"evidence/difficulty-packs/attempt-{pack_attempt:04d}/summary.json"
        try:
            stored_pack = common.load_json(attempt_path)
        except common.PipelineError:
            reasons.append("difficulty pack attempt receipt is unavailable")
        else:
            if stored_pack != pack:
                reasons.append("difficulty pack attempt receipt changed")
    mechanisms = pack.get("mechanisms")
    measured = []
    if isinstance(mechanisms, list):
        seen_ids = set()
        seen_pairs = set()
        for item in mechanisms:
            if not isinstance(item, dict):
                reasons.append("malformed mechanism entry")
                continue
            mechanism_id = item.get("mechanism_id")
            if not isinstance(mechanism_id, str) or mechanism_id in seen_ids:
                reasons.append("duplicate or malformed mechanism_id")
                continue
            seen_ids.add(mechanism_id)
            receipt = item.get("probe_receipt")
            if not isinstance(receipt, str):
                reasons.append(f"{mechanism_id}: missing probe receipt")
                continue
            try:
                receipt_path, receipt_hash = _workspace_file(
                    root, receipt, "probe receipt", "evidence/difficulty-probes")
            except common.PipelineError as exc:
                reasons.append(f"{mechanism_id}: {exc}")
                continue
            if receipt_path.name != "summary.json" or not receipt_path.parent.name.startswith("attempt-"):
                reasons.append(f"{mechanism_id}: probe receipt path malformed")
                continue
            if item.get("probe_receipt_sha256") != receipt_hash:
                reasons.append(f"{mechanism_id}: probe receipt changed")
                continue
            probe = common.load_json(receipt_path)
            if (probe.get("task_sha256") != task_sha256 or probe.get("status") != "complete"
                    or probe.get("causal_claim") != item.get("causal_claim")):
                reasons.append(f"{mechanism_id}: probe is not complete or bound to this task")
                continue
            effect = probe.get("score_effect")
            if not common.finite_number(effect) or effect <= 0 or effect != item.get("score_effect"):
                reasons.append(f"{mechanism_id}: probe effect mismatch")
                continue
            snapshots = probe.get("snapshots", {})
            pair = []
            for side in ("integrated", "ablated"):
                entry = snapshots.get(side)
                if not isinstance(entry, dict) or entry.get("valid") is not True:
                    reasons.append(f"{mechanism_id}: {side} probe snapshot invalid")
                    break
                digest = entry.get("snapshot_sha256")
                if not isinstance(digest, str) or not common.HEX64.fullmatch(digest):
                    reasons.append(f"{mechanism_id}: {side} snapshot hash malformed")
                    break
                try:
                    report_path, report_hash = _workspace_file(
                        root, f"{receipt[:-len('/summary.json')]}/{side}/report.json",
                        f"{mechanism_id} {side} report", "evidence/difficulty-probes")
                    process_path, process_hash = _workspace_file(
                        root, f"{receipt[:-len('/summary.json')]}/{side}/process.json",
                        f"{mechanism_id} {side} process", "evidence/difficulty-probes")
                except common.PipelineError as exc:
                    reasons.append(f"{mechanism_id}: {exc}")
                    break
                if entry.get("report_sha256") != report_hash:
                    reasons.append(f"{mechanism_id}: {side} report_sha256 mismatch")
                    break
                if entry.get("process_sha256") != process_hash:
                    reasons.append(f"{mechanism_id}: {side} process_sha256 mismatch")
                    break
                pair.append(digest)
            else:
                integrated, ablated = pair
                if (integrated != item.get("integrated_snapshot_sha256")
                        or ablated != item.get("ablated_snapshot_sha256")
                        or integrated == ablated):
                    reasons.append(f"{mechanism_id}: probe does not bind the declared snapshot pair")
                    continue
                if tuple(pair) in seen_pairs:
                    reasons.append(f"{mechanism_id}: duplicate probe snapshot pair")
                    continue
                seen_pairs.add(tuple(pair))
                measured.append(item)
    headroom = pack.get("measured_headroom")
    if headroom is not None and (not common.finite_number(headroom) or headroom <= 0):
        reasons.append("difficulty pack headroom is malformed")
        headroom = None
    for item in measured:
        item["headroom_effect_fraction"] = (
            item["score_effect"] / headroom if headroom is not None else None
        )
    combined = sum(item["score_effect"] for item in measured)
    complete = (not reasons and len(measured) >= required_mechanisms
                and pack.get("status") in {"partial", "complete"})
    report = {
        "schema_version": 1,
        "task_sha256": task_sha256,
        "status": "complete" if complete else "incomplete",
        "reasons": reasons,
        "pack_receipt": "evidence/difficulty-packs/latest.json",
        "pack_sha256": common.sha256_file(latest_path),
        "measured_headroom": headroom,
        "declared_mechanisms": pack.get("declared_mechanisms"),
        "measured_mechanisms": len(measured),
        "required_mechanisms": required_mechanisms,
        "combined_score_effect": combined,
        "combined_headroom_fraction": combined / headroom if headroom is not None else None,
        "mechanisms": measured,
        "note": "Difficulty evidence is diagnostic and never a model score.",
    }
    return report, 0 if complete else 2


def review_is_current(root: Path, task_sha256: str) -> bool:
    path = root / "evidence/author-reviews/latest.json"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        review = common.load_json(path)
    except common.PipelineError:
        return False
    if (review.get("status") != "ready" or review.get("task_sha256") != task_sha256
            or review.get("reviewer_role") != "main-agent"
            or not _text(review.get("reviewer_id"))):
        return False
    attempt = review.get("attempt")
    if type(attempt) is not int or attempt <= 0:
        return False
    try:
        receipt = common.load_json(root / f"evidence/author-reviews/attempt-{attempt:04d}/summary.json")
    except common.PipelineError:
        return False
    if receipt != review:
        return False
    quality = review.get("quality")
    if not isinstance(quality, dict) or not set(QUALITY_IDS).issubset(quality):
        return False
    if not all(isinstance(quality[key], dict) and quality[key].get("status") == "pass" for key in QUALITY_IDS):
        return False
    difficulty = review.get("difficulty")
    if (not isinstance(difficulty, dict)
            or difficulty.get("structure") not in {"interleaved-graph", "domain-specific"}
            or not all(_text(difficulty.get(key)) for key in (
                "mechanism", "feedback_loop", "baseline_comparison", "causal_probe", "revision_strategy",
            ))):
        return False
    try:
        _current_probe(root, task_sha256)
    except (common.PipelineError, OSError, TypeError, KeyError):
        return False
    try:
        pack_summary, pack_code = difficulty_summary(root)
    except (common.PipelineError, OSError, TypeError, KeyError):
        return False
    if pack_code != 0 or pack_summary.get("status") != "complete":
        return False
    difficulty_paths = {
        item.get("path") for item in review.get("difficulty_evidence", [])
        if isinstance(item, dict)
    }
    if not {"evidence/difficulty-probes/latest.json",
            "evidence/difficulty-packs/latest.json",
            "evidence/difficulty-cards/latest.json"}.issubset(difficulty_paths):
        return False
    try:
        _current_difficulty_card(root, task_sha256)
    except (common.PipelineError, OSError, TypeError, KeyError):
        return False
    evidence_groups = review.get("quality_evidence", {})
    if not isinstance(evidence_groups, dict) or not set(QUALITY_IDS).issubset(evidence_groups):
        return False
    for group in [*(evidence_groups[key] for key in QUALITY_IDS), review.get("difficulty_evidence")]:
        if not isinstance(group, list) or not group:
            return False
        for item in group:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                return False
            try:
                current = _checked_evidence(root, [item["path"]])
            except (common.PipelineError, TypeError):
                return False
            if current[0]["sha256"] != item["sha256"]:
                return False
    return True
