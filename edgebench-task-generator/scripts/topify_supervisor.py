#!/usr/bin/env python3
"""Supervisor protocol for hierarchical benchmark task production.

This module deliberately does not start an agent, call Orca, or kill a process.
It records the authority boundary around those actions so either the OpenCode
main session or a headless Orca CLI coordinator can execute the same signed
work orders and consume the same receipts.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import topify_common as common
import topify_dedup
import topify_profile
import topify_seed
import topify_validate


ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}\Z")
TASK_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*/[A-Za-z][A-Za-z0-9_-]*\Z")
ROLES = frozenset({"producer", "qc", "rollout"})
TRANSPORTS = frozenset({"opencode-task", "orca-cli"})
EMPLOYEE_STATES = frozenset({
    "queued", "active", "running", "waiting-qc", "repair-required",
    "rollout-pending", "rollout-running", "awaiting-approval",
    "needs-owner-decision", "replacement-required", "complete", "fired",
    "hardening-r1-required", "hardening-r2-required",
})
REPORT_STATES = frozenset({"heartbeat", "completed", "blocked", "failed", "question"})
QC_STATES = frozenset({"pass", "repair_required", "fail", "pass_with_exception"})
QC_SEVERITIES = frozenset({"info", "minor", "major", "critical"})
QC_DECISIONS = frozenset({"fire", "accept_exception", "request_user"})
GATE_STATES = frozenset({
    "CONTINUE", "ACCEPT", "EARLY_TOO_EASY", "INFRA_FAILURE",
    "EVIDENCE_INCOMPLETE", "FINAL_POLICY_REVIEW", "FINAL_POLICY_FAIL",
})
INFRA_SIGNALS = frozenset({
    "provider_failure", "quota_interruption", "missing_checkpoint",
    "container_failure", "judge_unavailable", "network_failure",
    "process_failed", "observation_failed", "incomplete_run",
})


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _id(value: object, label: str = "identifier") -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise common.PipelineError(f"{label} must be a lowercase hyphenated identifier")
    return value


def _task_id(value: object) -> str:
    if not isinstance(value, str) or TASK_ID_RE.fullmatch(value) is None:
        raise common.PipelineError("task_id must be a two-part benchmark/task identifier")
    return value


def _regular_file(value: str | Path, label: str) -> Path:
    path = Path(value)
    if path.is_symlink() or not path.is_file():
        raise common.PipelineError(f"{label} must be an existing regular file")
    return path.resolve(strict=True)


def _json_file(value: str | Path, label: str) -> tuple[Path, dict]:
    path = _regular_file(value, label)
    return path, common.load_json(path)


def _supervision_config(campaign: dict) -> dict:
    value = campaign.get("config", {}).get("supervision", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise common.PipelineError("campaign supervision config must be an object")
    allowed = {"max_concurrent", "transport_modes", "employee_unit", "max_child_tasks", "dedup_mode"}
    unknown = set(value) - allowed
    if unknown:
        raise common.PipelineError(f"unknown supervision config keys: {sorted(unknown)}")
    max_concurrent = value.get("max_concurrent", 1)
    if type(max_concurrent) is not int or max_concurrent < 1:
        raise common.PipelineError("supervision.max_concurrent must be a positive integer")
    modes = value.get("transport_modes", sorted(TRANSPORTS))
    if (not isinstance(modes, list) or not modes or
            not all(item in TRANSPORTS for item in modes)):
        raise common.PipelineError("supervision.transport_modes must contain OpenCode or Orca transports")
    employee_unit = value.get("employee_unit", "one-task")
    if employee_unit != "one-task":
        raise common.PipelineError("supervision.employee_unit must be one-task")
    max_child_tasks = value.get("max_child_tasks", 32)
    if type(max_child_tasks) is not int or max_child_tasks < 0:
        raise common.PipelineError("supervision.max_child_tasks must be a nonnegative integer")
    return {
        "max_concurrent": max_concurrent,
        "transport_modes": sorted(set(modes)),
        "employee_unit": employee_unit,
        "max_child_tasks": max_child_tasks,
        "dedup_mode": value.get("dedup_mode", "warn"),
    }


def validate_rollout_policy(profile: dict) -> dict:
    """Validate the optional benchmark-specific early-rollout policy.

    A profile without this policy remains usable by the legacy authoring path,
    but cannot be used for an automated supervisor rollout decision.
    """
    value = profile.get("supervision")
    if not isinstance(value, dict):
        raise common.PipelineError(
            f"profile {profile.get('profile_id', '<unknown>')} lacks supervision policy"
        )
    if value.get("employee_unit") != "one-task":
        raise common.PipelineError("profile supervision.employee_unit must be one-task")
    qc = value.get("qc")
    if not isinstance(qc, dict) or set(qc) != {"max_rounds", "qc2_escalation"}:
        raise common.PipelineError("profile supervision.qc must declare max_rounds and qc2_escalation")
    if qc["max_rounds"] != 2 or qc["qc2_escalation"] != "supervisor-user":
        raise common.PipelineError("supervision requires two QC rounds and supervisor-user escalation")
    rollout = value.get("rollout")
    if not isinstance(rollout, dict):
        raise common.PipelineError("profile supervision.rollout must be an object")
    checkpoints = rollout.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise common.PipelineError("supervision rollout needs checkpoints")
    previous = -1
    for checkpoint in checkpoints:
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) not in ({"name", "after_seconds", "mode", "metric"},
                                            {"name", "after_seconds", "mode", "metric", "operator", "value"})):
            raise common.PipelineError("invalid supervision rollout checkpoint shape")
        if not _text(checkpoint.get("name")) or type(checkpoint.get("after_seconds")) is not int:
            raise common.PipelineError("rollout checkpoint name/time is invalid")
        if checkpoint["after_seconds"] <= previous or checkpoint["after_seconds"] <= 0:
            raise common.PipelineError("rollout checkpoint times must increase")
        previous = checkpoint["after_seconds"]
        if checkpoint.get("mode") not in {"observe", "upper_bound"}:
            raise common.PipelineError("rollout checkpoint mode must be observe or upper_bound")
        if checkpoint.get("metric") not in {"best_score", "best_reward"}:
            raise common.PipelineError("rollout checkpoint metric is unsupported")
        if checkpoint.get("mode") == "upper_bound":
            if checkpoint.get("operator") not in {"gt", "gte"}:
                raise common.PipelineError("upper_bound checkpoint needs gt or gte operator")
            if not common.finite_number(checkpoint.get("value")):
                raise common.PipelineError("upper_bound checkpoint value must be finite")
    growth = rollout.get("growth")
    if (not isinstance(growth, dict)
            or set(growth) != {"enabled", "metric", "baseline", "operator", "min_delta"}
            or type(growth["enabled"]) is not bool
            or growth["metric"] not in {"best_score", "best_reward"}
            or growth["baseline"] not in {"nop", "zero", "requirements"}
            or growth["operator"] not in {"gt", "gte"}
            or not common.finite_number(growth["min_delta"])
            or growth["min_delta"] < 0):
        raise common.PipelineError("invalid supervision rollout growth policy")
    final = rollout.get("final")
    if not isinstance(final, dict) or set(final) != {"source", "low_result_action"}:
        raise common.PipelineError("supervision rollout final policy is invalid")
    if final["source"] not in {"requirements-document", "profile"}:
        raise common.PipelineError("final policy source must be requirements-document or profile")
    if final["low_result_action"] not in {"owner_review", "replace_seed", "record_only"}:
        raise common.PipelineError("invalid final low-result action")
    return value


def _root(workspace: str | Path) -> tuple[Path, dict]:
    root, campaign = topify_seed.load_campaign(workspace)
    return root, campaign


def _supervision_root(root: Path) -> Path:
    directory = root / "supervision"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise common.PipelineError("supervision path is unsafe")
    directory.mkdir(exist_ok=True)
    for name in ("employees", "child-requests", "dispatches", "reports", "decisions", "rollout-gates", "agent-workspaces"):
        path = directory / name
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise common.PipelineError(f"supervision/{name} path is unsafe")
        path.mkdir(exist_ok=True)
    events = directory / "events.jsonl"
    if events.is_symlink() or events.exists() and not events.is_file():
        raise common.PipelineError("supervision events path is unsafe")
    events.touch(exist_ok=True)
    return directory


def _append(root: Path, event: str, **fields: object) -> dict:
    record = {"schema_version": 1, "event": event, "recorded_at": common.utc_now(), **fields}
    record["event_sha256"] = common.canonical_sha256(record)
    with (root / "supervision/events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    return record


def _load_manifest(root: Path, campaign: dict) -> dict:
    path = root / "supervision/manifest.json"
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("run supervisor-init before managing employees")
    manifest = common.load_json(path)
    if manifest.get("campaign_config_sha256") != campaign.get("config_sha256"):
        raise common.PipelineError("supervision manifest does not match campaign config")
    return manifest


def initialize(
    workspace: str | Path,
    mode: str = "dual",
    max_concurrent: int | None = None,
    dedup_root: str | Path | None = None,
    supervisor_id: str | None = None,
    benchmark: str | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    supervision = _supervision_root(root)
    if mode not in {"dual", "opencode-task", "orca-cli"}:
        raise common.PipelineError("supervisor mode must be dual, opencode-task or orca-cli")
    config = _supervision_config(campaign)
    supervisor_id = supervisor_id or campaign["config"].get("campaign_id", "supervisor")
    supervisor_id = _id(supervisor_id, "supervisor_id")
    benchmark = benchmark or "Benchmark"
    if not _text(benchmark):
        raise common.PipelineError("benchmark must be nonempty")
    if max_concurrent is not None:
        if type(max_concurrent) is not int or max_concurrent < 1:
            raise common.PipelineError("max_concurrent must be a positive integer")
        config["max_concurrent"] = max_concurrent
    dedup_path = topify_dedup.ensure_root(dedup_root or (root / "supervision/dedup"))
    modes = config["transport_modes"] if mode == "dual" else [mode]
    manifest = {
        "schema_version": 1,
        "campaign": str(root),
        "campaign_config_sha256": campaign["config_sha256"],
        "mode": mode,
        "transport_modes": modes,
        "employee_unit": config["employee_unit"],
        "max_concurrent": config["max_concurrent"],
        "max_child_tasks": config["max_child_tasks"],
        "dedup_mode": config["dedup_mode"],
        "dedup_root": str(dedup_path),
        "supervisor_id": supervisor_id,
        "benchmark": benchmark,
        "status": "ready",
        "initialized_at": common.utc_now(),
    }
    path = supervision / "manifest.json"
    if path.exists() or path.is_symlink():
        existing = common.load_json(path)
        if existing.get("campaign_config_sha256") != manifest["campaign_config_sha256"]:
            raise common.PipelineError("supervision manifest already exists for another campaign config")
        stamp_path = supervision / "supervisor-stamp.json"
        if not stamp_path.exists() and not stamp_path.is_symlink():
            stamp = {
                "schema_version": 1,
                "watermark_type": "edgebench-supervisor",
                "supervisor_id": supervisor_id,
                "benchmark": benchmark,
                "campaign_root": str(root),
                "campaign_config_sha256": campaign["config_sha256"],
                "supervision_manifest_sha256": common.sha256_file(path),
                "dedup_root": existing.get("dedup_root", str(dedup_path)),
                "status": "active",
                "created_at": common.utc_now(),
            }
            common.write_new_json(stamp_path, stamp)
        return {"status": "already_initialized", "manifest": str(path), "stamp": str(stamp_path), **existing}, 0
    common.write_new_json(path, manifest)
    stamp_path = supervision / "supervisor-stamp.json"
    stamp = {
        "schema_version": 1,
        "watermark_type": "edgebench-supervisor",
        "supervisor_id": supervisor_id,
        "benchmark": benchmark,
        "campaign_root": str(root),
        "campaign_config_sha256": campaign["config_sha256"],
        "supervision_manifest_sha256": common.sha256_file(path),
        "dedup_root": str(dedup_path),
        "status": "active",
        "created_at": common.utc_now(),
    }
    common.write_new_json(stamp_path, stamp)
    _append(root, "supervisor_initialized", mode=mode, max_concurrent=config["max_concurrent"])
    return {"status": "ready", "manifest": str(path), "stamp": str(stamp_path), **manifest}, 0


def _employees(root: Path) -> list[dict]:
    values = []
    for path in sorted((root / "supervision/employees").glob("*.json")):
        if path.name == "latest.json":
            continue
        value = common.load_json(path)
        if value.get("employee_id") != path.stem:
            raise common.PipelineError("employee receipt filename does not match employee_id")
        values.append(value)
    return values


def _employee(root: Path, employee_id: str) -> dict:
    employee_id = _id(employee_id, "employee_id")
    path = root / "supervision/employees" / f"{employee_id}.json"
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError(f"unknown employee: {employee_id}")
    value = common.load_json(path)
    if value.get("employee_id") != employee_id:
        raise common.PipelineError("employee receipt identity mismatch")
    return value


def _tree(root: Path, employee_id: str) -> list[dict]:
    values = {item["employee_id"]: item for item in _employees(root)}
    if employee_id not in values:
        raise common.PipelineError(f"unknown employee: {employee_id}")
    result = []
    pending = [employee_id]
    while pending:
        current = pending.pop(0)
        value = values[current]
        result.append(value)
        pending.extend(
            child["employee_id"] for child in values.values()
            if child.get("parent_id") == current and child["employee_id"] not in pending
        )
    return result


def _save_employee(root: Path, value: dict) -> None:
    common.write_latest_json(root / "supervision/employees" / f"{value['employee_id']}.json", value)


def _active_count(root: Path) -> int:
    return sum(item.get("employment_status") in {"active", "running", "waiting-qc", "repair-required", "rollout-pending", "rollout-running", "awaiting-approval", "needs-owner-decision", "replacement-required", "hardening-r1-required", "hardening-r2-required"} for item in _employees(root))


def hire(
    workspace: str | Path,
    employee_id: str,
    role: str,
    transport: str,
    task_id: str | None = None,
    seed_id: str | None = None,
    parent_id: str | None = None,
    prompt_file: str | Path | None = None,
    replacement_of: str | None = None,
    fingerprint_file: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    _supervision_root(root)
    employee_id = _id(employee_id, "employee_id")
    if role not in ROLES:
        raise common.PipelineError(f"role must be one of {sorted(ROLES)}")
    if transport not in manifest["transport_modes"]:
        raise common.PipelineError(f"transport {transport} is not enabled for this supervisor")
    if task_id is not None:
        _task_id(task_id)
    if seed_id is not None and not _text(seed_id):
        raise common.PipelineError("seed_id must be nonempty text when supplied")
    path = root / "supervision/employees" / f"{employee_id}.json"
    if path.exists() or path.is_symlink():
        raise common.PipelineError("employee_id is already registered")
    parent = "supervisor"
    if parent_id is not None:
        parent = _id(parent_id, "parent_id")
        parent_record = _employee(root, parent)
        if parent_record.get("employment_status") == "fired":
            raise common.PipelineError("cannot hire a child under a fired employee")
        if parent_record.get("role") != "producer":
            raise common.PipelineError("only a producer employee may own child tasks")
    old = None
    if replacement_of is not None:
        old = _employee(root, replacement_of)
        if old.get("role") != "producer" or old.get("employment_status") != "fired":
            raise common.PipelineError("replacement_of must identify a fired producer")
        if seed_id is None or seed_id == old.get("seed_id"):
            raise common.PipelineError("a replacement producer must receive a new Seed")
    dedup_claim = None
    if role == "producer" and seed_id is not None:
        if fingerprint_file is None and manifest.get("dedup_mode") == "strict":
            raise common.PipelineError("strict dedup requires --fingerprint-file for a producer Seed")
        if fingerprint_file is not None:
            fingerprint_path = Path(fingerprint_file)
            dedup_claim, dedup_code = topify_dedup.claim(
                manifest["dedup_root"], fingerprint_path, employee_id,
                campaign["config"].get("campaign_id", "benchmark"), seed_id,
            )
            if dedup_code != 0:
                raise common.PipelineError(
                    f"Seed dedup collision: {dedup_claim.get('fingerprint', 'unknown')}"
                )
    active = _active_count(root)
    state = "active" if active < manifest["max_concurrent"] else "queued"
    job_root = root / "supervision/agent-workspaces" / employee_id
    if job_root.exists() or job_root.is_symlink():
        raise common.PipelineError("employee job_root already exists")
    job_root.mkdir(mode=0o700)
    prompt = None
    if prompt_file is not None:
        prompt = _regular_file(prompt_file, "prompt_file")
    value = {
        "schema_version": 1,
        "employee_id": employee_id,
        "parent_id": parent,
        "role": role,
        "transport": transport,
        "task_id": task_id,
        "seed_id": seed_id,
        "employment_status": state,
        "job_root": str(job_root),
        "prompt_path": str(prompt) if prompt else None,
        "prompt_sha256": common.sha256_file(prompt) if prompt else None,
        "replacement_of": old["employee_id"] if old else None,
        "dedup_claim": dedup_claim,
        "hired_at": common.utc_now(),
    }
    value["employment_contract_sha256"] = common.canonical_sha256(value)
    common.write_new_json(path, value)
    _append(root, "employee_hired", employee_id=employee_id, role=role,
            parent_id=parent, employment_status=state,
            replacement_of=value["replacement_of"])
    return value, 0


def status(workspace: str | Path) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    values = _employees(root)
    counts: dict[str, int] = {}
    for value in values:
        key = value.get("employment_status", "unknown")
        counts[key] = counts.get(key, 0) + 1
    alerts = []
    for value in values:
        if value.get("employment_status") in {"needs-owner-decision", "replacement-required", "repair-required"}:
            alerts.append({
                "kind": "employee-state",
                "employee_id": value["employee_id"],
                "status": value["employment_status"],
            })
        gate_dir = root / "supervision/rollout-gates" / value["employee_id"]
        gates = sorted(gate_dir.glob("attempt-*/gate.json")) if gate_dir.is_dir() else []
        if gates:
            latest_gate = common.load_json(gates[-1])
            if latest_gate.get("status") not in {"CONTINUE", "ACCEPT"}:
                alerts.append({
                    "kind": "rollout-gate",
                    "employee_id": value["employee_id"],
                    "status": latest_gate.get("status"),
                    "action": latest_gate.get("action"),
                    "gate_path": str(gates[-1]),
                })
    return {
        "schema_version": 1,
        "campaign": str(root),
        "manifest": manifest,
        "counts": dict(sorted(counts.items())),
        "active_count": _active_count(root),
        "employees": values,
        "alerts": alerts,
    }, 0


def _last_gate(root: Path, employee_id: str) -> dict | None:
    gate_dir = root / "supervision/rollout-gates" / employee_id
    gates = sorted(gate_dir.glob("attempt-*/gate.json")) if gate_dir.is_dir() else []
    if not gates:
        return None
    return common.load_json(gates[-1])


def _report_summary(employee: dict) -> dict | None:
    receipt = employee.get("last_report")
    if not isinstance(receipt, dict):
        return None
    value = {
        "status": receipt.get("report_status"),
        "recorded_at": receipt.get("recorded_at"),
        "report_path": receipt.get("report_path"),
        "report_sha256": receipt.get("report_sha256"),
    }
    path = receipt.get("report_path")
    if isinstance(path, str):
        report_path = Path(path)
        if report_path.is_file() and not report_path.is_symlink():
            try:
                report = common.load_json(report_path)
                if _text(report.get("summary")):
                    value["summary"] = report["summary"][:2000]
                if _text(report.get("question")):
                    value["question"] = report["question"][:1000]
            except common.PipelineError:
                value["summary_status"] = "unreadable"
    return value


def dashboard(workspace: str | Path, employee_id: str | None = None) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    values = _employees(root)
    if employee_id is not None:
        selected = _employee(root, employee_id)
        values = [selected]
    rows = []
    alerts = []
    for employee in values:
        gate = _last_gate(root, employee["employee_id"])
        row = {
            "employee_id": employee["employee_id"],
            "parent_id": employee.get("parent_id"),
            "role": employee.get("role"),
            "transport": employee.get("transport"),
            "task_id": employee.get("task_id"),
            "seed_id": employee.get("seed_id"),
            "status": employee.get("employment_status"),
            "job_root": employee.get("job_root"),
            "last_dispatch_id": employee.get("last_dispatch_id"),
            "report": _report_summary(employee),
            "qc": employee.get("last_qc_decision"),
            "rollout_gate": gate,
        }
        if employee.get("employment_status") in {
            "needs-owner-decision", "replacement-required", "repair-required",
        }:
            alerts.append({
                "kind": "employee-state",
                "employee_id": employee["employee_id"],
                "status": employee["employment_status"],
            })
        if gate is not None and gate.get("status") not in {"CONTINUE", "ACCEPT"}:
            alerts.append({
                "kind": "rollout-gate",
                "employee_id": employee["employee_id"],
                "status": gate.get("status"),
                "action": gate.get("action"),
            })
        rows.append(row)
    return {
        "schema_version": 1,
        "campaign": str(root),
        "generated_at": common.utc_now(),
        "capacity": {
            "active": _active_count(root),
            "max_concurrent": manifest["max_concurrent"],
            "available": max(0, manifest["max_concurrent"] - _active_count(root)),
        },
        "employees": rows,
        "alerts": alerts,
        "owner_action_required": bool(alerts),
    }, 0


def fire(workspace: str | Path, employee_id: str, reason: str, evidence: str | Path | None = None) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    value = _employee(root, employee_id)
    if value.get("employment_status") == "fired":
        raise common.PipelineError("employee is already fired")
    if not _text(reason):
        raise common.PipelineError("termination reason must be nonempty")
    evidence_path = _regular_file(evidence, "termination evidence") if evidence else None
    evidence_sha256 = common.sha256_file(evidence_path) if evidence_path else None
    fired = []
    for tree_value in _tree(root, employee_id):
        if tree_value.get("employment_status") == "fired":
            continue
        terminated = {
            **tree_value,
            "employment_status": "fired",
            "terminated_by": employee_id,
            "termination_reason": reason,
            "termination_evidence_path": str(evidence_path) if evidence_path else None,
            "termination_evidence_sha256": evidence_sha256,
            "fired_at": common.utc_now(),
            "process_action": "logical_state_only; stop only exact owned processes",
        }
        _save_employee(root, terminated)
        _append(root, "employee_fired", employee_id=terminated["employee_id"],
                role=terminated["role"], reason=reason, terminated_by=employee_id,
                evidence_sha256=evidence_sha256)
        fired.append(terminated)
    return {"terminated_root": employee_id, "terminated_employees": fired}, 0


def child_request(
    workspace: str | Path,
    parent_id: str,
    child_id: str,
    role: str,
    objective: str,
    transport: str,
    task_id: str | None = None,
    seed_id: str | None = None,
    prompt_file: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    parent = _employee(root, parent_id)
    if parent.get("role") != "producer" or parent.get("employment_status") == "fired":
        raise common.PipelineError("child request parent must be an active producer")
    if transport not in manifest["transport_modes"]:
        raise common.PipelineError("requested child transport is not enabled")
    child_id = _id(child_id, "child_id")
    if role not in ROLES:
        raise common.PipelineError("child role is invalid")
    if not _text(objective):
        raise common.PipelineError("child objective must be nonempty")
    max_children = _supervision_config(campaign)["max_child_tasks"]
    child_count = sum(
        item.get("parent_id") == parent_id and item.get("employment_status") != "fired"
        for item in _employees(root)
    )
    pending_count = sum(
        common.load_json(path).get("parent_id") == parent_id
        for path in (root / "supervision/child-requests").glob("*.json")
        if path.name != "latest.json"
    )
    if child_count + pending_count >= max_children:
        raise common.PipelineError("producer has reached supervision.max_child_tasks")
    existing = list((root / "supervision/child-requests").glob(f"{child_id}.json"))
    if existing:
        raise common.PipelineError("child_id already has a request")
    prompt = _regular_file(prompt_file, "prompt_file") if prompt_file else None
    value = {
        "schema_version": 1,
        "request_id": child_id,
        "parent_id": parent_id,
        "role": role,
        "transport": transport,
        "task_id": task_id,
        "seed_id": seed_id,
        "objective": objective,
        "prompt_path": str(prompt) if prompt else None,
        "prompt_sha256": common.sha256_file(prompt) if prompt else None,
        "status": "pending-supervisor-dispatch",
        "requested_at": common.utc_now(),
    }
    if task_id is not None:
        _task_id(task_id)
    path = root / "supervision/child-requests" / f"{child_id}.json"
    common.write_new_json(path, value)
    _append(root, "child_requested", request_id=child_id, parent_id=parent_id, role=role)
    return value, 0


def dispatch(workspace: str | Path, employee_id: str, output: str | Path | None = None) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("employment_status") not in {"active", "queued", "repair-required"}:
        raise common.PipelineError("employee is not dispatchable in its current state")
    if employee.get("employment_status") == "queued" and _active_count(root) >= manifest["max_concurrent"]:
        raise common.PipelineError("employee is queued until a concurrency slot is available")
    if (employee.get("role") == "producer" and employee.get("seed_id")
            and manifest.get("dedup_mode") == "strict" and not employee.get("dedup_claim")):
        raise common.PipelineError("producer cannot dispatch before its Seed has a dedup claim")
    number, attempt = common.next_attempt(root / "supervision/dispatches" / employee_id)
    dispatch_id = f"{employee_id}-d{number:04d}"
    prompt = employee.get("prompt_path")
    task = {
        "dispatch_id": dispatch_id,
        "employee_id": employee_id,
        "role": employee["role"],
        "transport": employee["transport"],
        "task_id": employee.get("task_id"),
        "seed_id": employee.get("seed_id"),
        "hardening_revision": (
            0 if employee.get("hardening_revision") is None
            else employee["hardening_revision"] + 1
            if employee.get("employment_status") == "repair-required"
            else employee.get("hardening_revision")
        ),
        "job_root": employee["job_root"],
        "prompt_path": prompt,
        "prompt_sha256": employee.get("prompt_sha256"),
        "contract_sha256": employee["employment_contract_sha256"],
        "worker_contract": "send heartbeat while active; report completion exactly once; ask through supervisor gate",
        "open_code": {
            "action": "native-task-or-opencode-run",
            "agent": {
                "producer": "edgebench-producer",
                "qc": "edgebench-qc",
                "rollout": "edgebench-rollout",
            }[employee["role"]],
            "argv_template": [
                "opencode", "run", "--agent",
                {"producer": "edgebench-producer", "qc": "edgebench-qc", "rollout": "edgebench-rollout"}[employee["role"]],
                "--format", "json", "<signed-prompt-content>",
            ],
            "working_directory": employee["job_root"],
        },
        "orca_cli": {
            "action": "orca orchestration worker-start",
            "requires_running_orca": True,
            "requires_task_id_from_coordinator": True,
            "argv_templates": [
                ["orca", "orchestration", "run-create", "--objective", "<employee-objective>", "--json"],
                ["orca", "orchestration", "task-create", "--spec", "<signed-prompt-content>", "--task-title", dispatch_id, "--json"],
                ["orca", "orchestration", "worker-start", "--task", "<taskId>", "--worktree", "current", "--agent", "opencode", "--setup", "run", "--json"],
                ["orca", "orchestration", "check", "--wait", "--types", "worker_done,escalation,question", "--json"],
            ],
        },
        "created_at": common.utc_now(),
    }
    task["dispatch_sha256"] = common.canonical_sha256(task)
    path = attempt / "dispatch.json"
    common.write_new_json(path, task)
    employee = {**employee, "employment_status": "running", "last_dispatch_id": dispatch_id,
                "last_dispatch_path": str(path)}
    _save_employee(root, employee)
    _append(root, "employee_dispatched", employee_id=employee_id, dispatch_id=dispatch_id,
            transport=employee["transport"], dispatch_sha256=task["dispatch_sha256"])
    if output is not None:
        destination = Path(output)
        if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
            raise common.PipelineError("dispatch output must be a new file in an existing directory")
        destination.write_text(json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "dispatched", "dispatch": task, "path": str(path), "manifest_modes": manifest["transport_modes"]}, 0


def report(workspace: str | Path, employee_id: str, report_file: str | Path) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    path, value = _json_file(report_file, "employee report")
    status_value = value.get("status")
    if status_value not in REPORT_STATES:
        raise common.PipelineError(f"employee report status must be one of {sorted(REPORT_STATES)}")
    if value.get("employee_id") not in {None, employee_id}:
        raise common.PipelineError("employee report employee_id does not match")
    if not _text(value.get("summary")) and status_value != "heartbeat":
        raise common.PipelineError("employee report needs a summary")
    revision = value.get("revision_index", 0)
    if type(revision) is not int or revision not in {0, 1, 2}:
        raise common.PipelineError("producer revision_index must be 0, 1 or 2")
    number, attempt = common.next_attempt(root / "supervision/reports" / employee_id)
    receipt = {
        "schema_version": 1,
        "employee_id": employee_id,
        "report_status": status_value,
        "report_path": str(path),
        "report_sha256": common.sha256_file(path),
        "attempt": number,
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "receipt.json", receipt)
    state = {
        "heartbeat": "running",
        "completed": (
            "hardening-r1-required" if employee.get("role") == "producer" and revision == 0
            else "hardening-r2-required" if employee.get("role") == "producer" and revision == 1
            else "awaiting-approval"
        ),
        "blocked": "needs-owner-decision",
        "question": "needs-owner-decision",
        "failed": "needs-owner-decision",
    }[status_value]
    _save_employee(root, {
        **employee,
        "employment_status": state,
        "last_report": receipt,
        **({"hardening_revision": revision} if employee.get("role") == "producer" else {}),
    })
    _append(root, "employee_reported", employee_id=employee_id, report_status=status_value,
            report_sha256=receipt["report_sha256"])
    return receipt, 0


def hardening_challenge(
    workspace: str | Path,
    producer_id: str,
    round_number: int,
    note: str,
    evidence: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    producer = _employee(root, producer_id)
    if producer.get("role") != "producer":
        raise common.PipelineError("hardening challenge needs a producer")
    expected_state = "hardening-r1-required" if round_number == 1 else "hardening-r2-required"
    if round_number not in {1, 2} or producer.get("employment_status") != expected_state:
        raise common.PipelineError("producer is not at the requested hardening round")
    if not _text(note):
        raise common.PipelineError("hardening challenge needs a mechanism-specific note")
    evidence_path = _regular_file(evidence, "hardening evidence") if evidence else None
    challenge = {
        "schema_version": 1,
        "producer_id": producer_id,
        "round": round_number,
        "verdict": "too-simple-for-target; hardening-required",
        "note": note,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_sha256": common.sha256_file(evidence_path) if evidence_path else None,
        "recorded_at": common.utc_now(),
    }
    challenge["challenge_sha256"] = common.canonical_sha256(challenge)
    number, attempt = common.next_attempt(root / "supervision/decisions" / producer_id)
    challenge["attempt"] = number
    common.write_new_json(attempt / f"hardening-r{round_number}.json", challenge)
    _save_employee(root, {**producer, "employment_status": "repair-required", "last_hardening_challenge": challenge})
    _append(root, "hardening_challenge_recorded", producer_id=producer_id,
            round=round_number, verdict=challenge["verdict"],
            challenge_sha256=challenge["challenge_sha256"])
    return challenge, 0


def dedup_claim(workspace: str | Path, employee_id: str, fingerprint_file: str | Path) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("role") != "producer" or not employee.get("seed_id"):
        raise common.PipelineError("dedup claim needs a producer with an assigned Seed")
    if employee.get("dedup_claim"):
        raise common.PipelineError("producer already has a dedup claim")
    claim_value, code = topify_dedup.claim(
        manifest["dedup_root"], fingerprint_file, employee_id,
        campaign["config"].get("campaign_id", "benchmark"), employee["seed_id"],
    )
    if code != 0:
        raise common.PipelineError(
            f"Seed dedup collision: {claim_value.get('fingerprint', 'unknown')}"
        )
    _save_employee(root, {**employee, "dedup_claim": claim_value})
    _append(root, "employee_dedup_claimed", employee_id=employee_id,
            fingerprint=claim_value["fingerprint"], claim_sha256=claim_value["claim_sha256"])
    return claim_value, 0


def dedup_register_task(workspace: str | Path, employee_id: str, task_sha256: str) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("role") != "producer":
        raise common.PipelineError("task dedup registration needs a producer")
    result, code = topify_dedup.register_task(
        manifest["dedup_root"], task_sha256, employee_id,
        campaign["config"].get("campaign_id", "benchmark"),
    )
    if code != 0:
        raise common.PipelineError(f"task SHA-256 collision: {task_sha256}")
    _save_employee(root, {**employee, "task_dedup": result})
    _append(root, "employee_task_registered", employee_id=employee_id,
            task_sha256=task_sha256, registration_sha256=result["registration_sha256"])
    return result, 0


def qc_decision(
    workspace: str | Path,
    producer_id: str,
    qc_id: str,
    round_number: int,
    decision: str,
    severity: str,
    report_file: str | Path,
    note: str,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    producer = _employee(root, producer_id)
    qc = _employee(root, qc_id)
    if (producer.get("role") != "producer" or qc.get("role") != "qc"
            or producer.get("employment_status") == "fired"
            or qc.get("employment_status") == "fired"):
        raise common.PipelineError("QC decision must bind a producer and a QC employee")
    producer_tree = {item["employee_id"] for item in _tree(root, producer_id)}
    qc_tree = {item["employee_id"] for item in _tree(root, qc_id)}
    if producer_tree & qc_tree or producer_id == qc.get("parent_id") or qc_id == producer.get("parent_id"):
        raise common.PipelineError("QC employee must be independent from the producer tree")
    if producer.get("hardening_revision") != 2:
        raise common.PipelineError("producer must complete mandatory r0->r1->r2 hardening before QC")
    if type(round_number) is not int or round_number not in {1, 2}:
        raise common.PipelineError("QC round must be 1 or 2")
    if decision not in QC_STATES or severity not in QC_SEVERITIES:
        raise common.PipelineError("invalid QC decision or severity")
    if not _text(note):
        raise common.PipelineError("QC decision needs a nonempty note")
    path = _regular_file(report_file, "QC report")
    receipt = {
        "schema_version": 1,
        "producer_id": producer_id,
        "qc_employee_id": qc_id,
        "round": round_number,
        "decision": decision,
        "severity": severity,
        "note": note,
        "report_path": str(path),
        "report_sha256": common.sha256_file(path),
        "task_id": producer.get("task_id"),
        "recorded_at": common.utc_now(),
    }
    receipt["decision_sha256"] = common.canonical_sha256(receipt)
    number, attempt = common.next_attempt(root / "supervision/decisions" / producer_id)
    receipt["attempt"] = number
    common.write_new_json(attempt / "qc-decision.json", receipt)
    if decision == "pass":
        next_state = "rollout-pending"
    elif decision == "pass_with_exception":
        next_state = "needs-owner-decision"
    elif round_number == 1:
        next_state = "repair-required"
    else:
        next_state = "needs-owner-decision"
    _save_employee(root, {**producer, "employment_status": next_state, "last_qc_decision": receipt})
    _append(root, "qc_decision_recorded", producer_id=producer_id, qc_employee_id=qc_id,
            round=round_number, decision=decision, severity=severity,
            next_state=next_state, decision_sha256=receipt["decision_sha256"])
    return {**receipt, "next_employee_state": next_state}, 0


def qc_resolve(
    workspace: str | Path,
    producer_id: str,
    decision: str,
    note: str,
    evidence: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    if decision not in QC_DECISIONS:
        raise common.PipelineError(f"decision must be one of {sorted(QC_DECISIONS)}")
    if not _text(note):
        raise common.PipelineError("QC resolution needs a note")
    producer = _employee(root, producer_id)
    if producer.get("employment_status") != "needs-owner-decision":
        raise common.PipelineError("producer is not waiting for an owner QC decision")
    evidence_path = _regular_file(evidence, "QC resolution evidence") if evidence else None
    resolution = {
        "schema_version": 1,
        "producer_id": producer_id,
        "decision": decision,
        "note": note,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_sha256": common.sha256_file(evidence_path) if evidence_path else None,
        "recorded_at": common.utc_now(),
    }
    resolution["resolution_sha256"] = common.canonical_sha256(resolution)
    number, attempt = common.next_attempt(root / "supervision/decisions" / producer_id)
    resolution["attempt"] = number
    common.write_new_json(attempt / "owner-qc-resolution.json", resolution)
    if decision == "fire":
        value, _ = fire(root, producer_id, f"owner QC resolution: {note}", evidence_path)
    elif decision == "accept_exception":
        value = {**producer, "employment_status": "rollout-pending", "qc_exception": resolution}
        _save_employee(root, value)
    else:
        value = {**producer, "employment_status": "needs-owner-decision", "qc_escalation": resolution}
        _save_employee(root, value)
    _append(root, "qc_resolution_recorded", producer_id=producer_id, decision=decision,
            resolution_sha256=resolution["resolution_sha256"])
    return {"resolution": resolution, "employee": value}, 0


def _metric(observations: dict, metric: str, entries: list[dict]) -> float | None:
    direct = observations.get(metric)
    if common.finite_number(direct):
        return float(direct)
    key = "score" if metric == "best_score" else "reward"
    values = [entry.get(key) for entry in entries if common.finite_number(entry.get(key)) and entry.get("valid", True) is True]
    return max((float(value) for value in values), default=None)


def evaluate_rollout(profile: dict, observations: dict, final: bool = False) -> dict:
    policy = validate_rollout_policy(profile)
    if not isinstance(observations, dict):
        raise common.PipelineError("rollout observations must be an object")
    status_value = observations.get("status")
    signals = {str(item) for item in observations.get("signals", []) if isinstance(item, str)}
    error_class = observations.get("error_class")
    if status_value in {"infra_failure", "process-failed", "observation-failed"} or error_class in INFRA_SIGNALS or signals & INFRA_SIGNALS:
        return {"status": "INFRA_FAILURE", "action": "retry_same_frozen_task", "reasons": sorted(signals | ({str(error_class)} if error_class else set()))}
    entries = observations.get("trajectory", [])
    if not isinstance(entries, list):
        raise common.PipelineError("rollout observations trajectory must be a list")
    normalized = [entry for entry in entries if isinstance(entry, dict) and common.finite_number(entry.get("elapsed_seconds"))]
    elapsed = observations.get("elapsed_seconds", 0)
    if not common.finite_number(elapsed):
        raise common.PipelineError("rollout observations elapsed_seconds must be finite")
    checkpoint_results = []
    for checkpoint in policy["rollout"]["checkpoints"]:
        if float(elapsed) < checkpoint["after_seconds"]:
            continue
        available = [entry for entry in normalized if float(entry["elapsed_seconds"]) <= checkpoint["after_seconds"]]
        value = _metric({}, checkpoint["metric"], available)
        row = {"name": checkpoint["name"], "after_seconds": checkpoint["after_seconds"], "metric": checkpoint["metric"], "value": value, "mode": checkpoint["mode"]}
        if checkpoint["mode"] == "upper_bound" and value is not None:
            bound = float(checkpoint["value"])
            hit = value > bound if checkpoint["operator"] == "gt" else value >= bound
            row["upper_bound"] = bound
            row["triggered"] = hit
            if hit:
                return {"status": "EARLY_TOO_EASY", "action": "fire_and_replace_seed", "checkpoint": row, "checkpoints": checkpoint_results + [row], "reasons": ["early_score_upper_bound"]}
        checkpoint_results.append(row)
    if final and policy["rollout"]["growth"]["enabled"]:
        growth = policy["rollout"]["growth"]
        current = _metric(observations, growth["metric"], normalized)
        if growth["baseline"] == "zero":
            baseline = 0.0
        else:
            baseline_key = "baseline_score" if growth["metric"] == "best_score" else "baseline_reward"
            baseline = observations.get(baseline_key)
            if not common.finite_number(baseline):
                return {"status": "EVIDENCE_INCOMPLETE", "action": "request_baseline_or_owner_review", "checkpoints": checkpoint_results, "reasons": ["growth_baseline_missing"]}
            baseline = float(baseline)
        if current is None:
            return {"status": "FINAL_POLICY_REVIEW", "action": policy["rollout"]["final"]["low_result_action"], "checkpoints": checkpoint_results, "reasons": ["no_valid_final_metric"]}
        delta = current - baseline
        required = float(growth["min_delta"])
        passed = delta > required if growth["operator"] == "gt" else delta >= required
        if not passed:
            action = policy["rollout"]["final"]["low_result_action"]
            return {"status": "FINAL_POLICY_FAIL" if action == "replace_seed" else "FINAL_POLICY_REVIEW", "action": action, "current": current, "baseline": baseline, "delta": delta, "required_delta": required, "checkpoints": checkpoint_results, "reasons": ["growth_rule_not_met"]}
        return {"status": "ACCEPT", "action": "continue_or_record_acceptance", "current": current, "baseline": baseline, "delta": delta, "checkpoints": checkpoint_results, "reasons": []}
    return {"status": "CONTINUE", "action": "continue_rollout", "checkpoints": checkpoint_results, "reasons": []}


def rollout_gate(
    workspace: str | Path,
    employee_id: str,
    observations_file: str | Path,
    profile_file: str | Path | None = None,
    task_workspace: str | Path | None = None,
    final: bool = False,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("role") != "producer":
        raise common.PipelineError("rollout gate must belong to a producer employee")
    observations_path, observations = _json_file(observations_file, "rollout observations")
    task_dedup = None
    if profile_file is None and task_workspace is not None:
        task_root, _ = common.load_workspace(task_workspace)
        task_hash = topify_validate.task_sha256(task_root / "task")
        task_dedup, task_code = topify_dedup.register_task(
            manifest["dedup_root"], task_hash, employee_id,
            campaign["config"].get("campaign_id", "benchmark"),
        )
        if task_code != 0:
            raise common.PipelineError(f"task SHA-256 collision: {task_hash}")
        receipt = topify_profile.current(task_root, task_hash)
        if not receipt:
            raise common.PipelineError("task workspace has no current benchmark profile")
        profile = topify_profile.load_profile(receipt["profile_id"], receipt["profile_path"])
    elif profile_file is not None:
        task_hash = observations.get("task_sha256")
        if isinstance(task_hash, str) and common.HEX64.fullmatch(task_hash):
            task_dedup, task_code = topify_dedup.register_task(
                manifest["dedup_root"], task_hash, employee_id,
                campaign["config"].get("campaign_id", "benchmark"),
            )
            if task_code != 0:
                raise common.PipelineError(f"task SHA-256 collision: {task_hash}")
        profile_path = _regular_file(profile_file, "profile_file")
        raw_profile = common.load_json(profile_path)
        profile_id = raw_profile.get("profile_id")
        profile = topify_profile.load_profile(profile_id, profile_path)
    else:
        raise common.PipelineError("rollout gate needs profile_file or task_workspace")
    result = evaluate_rollout(profile, observations, final=final)
    result.update({
        "schema_version": 1,
        "employee_id": employee_id,
        "task_id": employee.get("task_id"),
        "run_id": observations.get("run_id"),
        "observations_path": str(observations_path),
        "observations_sha256": common.sha256_file(observations_path),
        "profile_id": profile["profile_id"],
        "profile_sha256": common.canonical_sha256(profile),
        "final_check": final,
        "recorded_at": common.utc_now(),
    })
    result["gate_sha256"] = common.canonical_sha256(result)
    number, attempt = common.next_attempt(root / "supervision/rollout-gates" / employee_id)
    result["attempt"] = number
    common.write_new_json(attempt / "gate.json", result)
    if result["status"] == "EARLY_TOO_EASY":
        next_state = "replacement-required"
    elif result["status"] == "INFRA_FAILURE":
        next_state = "rollout-running"
    elif result["status"] in {"ACCEPT", "CONTINUE"}:
        next_state = "rollout-running"
    else:
        next_state = "needs-owner-decision"
    _save_employee(root, {
        **employee,
        "employment_status": next_state,
        "last_rollout_gate": result,
        **({"task_dedup": task_dedup} if task_dedup is not None else {}),
    })
    _append(root, "rollout_gate_recorded", employee_id=employee_id, status=result["status"],
            action=result["action"], gate_sha256=result["gate_sha256"])
    return result, 0 if result["status"] in {"CONTINUE", "ACCEPT"} else 2


def replace_seed(
    workspace: str | Path,
    former_employee_id: str,
    new_employee_id: str,
    new_seed_id: str,
    transport: str,
    prompt_file: str | Path | None = None,
    fingerprint_file: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    former = _employee(root, former_employee_id)
    if former.get("role") != "producer" or former.get("employment_status") != "fired":
        raise common.PipelineError("former employee must be a fired producer")
    if new_seed_id == former.get("seed_id") or not _text(new_seed_id):
        raise common.PipelineError("replacement must use a different nonempty Seed ID")
    value, code = hire(root, new_employee_id, "producer", transport,
                       task_id=None, seed_id=new_seed_id, parent_id=None,
                       prompt_file=prompt_file, replacement_of=former_employee_id,
                       fingerprint_file=fingerprint_file)
    if code != 0:
        return value, code
    order = {
        "schema_version": 1,
        "former_employee_id": former_employee_id,
        "new_employee_id": new_employee_id,
        "new_seed_id": new_seed_id,
        "new_employee_contract_sha256": value["employment_contract_sha256"],
        "created_at": common.utc_now(),
    }
    order["replacement_order_sha256"] = common.canonical_sha256(order)
    number, attempt = common.next_attempt(root / "supervision/decisions" / former_employee_id)
    order["attempt"] = number
    common.write_new_json(attempt / "replacement-order.json", order)
    _append(root, "replacement_order_created", former_employee_id=former_employee_id,
            new_employee_id=new_employee_id, new_seed_id=new_seed_id,
            replacement_order_sha256=order["replacement_order_sha256"])
    return {"order": order, "employee": value}, 0
