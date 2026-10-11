#!/usr/bin/env python3
"""Produce the read-only pre-rollout process QC report required by PQC v0.2."""

from __future__ import annotations

from pathlib import Path
import re

import topify_common as common
import topify_delivery_contract
import topify_harbor_acceptance
import topify_review
import topify_profile
import topify_sforge_acceptance
import topify_validate


G_IDS = ("G-01", "G-02", "G-03", "G-04", "G-05", "G-06", "G-07",
         "G-08", "G-09", "G-10", "G-11", "G-12", "G-13", "G-15")


def _md(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def _review(root: Path, task_sha: str) -> dict:
    path = root / "evidence/author-reviews/latest.json"
    if not path.is_file():
        return {}
    value = common.load_json(path)
    return value if value.get("task_sha256") == task_sha else {}


def _has_model_trials(root: Path, task_sha: str) -> bool:
    path = root / "evidence/rollout-collections/latest.json"
    if not path.is_file():
        return False
    value = common.load_json(path)
    return value.get("task_sha256") == task_sha and bool(value.get("runs"))


def _row(identifier: str, status: str, detail: str, evidence: object = None) -> dict:
    return {"id": identifier, "status": status, "detail": detail, "evidence": evidence}


def report(workspace: str | Path, output: str | Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha = topify_validate.task_sha256(task)
    selected = topify_delivery_contract.current(root, task_sha)
    if not selected:
        raise common.PipelineError("pre-rollout report requires a current delivery-format selection")
    profile_receipt = topify_profile.current(root, task_sha)
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    fmt = selected["format"]
    acceptance = (topify_sforge_acceptance.audit_task(root)
                  if fmt == "sforge-only" else topify_harbor_acceptance.audit_task(root))
    review = _review(root, task_sha)
    quality = review.get("quality", {}) if isinstance(review.get("quality"), dict) else {}
    has_trials = _has_model_trials(root, task_sha)
    instruction = task / "instruction.md"
    instruction_text = instruction.read_text(encoding="utf-8") if instruction.is_file() else ""
    records = topify_validate.task_records(task)
    safe_task = bool(records) and all(item["type"] in {"file", "directory"} for item in records)
    review_ready = review.get("status") == "ready" and all(
        isinstance(quality.get(key), dict) and quality[key].get("status") == "pass"
        for key in topify_review.QUALITY_IDS
    )
    measured = common.load_json(root / "evidence/measurements/latest.json") if (root / "evidence/measurements/latest.json").is_file() else {}
    measured_ok = measured.get("task_sha256") == task_sha
    acceptance_ok = acceptance.get("status") == "complete"
    rows = [
        _row("G-01", "PASS", f"Current {profile['benchmark']} profile, format selection and normative source hashes are recorded.", selected["spec_sha256"]),
        _row("G-02", "PASS" if safe_task else "FAIL", "Current task inventory contains only regular files and directories.", task_sha),
        _row("G-03", "PASS" if instruction_text and not re.search(r"[\u4e00-\u9fff]", instruction_text) else "BLOCKED",
             "Model-visible instruction is readable UTF-8 English."),
        _row("G-04", "BLOCKED", "Full process-package content, hidden metadata and private-data review requires the exact submitted process package."),
        _row("G-05", "PASS" if not re.search(r"\b(codex|claude|kimi|cursor|copilot)\b", instruction_text, re.I) else "FAIL",
             "Task-visible text does not name a model or coding tool."),
        _row("G-06", "PASS" if acceptance_ok else "BLOCKED",
             f"Selected {fmt} runtime/isolation evidence is {'complete' if acceptance_ok else 'not complete'}.", acceptance.get("missing")),
        _row("G-07", "PASS" if acceptance_ok else "BLOCKED", "Executable task and verifier evidence is required before rollout.", acceptance.get("missing")),
        _row("G-08", "BLOCKED", "The independent package/report consistency check is post-rollout and has not run at this stage."),
        _row("G-09", "PASS" if review_ready else "BLOCKED", "Q1–Q7 review is bound to the current frozen task hash.", review.get("reviewed_at")),
        _row("G-10", "PASS" if review_ready else "BLOCKED", "Difficulty review and causal evidence are required before rollout.", review.get("difficulty_evidence")),
        _row("G-11", "PASS" if measured_ok else "BLOCKED", "NOP/interior/reference controls use current task bytes.", measured.get("attempt")),
        _row("G-12", "N/A" if not has_trials else "BLOCKED", "No model trials are planned at the pre-rollout stage."),
        _row("G-13", "N/A" if not has_trials else "BLOCKED", "No model metrics are claimed before rollout."),
        _row("G-15", "PASS", "Stage scope and unavailable evidence are explicitly labelled."),
    ]
    q_rows = []
    for key in topify_review.QUALITY_IDS:
        item = quality.get(key, {}) if isinstance(quality.get(key), dict) else {}
        q_rows.append({"id": key, "status": item.get("status", "pending"),
                       "finding": item.get("finding", "Not reviewed"), "evidence": item.get("evidence", [])})
    q_rows.append({"id": "Q8", "status": "not_required_by_owner",
                   "finding": "Removed from the current owner task review scope; vendor runtime isolation remains separately checked.",
                   "evidence": ["references/current-owner-scope.md"]})
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise common.PipelineError("pre-rollout QC output must be a new file in an existing directory")
    lines = [
        f"# EdgeBench Pre-rollout Process QC — {_md(metadata['task_id'])}", "",
        "This is the read-only, pre-trajectory process report. It does not claim final delivery or model-trial acceptance.", "",
        "## 1. Summary", "",
        "| Field | Result |", "|---|---|",
        f"| Selected format | `{fmt}` |",
        f"| Benchmark profile | `{profile['profile_id']}` ({_md(profile['benchmark'])}) |",
        f"| Frozen task SHA-256 | `{task_sha}` |",
        f"| Format acceptance audit | `{acceptance['status']}` |",
        f"| Model trajectories | {'N/A at this stage' if not has_trials else 'Present and requires trial review'} |",
        "| Formal delivery acceptance | Not assessed at this stage |", "",
        "## 2. G checks", "",
        "| Check | Status | Detail | Evidence |", "|---|---|---|---|",
    ]
    for item in rows:
        lines.append(f"| {item['id']} | {item['status']} | {_md(item['detail'])} | {_md(item.get('evidence', ''))} |")
    lines += ["", "## 3. Task quality review", "",
              "Q1–Q7 are reviewed against the frozen task. Q8 is recorded as not required by the current owner scope.", "",
              "| Criterion | Status | Finding | Evidence |", "|---|---|---|---|"]
    for item in q_rows:
        lines.append(f"| {item['id']} | {item['status']} | {_md(item['finding'])} | {_md(item['evidence'])} |")
    lines += ["", "## 4. Next stage", "",
              "Resolve every applicable BLOCKED/FAIL item before `rollout-check`. After real trajectories, run the post-rollout package inventory and `rl-task-qc` with the same format.", ""]
    destination.write_text("\n".join(lines), encoding="utf-8")
    failed = any(item["status"] == "FAIL" for item in rows)
    blocked = any(item["status"] == "BLOCKED" for item in rows)
    quality_blocked = any(item["status"] == "pending" for item in q_rows)
    state = "failed" if failed else "ready" if not blocked and not quality_blocked else "incomplete"
    blocking_count = (sum(item["status"] == "BLOCKED" for item in rows)
                      + sum(item["status"] == "pending" for item in q_rows))
    receipt = {
        "schema_version": 1,
        "status": state,
        "task_sha256": task_sha,
        "profile_id": profile["profile_id"],
        "format": fmt,
        "report_path": str(destination),
        "report_sha256": common.sha256_file(destination),
        "blocking_rows": blocking_count,
        "reviewed_at": common.utc_now(),
    }
    number, attempt = common.next_attempt(root / "evidence/pre-rollout-qc")
    common.write_new_json(attempt / "summary.json", {**receipt, "attempt": number})
    common.write_latest_json(root / "evidence/pre-rollout-qc/latest.json", {**receipt, "attempt": number})
    common.append_event(root, "pre_rollout_qc_recorded", attempt=number, status=state,
                        task_sha256=task_sha)
    return {"status": state, "format": fmt, "task_sha256": task_sha,
            "report": str(destination), "receipt": str(root / "evidence/pre-rollout-qc/latest.json"),
            "g_rows": rows, "q_rows": q_rows, "blocking_rows": blocking_count}, 2 if state != "ready" else 0
