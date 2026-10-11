#!/usr/bin/env python3
"""Build the task-bound pre-rollout freeze receipt before model hours."""

from __future__ import annotations

from pathlib import Path

import topify_common as common
import topify_completion
import topify_delivery_contract
import topify_harbor_acceptance
import topify_profile
import topify_review
import topify_sforge_acceptance
import topify_validate


def _latest(root: Path, name: str) -> dict:
    return topify_completion._latest(root / "evidence" / name)


def _status(rows: list[dict], *ids: str) -> bool:
    values = {row.get("id"): row.get("status") for row in rows}
    return bool(ids) and all(values.get(identifier) == "complete" for identifier in ids)


def audit(workspace: str | Path) -> dict:
    root, metadata = common.load_workspace(workspace)
    task_sha = topify_validate.task_sha256(root / "task")
    profile_receipt, profile = topify_profile.require(root, task_sha)
    format_receipt = topify_delivery_contract.require(root, task_sha)
    format_name = format_receipt["format"]
    acceptance = (topify_sforge_acceptance.audit_task(root)
                  if format_name == "sforge-only" else topify_harbor_acceptance.audit_task(root))
    acceptance_rows = acceptance.get("rows", [])
    review = _latest(root, "author-reviews")
    measurement = _latest(root, "measurements")
    build = _latest(root, "builds")
    data_hashes = _latest(root, "data-hashes")
    pre_qc = _latest(root, "pre-rollout-qc")
    adapter = _latest(root, "sforge-adapters")
    required = set(profile["gate_ids"])

    review_ready = (review.get("status") == "ready"
                    and review.get("task_sha256") == task_sha
                    and topify_review.review_is_current(root, task_sha))
    measurement_checks = {item.get("id"): item.get("status")
                          for item in measurement.get("checks", []) if isinstance(item, dict)}
    local_ready = measurement.get("task_sha256") == task_sha and bool(measurement_checks)
    adapter_ready = adapter.get("status") == "complete" and adapter.get("task_sha256") == task_sha
    build_ready = build.get("status") == "complete" and build.get("task_sha256") == task_sha
    hashes_ready = data_hashes.get("status") == "complete" and data_hashes.get("task_sha256") == task_sha

    rows = [
        {"id": "G_contract", "status": "complete",
         "detail": "Requirements, profile, task identity and delivery format are bound.",
         "evidence": [profile_receipt, format_receipt]},
        {"id": "G_rubric", "status": "complete" if review_ready else "incomplete",
         "detail": "Current task review is ready and task-bound.", "evidence": review.get("attempt")},
        {"id": "G_endpoints", "status": "complete" if local_ready else "incomplete",
         "detail": "Current executable score ladder and endpoint evidence exist.", "evidence": measurement.get("attempt")},
        {"id": "G_difficulty", "status": "complete" if review_ready else "incomplete",
         "detail": "Difficulty and causal review is current.", "evidence": review.get("difficulty_evidence")},
        {"id": "G_semantics", "status": "complete" if _status(acceptance_rows, "sforge.score_and_offline_contract", "sforge.snapshot_submission") or acceptance.get("status") == "complete" else "incomplete",
         "detail": "Scoring semantics and snapshot scope are current.", "evidence": acceptance.get("missing")},
        {"id": "G_feedback", "status": "complete" if local_ready else "incomplete",
         "detail": "Agent-visible local feedback has current measurement evidence.", "evidence": measurement.get("attempt")},
        {"id": "G_security", "status": "complete" if _status(acceptance_rows, "sforge.runtime_isolation", "harbor.runtime_isolation") else "incomplete",
         "detail": "Runtime isolation evidence is current.", "evidence": acceptance.get("missing")},
        {"id": "G_repro", "status": "complete" if _status(acceptance_rows, "sforge.reproducible_contexts", "harbor.reproducible_contexts") else "incomplete",
         "detail": "Reproducibility evidence is current.", "evidence": acceptance.get("missing")},
        {"id": "G_build", "status": "complete" if build_ready else "incomplete",
         "detail": "Delivered build evidence is current.", "evidence": build.get("attempt")},
        {"id": "G_pre_parity", "status": "complete" if adapter_ready and _status(acceptance_rows, "sforge.score_parity", "harbor.sforge_shared_core") else "incomplete",
         "detail": "Pre-rollout scorer/format parity is current.", "evidence": adapter.get("attempt")},
        {"id": "G_score_audit", "status": "complete" if hashes_ready and pre_qc.get("status") == "ready" else "incomplete",
         "detail": "Data hashes and pre-rollout QC receipt are ready.", "evidence": [data_hashes.get("attempt"), pre_qc.get("attempt")]},
    ]
    rows = [row for row in rows if row["id"] in required]
    complete = all(row["status"] == "complete" for row in rows)
    subject = {
        "task_sha256": task_sha,
        "profile_id": profile["profile_id"],
        "profile_sha256": profile_receipt["profile_sha256"],
        "delivery_format": format_name,
        "request_sha256": metadata["request_sha256"],
        "adapter_sha256": adapter.get("adapter", {}).get("task_definition_sha256"),
        "contexts_sha256": adapter.get("adapter", {}).get("contexts_sha256"),
        "resources": metadata["request"]["resources"],
    }
    return {"schema_version": 1, "status": "FROZEN" if complete else "INCOMPLETE",
            "task_id": metadata["task_id"], "subject": subject, "rows": rows,
            "missing": [row["id"] for row in rows if row["status"] != "complete"],
            "profile_receipt": profile_receipt, "format_receipt": format_receipt}


def freeze(workspace: str | Path, reviewer_id: str = "main-agent") -> tuple[dict, int]:
    root, _ = common.load_workspace(workspace)
    report = audit(root)
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise common.PipelineError("reviewer_id must be nonempty")
    report["reviewer_role"] = "main-agent"
    report["reviewer_id"] = reviewer_id
    report["reviewed_at"] = common.utc_now()
    report["contract_sha256"] = common.canonical_sha256(report["subject"])
    number, attempt = common.next_attempt(root / "evidence/freeze")
    report["attempt"] = number
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(root / "evidence/freeze/latest.json", report)
    common.append_event(root, "freeze_evaluated", attempt=number,
                        status=report["status"], task_sha256=report["subject"]["task_sha256"])
    return report, 0 if report["status"] == "FROZEN" else 2
