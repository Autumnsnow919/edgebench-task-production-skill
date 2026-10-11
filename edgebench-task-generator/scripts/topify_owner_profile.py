#!/usr/bin/env python3
"""Strict task-bound owner exception for the current English review sample."""

from __future__ import annotations

from pathlib import Path

import topify_common as common


PROFILE_ID = "owner-sample-2026-09-29"
DECISION_ID = "2026-09-29-owner-sample"
RECEIPT_KEYS = {
    "schema_version", "profile_id", "owner_decision", "task_id",
    "task_sha256", "sforge_task_sha256",
}


def load_profile(manifest: dict, root: Path, task_sha: str,
                 sforge_sha: str, task_id: str) -> dict | None:
    """An explicit owner decision is bound to both task identities, not a waiver list."""
    profile_id = manifest.get("delivery_profile")
    if profile_id is None:
        return None
    if (set(manifest) != {"schema_version", "batch_id", "workspaces",
                          "delivery_language", "delivery_profile"}
            or manifest.get("schema_version") != 1
            or profile_id != PROFILE_ID or manifest.get("delivery_language") != "en"
            or manifest.get("workspaces") != [str(root)]):
        raise common.PipelineError("owner sample requires one matching English task workspace")
    receipt_path = root / "evidence/owner-scope.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise common.PipelineError("owner-scope receipt must be a regular task-local file")
    receipt = common.load_json(receipt_path)
    if (set(receipt) != RECEIPT_KEYS or receipt.get("schema_version") != 1
            or receipt.get("profile_id") != PROFILE_ID
            or receipt.get("owner_decision") != DECISION_ID
            or receipt.get("task_id") != task_id
            or receipt.get("task_sha256") != task_sha
            or receipt.get("sforge_task_sha256") != sforge_sha):
        raise common.PipelineError("owner-scope receipt does not bind this frozen task")
    return receipt
