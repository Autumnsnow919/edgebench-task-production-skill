#!/usr/bin/env python3
"""Bind the user-selected EdgeBench delivery format to a task workspace."""

from __future__ import annotations

from pathlib import Path
import re

import topify_common as common
import topify_profile
import topify_validate


FORMATS = frozenset({"sforge-only", "harbor"})
FORMAT_LABELS = {
    "sforge-only": "SForge-only customer ZIP",
    "harbor": "unpacked Harbor customer sample",
}
FORMAT_RE = re.compile(r"(?:sforge-only|harbor)\Z")


def _spec_hashes() -> dict[str, str]:
    """Record the local normative inputs when they are present.

    This is an applicability receipt, not a claim that an external platform
    accepted the package. Missing documents remain visible in the receipt.
    """
    project_root = Path(__file__).resolve().parents[2]
    candidates = {
        "meta_vendor_v3_zip": project_root / "Meta Vendor Data Spec V3.0.zip",
        "edgebench_vendor_v1": project_root / "EdgeBench Vendor Dataset Requirements.md",
        "pre_rollout_qc": project_root / "01 过程小样质检v0.2（出题后，跑轨迹之前）.md",
        "post_rollout_packaging": project_root / "02 打包prompt v0.1(跑完轨迹后）.md",
    }
    vendor_dir = project_root / "vendor-docs"
    fallback = {
        "meta_vendor_v3_zip": vendor_dir / "Meta Vendor Data Spec V3.0.zip",
        "edgebench_vendor_v1": vendor_dir / "EdgeBench Vendor Dataset Requirements.md",
        "pre_rollout_qc": vendor_dir / "01-process-qc-v0.2.md",
        "post_rollout_packaging": vendor_dir / "02-packaging-prompt-v0.1.md",
    }
    candidates = {
        key: path if path.is_file() else fallback[key]
        for key, path in candidates.items()
    }
    result = {}
    for key, path in candidates.items():
        result[key] = common.sha256_file(path) if path.is_file() and not path.is_symlink() else "missing"
    return result


def _contract_dir(root: Path) -> Path:
    return root / "evidence/delivery-contract"


def current(root: Path, task_sha256: str | None = None) -> dict:
    path = _contract_dir(root) / "latest.json"
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = common.load_json(path)
    except common.PipelineError:
        return {}
    if value.get("status") != "selected" or value.get("format") not in FORMATS:
        return {}
    if task_sha256 is not None and value.get("task_sha256") != task_sha256:
        return {}
    return value


def select(workspace: str | Path, delivery_format: str, reviewer_id: str = "main-agent") -> tuple[dict, int]:
    if not FORMAT_RE.fullmatch(delivery_format):
        raise common.PipelineError("delivery format must be sforge-only or harbor")
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    if not task.is_dir():
        raise common.PipelineError("select the delivery format after task generation")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise common.PipelineError("reviewer_id must be nonempty")
    task_sha256 = topify_validate.task_sha256(task)
    selected_profile = topify_profile.current(root, task_sha256)
    if metadata["request"].get("plugin") != "mini_continuous" and not selected_profile:
        raise common.PipelineError(
            "select a benchmark profile before selecting the delivery format"
        )
    previous = current(root)
    if previous and previous.get("task_sha256") == task_sha256 and previous.get("format") == delivery_format:
        return {**previous, "state": "already_selected"}, 0
    attempt_number, attempt = common.next_attempt(_contract_dir(root))
    receipt = {
        "schema_version": 1,
        "status": "selected",
        "attempt": attempt_number,
        "format": delivery_format,
        "format_label": FORMAT_LABELS[delivery_format],
        "task_id": metadata["task_id"],
        "task_sha256": task_sha256,
        "reviewer_role": "main-agent",
        "reviewer_id": reviewer_id,
        "selected_at": common.utc_now(),
        "spec_sha256": _spec_hashes(),
        "runtime_harness": "SForge",
        "profile_id": selected_profile.get("profile_id") if selected_profile else None,
        "format_contract": (
            "Ship SForge JSON, BENCHMARK.yaml, independent Work/Judge contexts and English task text."
            if delivery_format == "sforge-only"
            else "Ship unpacked Harbor task directories with separate agent/verifier environments and Harbor validation."
        ),
    }
    common.write_new_json(attempt / "summary.json", receipt)
    common.write_latest_json(_contract_dir(root) / "latest.json", receipt)
    common.append_event(root, "delivery_format_selected", format=delivery_format,
                        task_sha256=task_sha256, attempt=attempt_number)
    return receipt, 0


def require(root: Path, task_sha256: str, expected: str | None = None) -> dict:
    value = current(root, task_sha256)
    if not value:
        raise common.PipelineError(
            "delivery format has not been selected for the current task; "
            "run `delivery-format WORKSPACE --format sforge-only|harbor` before rollout"
        )
    if expected is not None and value["format"] != expected:
        raise common.PipelineError(
            f"selected delivery format is {value['format']!r}, but this command requires {expected!r}"
        )
    return value


def audit(workspace: str | Path) -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    task_sha256 = topify_validate.task_sha256(root / "task")
    value = current(root, task_sha256)
    if not value:
        return {"status": "incomplete", "reason": "no current delivery-format selection",
                "formats": sorted(FORMATS), "task_sha256": task_sha256}, 2
    return {"status": "selected", "task_sha256": task_sha256,
            "format": value["format"], "format_label": value["format_label"],
            "selected_at": value["selected_at"], "receipt": str(root / "evidence/delivery-contract/latest.json")}, 0


def manifest_format(manifest: dict, expected: str, *, require_explicit: bool = False) -> None:
    value = manifest.get("delivery_format")
    if value is None and not require_explicit:
        return
    if value not in FORMATS:
        raise common.PipelineError("delivery_format must be sforge-only or harbor")
    if value != expected:
        raise common.PipelineError(f"manifest delivery_format must be {expected}")
