#!/usr/bin/env python3
"""Load and bind benchmark-specific production requirements."""

from __future__ import annotations

from pathlib import Path
import re

import topify_common as common
import topify_validate


PROFILE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REQUIRED_KEYS = {"schema_version", "profile_id", "benchmark", "requirements_precedence", "delivery", "capabilities", "thresholds", "gate_ids", "checks"}
OPTIONAL_KEYS = {"supervision"}
THRESHOLD_KEYS = {
    "interaction_budget_seconds_minimum", "interaction_budget_seconds_preferred",
    "frontier_trials_minimum", "reference_repeats", "architectures_required",
    "q_review_required", "q_review_not_required",
}


def profiles_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "references/profiles"


def load_profile(profile_id: str, profile_file: str | Path | None = None) -> dict:
    if not isinstance(profile_id, str) or PROFILE_ID.fullmatch(profile_id) is None:
        raise common.PipelineError("profile_id must be a lowercase hyphenated identifier")
    path = Path(profile_file) if profile_file is not None else profiles_dir() / f"{profile_id}.json"
    value = common.load_json(path)
    if (not REQUIRED_KEYS <= set(value) or set(value) - REQUIRED_KEYS - OPTIONAL_KEYS
            or value.get("schema_version") != 1 or value.get("profile_id") != profile_id):
        raise common.PipelineError(f"profile keys or identity are invalid: {path}")
    if not isinstance(value.get("benchmark"), str) or not value["benchmark"].strip():
        raise common.PipelineError("profile benchmark must be nonempty")
    precedence = value["requirements_precedence"]
    if not isinstance(precedence, list) or not precedence or not all(isinstance(item, str) for item in precedence):
        raise common.PipelineError("profile requirements_precedence must be a nonempty string list")
    delivery = value["delivery"]
    if not isinstance(delivery, dict) or set(delivery) != {"formats", "default_format", "rollout_harness"}:
        raise common.PipelineError("profile delivery must declare formats, default_format and rollout_harness")
    formats = delivery["formats"]
    if not isinstance(formats, list) or not formats or not all(isinstance(item, str) for item in formats):
        raise common.PipelineError("profile delivery.formats must be a nonempty string list")
    if delivery["default_format"] is not None and delivery["default_format"] not in formats:
        raise common.PipelineError("profile default_format must be one of delivery.formats or null")
    thresholds = value["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLD_KEYS:
        raise common.PipelineError("profile thresholds are incomplete")
    for key in ("interaction_budget_seconds_minimum", "interaction_budget_seconds_preferred",
                "frontier_trials_minimum", "reference_repeats"):
        if type(thresholds[key]) is not int or thresholds[key] < 0:
            raise common.PipelineError(f"profile threshold {key} must be a nonnegative integer")
    if thresholds["interaction_budget_seconds_preferred"] < thresholds["interaction_budget_seconds_minimum"]:
        raise common.PipelineError("preferred budget cannot be below minimum budget")
    for key in ("architectures_required", "q_review_required", "q_review_not_required"):
        if not isinstance(thresholds[key], list) or not all(isinstance(item, str) for item in thresholds[key]):
            raise common.PipelineError(f"profile threshold {key} must be a string list")
    if set(thresholds["q_review_required"]) & set(thresholds["q_review_not_required"]):
        raise common.PipelineError("Q review required and not-required lists overlap")
    if not isinstance(value["checks"], dict):
        raise common.PipelineError("profile checks must be an object")
    capabilities = value["capabilities"]
    if (not isinstance(capabilities, dict)
            or set(capabilities) != {"reward_types", "artifact_mode", "architectures", "quality_ids"}
            or not isinstance(capabilities["reward_types"], list)
            or not isinstance(capabilities["architectures"], list)
            or not isinstance(capabilities["quality_ids"], list)
            or not all(isinstance(item, str) for item in capabilities["reward_types"] + capabilities["architectures"] + capabilities["quality_ids"])
            or capabilities["artifact_mode"] not in {"single-file"}):
        raise common.PipelineError("profile capabilities are invalid or unsupported by the current adapter")
    gates = value["gate_ids"]
    if not isinstance(gates, list) or not gates or len(set(gates)) != len(gates) or not all(isinstance(item, str) for item in gates):
        raise common.PipelineError("profile gate_ids must be a nonempty unique string list")
    common.canonical_sha256(value)
    return value


def assert_supported(profile: dict) -> None:
    """Fail before authoring when a profile asks for an unsupported adapter shape."""
    capabilities = profile["capabilities"]
    if set(capabilities["reward_types"]) != {"bounded_continuous"}:
        raise common.PipelineError(
            "the current EdgeBench authoring adapter supports bounded_continuous only; "
            "choose a compatible profile or provide a dedicated adapter"
        )
    if capabilities["artifact_mode"] != "single-file":
        raise common.PipelineError("the current adapter supports one declared snapshot file only")
    if set(capabilities["architectures"]) != common.ARCHITECTURES:
        raise common.PipelineError(
            "the current EdgeBench adapter requires both linux/amd64 and linux/arm64; "
            "an alternate architecture policy needs a dedicated adapter and explicit review"
        )
    if set(capabilities["quality_ids"]) != {f"Q{index}" for index in range(1, 8)}:
        raise common.PipelineError("the current main review adapter supports Q1-Q7 only")


def current(root: Path, task_sha256: str | None = None) -> dict:
    path = root / "evidence/benchmark-profile/latest.json"
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = common.load_json(path)
    except common.PipelineError:
        return {}
    if value.get("status") != "selected":
        return {}
    if task_sha256 is not None and value.get("task_sha256") != task_sha256:
        return {}
    try:
        load_profile(value["profile_id"], value["profile_path"])
        if common.sha256_file(value["profile_path"]) != value.get("profile_sha256"):
            return {}
        requirements_doc = value.get("requirements_doc")
        if requirements_doc is not None and (
            not Path(requirements_doc).is_file()
            or common.sha256_file(requirements_doc) != value.get("requirements_doc_sha256")
        ):
            return {}
    except (common.PipelineError, KeyError):
        return {}
    return value


def select(workspace: str | Path, profile_id: str, profile_file: str | Path | None = None,
           requirements_doc: str | Path | None = None, reviewer_id: str = "main-agent") -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise common.PipelineError("reviewer_id must be nonempty")
    profile = load_profile(profile_id, profile_file)
    assert_supported(profile)
    declared_profile = metadata["request"].get("benchmark_profile")
    if declared_profile is not None and declared_profile != profile_id:
        raise common.PipelineError(
            f"workspace benchmark_profile={declared_profile!r} does not match selected {profile_id!r}"
        )
    task_sha = topify_validate.task_sha256(task) if task.is_dir() else None
    profile_path = (Path(profile_file).absolute() if profile_file is not None
                    else profiles_dir() / f"{profile_id}.json")
    requirement_hash = "not-provided"
    if requirements_doc is not None:
        requirement_hash = common.sha256_file(requirements_doc)
    previous = current(root, task_sha)
    if (previous and previous.get("profile_id") == profile_id
            and previous.get("requirements_doc_sha256") == requirement_hash
            and previous.get("task_sha256") == task_sha):
        return {**previous, "state": "already_selected"}, 0
    number, attempt = common.next_attempt(root / "evidence/benchmark-profile")
    receipt = {
        "schema_version": 1,
        "status": "selected",
        "attempt": number,
        "profile_id": profile_id,
        "benchmark": profile["benchmark"],
        "profile_path": str(profile_path),
        "profile_sha256": common.sha256_file(profile_path),
        "requirements_doc": str(Path(requirements_doc).absolute()) if requirements_doc is not None else None,
        "requirements_doc_sha256": requirement_hash,
        "binding": "task" if task_sha is not None else "workspace-intent",
        "task_id": metadata["task_id"],
        "task_sha256": task_sha,
        "reviewer_role": "main-agent",
        "reviewer_id": reviewer_id,
        "selected_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", receipt)
    common.write_latest_json(root / "evidence/benchmark-profile/latest.json", receipt)
    common.append_event(root, "benchmark_profile_selected", profile_id=profile_id,
                        task_sha256=task_sha, attempt=number)
    return receipt, 0


def require(root: Path, task_sha256: str) -> tuple[dict, dict]:
    receipt = current(root, task_sha256)
    if not receipt:
        raise common.PipelineError(
            "benchmark profile is not selected for the current task; run benchmark-profile first"
        )
    profile = load_profile(receipt["profile_id"], receipt["profile_path"])
    return receipt, profile


def threshold(profile: dict, key: str, default: int | None = None) -> int:
    value = profile.get("thresholds", {}).get(key, default)
    if type(value) is not int:
        raise common.PipelineError(f"profile threshold is missing or invalid: {key}")
    return value
