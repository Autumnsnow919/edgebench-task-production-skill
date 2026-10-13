#!/usr/bin/env python3
"""Supervisor protocol for hierarchical benchmark task production.

Dispatches and state changes are receipts, not worker execution. Explicit owner
Web messages can resume a verified, already-bound supervisor through the native
OpenCode prompt API. Merely reading this ledger never starts or stops an agent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess

import topify_common as common
from topify_ledger_lock import mutation
import topify_dedup
import topify_opencode
import topify_profile
import topify_seed
import topify_validate


ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}\Z")
TASK_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*/[A-Za-z][A-Za-z0-9_-]*\Z")
CHILD_ROLES = frozenset({"seed-worker", "prototype-worker"})
ROLES = frozenset({"producer", "qc", "rollout"}) | CHILD_ROLES
ROLE_AGENTS = {
    "producer": "edgebench-producer", "qc": "edgebench-qc",
    "rollout": "edgebench-rollout", "seed-worker": "edgebench-seed-worker",
    "prototype-worker": "edgebench-prototype-worker",
}
TRANSPORTS = frozenset({"opencode-task", "orca-cli"})
EMPLOYEE_STATES = frozenset({
    "queued", "active", "running", "waiting-qc", "repair-required",
    "rollout-pending", "rollout-running", "awaiting-approval",
    "needs-owner-decision", "replacement-required", "complete", "fired",
    "hardening-r1-required", "hardening-r2-required", "dispatch-created",
})
REPORT_STATES = frozenset({"heartbeat", "completed", "blocked", "failed", "question"})
QC_STATES = frozenset({"pass", "repair_required", "fail", "pass_with_exception"})
QC_SEVERITIES = frozenset({"info", "minor", "major", "critical"})
QC_DECISIONS = frozenset({"fire", "accept_exception", "continue_repair", "request_user"})
QC_OWNER_CHOICES = {"accept_exception": "接受指定例外", "continue_repair": "继续返修复检", "fire": "淘汰员工子树"}
HEARTBEAT_STALE_AFTER_SECONDS = 300
HEARTBEAT_DELIVERY_INTERVAL_SECONDS = 120
WORKER_MONITORED_STATES = frozenset({
    "active", "running", "dispatch-created", "repair-required", "rollout-running",
})
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


def _age_seconds(value: object, now: float | None = None) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    current = now if now is not None else datetime.now(timezone.utc).timestamp()
    return max(0.0, current - stamp)


def employee_health(employee: dict, runtime: dict | None = None,
                    now: float | None = None,
                    stale_after: int = HEARTBEAT_STALE_AFTER_SECONDS) -> dict:
    """Classify liveness from receipts plus the real bound OpenCode session.

    A ledger state is not a heartbeat. This is observational only: it never
    fires, resumes or kills an employee.
    """
    status = employee.get("employment_status", employee.get("status"))
    if status in {"fired", "complete"}:
        return {"status": "inactive", "attention": False, "age_seconds": None,
                "reason": "员工已结束，不再等待心跳。"}
    if status not in WORKER_MONITORED_STATES:
        return {"status": "waiting", "attention": False, "age_seconds": None,
                "reason": "当前阶段由上司/负责人处理，不要求员工继续发送心跳。"}
    runtime = runtime or {}
    report = employee.get("last_report") or employee.get("report") or {}
    recorded_at = report.get("recorded_at")
    if not recorded_at:
        binding = employee.get("session_binding") or {}
        recorded_at = binding.get("bound_at") or employee.get("hired_at")
    age = _age_seconds(recorded_at, now)
    session_status = runtime.get("status")
    if not employee.get("session_id"):
        if status in WORKER_MONITORED_STATES:
            return {"status": "unbound", "attention": True, "age_seconds": age,
                    "last_report_at": recorded_at,
                    "reason": "工作单存在但没有绑定实际员工会话。"}
        return {"status": "waiting", "attention": False, "age_seconds": age,
                "last_report_at": recorded_at, "reason": "当前阶段不要求员工运行。"}
    if age is None or age > stale_after:
        kind = "stalled" if session_status in {"idle", "interrupted", "failed", "unavailable"} else "heartbeat-missed"
        return {"status": kind, "attention": True, "age_seconds": age,
                "last_report_at": recorded_at, "session_status": session_status,
                "last_report_status": report.get("report_status", report.get("status")),
                "reason": "实际会话已空闲/不可用且没有新心跳。" if kind == "stalled"
                          else "实际会话可能仍运行，但员工超过心跳期限未汇报。"}
    return {"status": "healthy", "attention": False, "age_seconds": age,
            "last_report_at": recorded_at, "session_status": session_status,
            "last_report_status": report.get("report_status", report.get("status")), "reason": "最近有员工报告。"}


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


@mutation
def initialize(
    workspace: str | Path,
    mode: str = "dual",
    max_concurrent: int | None = None,
    dedup_root: str | Path | None = None,
    supervisor_id: str | None = None,
    benchmark: str | None = None,
    dedup_mode: str | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    supervision = _supervision_root(root)
    if mode not in {"dual", "opencode-task", "orca-cli"}:
        raise common.PipelineError("supervisor mode must be dual, opencode-task or orca-cli")
    config = _supervision_config(campaign)
    if dedup_mode is not None:
        if dedup_mode not in {"strict", "warn"}:
            raise common.PipelineError("dedup_mode must be strict or warn")
        config["dedup_mode"] = dedup_mode
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


@mutation
def bind_supervisor_session(workspace: str | Path, session_id: str) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    info = topify_opencode._visible_session(root.parent.parent.parent, session_id)
    session_directory = Path(info["directory"]).resolve()
    if not root.resolve().is_relative_to(session_directory):
        raise common.PipelineError("supervisor session directory does not contain the campaign")
    existing = manifest.get("supervisor_session_id")
    if existing not in {None, session_id}:
        raise common.PipelineError("campaign already has a different supervisor session")
    updated = {**manifest, "supervisor_session_id": session_id,
               "supervisor_session_directory": str(session_directory),
               "supervisor_session_bound_at": common.utc_now()}
    common.write_latest_json(root / "supervision/manifest.json", updated)
    _append(root, "supervisor_session_bound", session_id=session_id,
            directory=str(session_directory))
    return updated, 0


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


def _owner_form_employee_id(root: Path, form_id: str) -> str | None:
    """Locate an old detached-form receipt for compatibility diagnostics only."""
    for employee in _employees(root):
        if employee.get("owner_form", {}).get("form_id") == form_id:
            return employee["employee_id"]
    return None


def _unresolved_qc_failure(employee: dict, *, second_round_only: bool = False) -> bool:
    qc = employee.get("last_qc_decision") or {}
    if employee.get("role") != "producer" or qc.get("decision") not in {"fail", "repair_required", "pass_with_exception"}:
        return False
    if second_round_only and (not isinstance(qc.get("round"), int) or qc["round"] < 2):
        return False
    resolution = employee.get("owner_qc_resolution") or {}
    return not (qc.get("decision_sha256") and resolution.get("qc_decision_sha256") == qc["decision_sha256"]
                and resolution.get("decision") in {"fire", "accept_exception", "continue_repair"})


def _qc_owner_review(root: Path, employee: dict) -> dict | None:
    """Evidence-linked owner brief, never convert missing proof into a failure."""
    if not _unresolved_qc_failure(employee, second_round_only=True):
        return None
    qc = employee["last_qc_decision"]
    brief = {
        "producer_id": employee["employee_id"], "round": qc["round"],
        "decision": qc["decision"], "severity": qc.get("severity"), "summary": qc.get("note"),
        "report_path": qc.get("report_path"), "report_sha256": qc.get("report_sha256"),
        "qc_decision_sha256": qc.get("decision_sha256"), "report_integrity": "unavailable",
        "findings": [], "resolved_items": [], "quality_checks": {},
        "allowed_owner_decisions": ["accept_exception", "continue_repair", "fire"],
        "automatic_fire_allowed": False,
        "notice": "QC-2 后仍未通过，保留员工及证据等待负责人。接受例外不改变原始 QC/成绩，不自动冻结或试跑。",
    }
    try:
        source = _regular_file(qc.get("report_path"), "QC report")
        if not source.is_relative_to(root):
            return brief
        if common.sha256_file(source) != qc.get("report_sha256"):
            return {**brief, "report_integrity": "changed"}
        report = common.load_json(source)
    except (common.PipelineError, OSError, ValueError, TypeError):
        return brief
    brief["report_integrity"] = "verified"
    checks = report.get("q1_q7") or report.get("quality_checks")
    if isinstance(checks, dict):
        brief["quality_checks"] = {key: value for key, value in checks.items()
                                   if key in {f"Q{n}" for n in range(1, 8)} and isinstance(value, str)}
    closed = report.get("repair_closed") or report.get("resolved_findings") or []
    if isinstance(closed, list):
        brief["resolved_items"] = [item[:2000] for item in closed if isinstance(item, str)][:100]
    findings = report.get("findings") or report.get("remaining_findings") or report.get("remaining_blockers") or []
    if isinstance(findings, list):
        for item in findings[:100]:
            if isinstance(item, str):
                brief["findings"].append({"title": item[:2000], "status": "not-classified", "severity": qc.get("severity")})
            elif isinstance(item, dict):
                brief["findings"].append({key: item[key] for key in (
                    "id", "title", "summary", "status", "severity", "requirement", "evidence", "evidence_path",
                    "expected", "observed", "repair", "recommendation", "impact", "waivable"
                ) if key in item})
    return brief


def qc_reply_scopes(root: Path, consumer: dict) -> list[dict]:
    """Bind an answered native question to the QC subject it actually asked."""
    result = []
    for index, question in enumerate(consumer.get("questions", [])):
        text = question.get("question", "") if isinstance(question, dict) else ""
        if not isinstance(text, str):
            continue
        for employee in _employees(root):
            qc = employee.get("last_qc_decision") or {}
            digest = qc.get("decision_sha256")
            if (_unresolved_qc_failure(employee) and digest and employee["employee_id"] in text and digest in text):
                result.append({"producer_id": employee["employee_id"], "qc_decision_sha256": digest,
                               "qc_report_sha256": qc.get("report_sha256"), "answer_key": f"q{index}"})
    return result


def _qc_owner_authorization(root: Path, producer: dict, decision: str, evidence: Path | None) -> dict:
    if evidence is None or not evidence.is_relative_to(root / "supervision/native-interactions"):
        raise common.PipelineError("QC 例外、追加返修或淘汰必须附上负责人真实 question 回答回执；只有上司说明不能代替负责人决定。")
    receipt = common.load_json(evidence)
    digest = receipt.get("reply_sha256")
    if not digest or common.canonical_sha256({k: v for k, v in receipt.items() if k != "reply_sha256"}) != digest:
        raise common.PipelineError("负责人回答回执身份不一致。")
    manifest = common.load_json(root / "supervision/manifest.json")
    if receipt.get("status") != "answered" or receipt.get("session_id") != manifest.get("supervisor_session_id"):
        raise common.PipelineError("回答不属于绑定的实际监督会话。")
    qc = producer.get("last_qc_decision") or {}
    for scope in receipt.get("qc_owner_scopes", []):
        if (scope.get("producer_id") == producer["employee_id"]
                and scope.get("qc_decision_sha256") == qc.get("decision_sha256")
                and scope.get("qc_report_sha256") == qc.get("report_sha256")
                and receipt.get("answer", {}).get(scope.get("answer_key")) in {decision, QC_OWNER_CHOICES[decision]}):
            return {"form_id": receipt.get("form_id"), "reply_sha256": digest,
                    "session_id": receipt["session_id"], "answer_key": scope["answer_key"]}
    raise common.PipelineError("负责人回答没有授权此员工、当前 QC 凭证和这一操作；不能把其他问题的回答套用过来。")


@mutation
def record_qc_owner_answer(workspace: str | Path, producer_id: str, decision: str) -> tuple[dict, int]:
    """Import a real completed question-tool answer from TUI/native chat."""
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    producer = _employee(root, producer_id)
    if decision not in QC_OWNER_CHOICES or not _unresolved_qc_failure(producer):
        raise common.PipelineError("先定位待负责人决定的具体 QC 及操作。")
    session_id = manifest.get("supervisor_session_id")
    directory = manifest.get("supervisor_session_directory")
    if not session_id or not directory:
        raise common.PipelineError("先绑定实际上司会话。")
    topify_opencode._visible_session(directory, session_id)
    messages = topify_opencode._get(f"/api/session/{session_id}/message?limit=5&order=desc&type=assistant").get("data", [])
    for message in messages:
        for tool in message.get("content", []):
            state = tool.get("state", {})
            if tool.get("type") != "tool" or tool.get("name") != "question" or state.get("status") != "completed":
                continue
            questions = state.get("input", {}).get("questions", [])
            consumer = {"message_id": message.get("id"), "tool_id": tool.get("id"),
                        "agent": message.get("agent"), "questions": questions, "status": "completed"}
            scopes = qc_reply_scopes(root, consumer)
            output = "\n".join(part.get("text", "") for part in state.get("content", []) if isinstance(part, dict))
            for scope in scopes:
                if scope["producer_id"] != producer_id:
                    continue
                index = int(scope["answer_key"][1:])
                question = questions[index]["question"]
                label = QC_OWNER_CHOICES[decision]
                if ("User has answered your questions:" not in output or f'"{question}"="{label}"' not in output):
                    continue
                receipt = {
                    "schema_version": 1, "source": "opencode-question-tool-result", "form_id": None,
                    "session_id": session_id, "consumer": consumer, "status": "answered",
                    "answer": {scope["answer_key"]: label}, "qc_owner_scopes": [scope],
                    "tool_output_sha256": common.canonical_sha256(output),
                    "execution_status": "awaiting-agent-action", "recorded_at": common.utc_now(),
                }
                receipt["reply_sha256"] = common.canonical_sha256(receipt)
                directory = root / "supervision/native-interactions" / ("tool-" + common.canonical_sha256(consumer)[:32])
                number, attempt = common.next_attempt(directory)
                path = attempt / "reply.json"
                common.write_new_json(path, receipt)
                _append(root, "qc_owner_answer_recorded", producer_id=producer_id, decision=decision,
                        reply_sha256=receipt["reply_sha256"], message_id=consumer["message_id"],
                        qc_decision_sha256=scope["qc_decision_sha256"])
                return {"status": "recorded", "evidence_path": str(path), **receipt}, 0
    raise common.PipelineError("未找到实际 question 工具对当前 QC 凭证和该选项的完成回答；不能用聊天猜测或上司自己的说明批准。")


def _deliver_to_supervisor(root: Path, text: str, sender_type: str,
                           sender_id: str, subject: str = "", message: dict | None = None) -> dict:
    """Deliver subordinate/watchdog input to the bound real Supervisor session."""
    manifest = common.load_json(root / "supervision/manifest.json")
    session_directory = manifest.get("supervisor_session_directory")
    if not session_directory or not manifest.get("supervisor_session_id"):
        return {"status": "unavailable", "reason": "没有绑定实际上司会话。"}
    runtime = topify_opencode.supervisor_runtime(session_directory, root, manifest)
    if not runtime.get("can_chat"):
        return {"status": "unavailable", "session_id": runtime.get("session_id"),
                "reason": runtime.get("reason", "无法核验上司会话。")}
    if message is None:
        message, _ = _persist_message(root, "supervisor", manifest["supervisor_id"], text,
                                     sender_type=sender_type, sender_id=sender_id)
    prompt = (
        f"{sender_type} update from {sender_id}; ledger message {message['message_id']}.\n"
        f"Campaign: {root}\nSubject: {subject or 'production update'}\n"
        "This is an actual subordinate/watchdog communication delivered to your bound session. "
        "Acknowledge it in this session, inspect the named employee/session and take the next lawful action. "
        "Use supervisor-directive to send a real response into the employee's bound session, not only a ledger file. "
        "For watchdog incidents record supervisor-ack with the incident key and action/evidence; admission is not acknowledgment. "
        "If an owner question currently blocks you, retain that question and report waiting-owner, never invent its answer. "
        "Do not infer completion from a ledger write. Do not automatically fire a stalled employee; "
        "contact/resume/inspect it or escalate a concrete owner question. Preserve QC and technical gates.\n\n"
        + text
    )
    try:
        admitted = topify_opencode.send_message(session_directory, runtime["session_id"], prompt)
    except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        _append(root, "supervisor_delivery_failed", message_id=message["message_id"],
                sender_type=sender_type, sender_id=sender_id,
                session_id=runtime["session_id"], status="unconfirmed")
        return {"status": "unconfirmed", "message_id": message["message_id"],
                "session_id": runtime["session_id"], "reason": str(exc)}
    data = admitted.get("response", {}).get("data", {})
    receipt = {
        "schema_version": 1, "status": "admitted", "message_id": message["message_id"],
        "session_id": runtime["session_id"], "sender_type": sender_type,
        "sender_id": sender_id, "inbox_id": data.get("id"),
        "recorded_at": common.utc_now(), "execution_status": "awaiting-agent-action",
    }
    receipt["delivery_sha256"] = common.canonical_sha256(receipt)
    _, attempt = common.next_attempt(root / "supervision/session-deliveries")
    common.write_new_json(attempt / "delivery.json", receipt)
    _append(root, "supervisor_update_delivered", **receipt)
    return receipt


def _should_deliver_report(employee: dict, report_status: str, now: float) -> bool:
    if report_status != "heartbeat":
        return True
    previous = employee.get("last_supervisor_delivery") or {}
    age = _age_seconds(previous.get("recorded_at"), now)
    return age is None or age >= HEARTBEAT_DELIVERY_INTERVAL_SECONDS


@mutation
def send_supervisor_message(workspace: str | Path, text: str) -> tuple[dict, int]:
    """Admit owner input to the bound *real* session; never claim execution."""
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    directory = manifest.get("supervisor_session_directory")
    if not directory:
        raise common.PipelineError("上司尚未绑定实际会话，消息未发送。")
    runtime = topify_opencode.supervisor_runtime(directory, root, manifest)
    if not runtime.get("can_chat"):
        raise common.PipelineError(runtime.get("reason", "无法核验上司会话"))
    message, _ = send_message(root, "supervisor", manifest["supervisor_id"], text)
    prompt = (
        f"Owner Web message {message['message_id']}\nCampaign scope: {root}\n"
        f"Ledger message: {root / 'supervision/messages' / ('supervisor--' + manifest['supervisor_id'])}\n"
        "You are the bound supervisor for this campaign. Read its locked requirements and current evidence. "
        "Reply in this actual OpenCode session. A ledger write or Form ID is not execution. "
        "If an owner policy decision is truly needed, use the native question TOOL and await its answer; "
        "never create an unattached Form using shell/API or ask the owner to approve every child completion. "
        "Explain the specific finding, affected task and the action each choice permits. "
        "Acceptance/approval does not require an owner reason; do not add a mandatory explanation field. "
        "After a native answer, execute only its scoped lawful next step, retain action receipts, "
        "QC-1 failure requires repair; after repaired QC-2 any remaining failure/incomplete finding must go to the owner. "
        "Do not automatically fire or silently relax the rubric; present exact findings, closed repairs, evidence, "
        "severity and impact. Distinguish failed checks from unmeasured/incomplete evidence and extra reviewer demands. "
        "Let the owner accept a scoped exception without a reason, authorize further repair, or explicitly terminate. "
        "and report what actually ran. Seed/helper/QC-1 routine handling is your responsibility. "
        "Do not treat this ordinary message as a response to another native question or waive technical gates.\n\n"
        + text
    )
    try:
        admitted = topify_opencode.send_message(directory, runtime["session_id"], prompt)
    except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        _append(root, "supervisor_message_delivery_failed", message_id=message["message_id"],
                session_id=runtime["session_id"], status="unconfirmed")
        raise common.PipelineError("消息留存在台账，但无法确认 OpenCode 接收；没有执行证据，请勿盲目重发。") from exc
    receipt = {
        "schema_version": 1, "message_id": message["message_id"],
        "session_id": runtime["session_id"], "status": "admitted",
        "inbox_id": admitted.get("response", {}).get("data", {}).get("id"),
        "recorded_at": common.utc_now(), "execution_status": "awaiting-agent-action",
    }
    receipt["delivery_sha256"] = common.canonical_sha256(receipt)
    number, attempt = common.next_attempt(root / "supervision/session-deliveries")
    common.write_new_json(attempt / "delivery.json", receipt)
    _append(root, "supervisor_session_message_sent", **receipt)
    return {**receipt, "note": "已提交真实上司会话；请查看其回复与工具调用，接收不等于执行完成。"}, 0


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
    return sum(item.get("employment_status") in {"active", "running", "dispatch-created", "waiting-qc", "repair-required", "rollout-pending", "rollout-running", "awaiting-approval", "needs-owner-decision", "replacement-required", "hardening-r1-required", "hardening-r2-required"} for item in _employees(root))


@mutation
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
        if role not in CHILD_ROLES:
            raise common.PipelineError("only Seed/prototype workers belong to a producer tree; QC is independent")
    elif role in CHILD_ROLES:
        raise common.PipelineError("Seed/prototype workers require a producer parent")
    old = None
    if replacement_of is not None:
        old = _employee(root, replacement_of)
        if old.get("role") != "producer" or old.get("employment_status") != "fired":
            raise common.PipelineError("replacement_of must identify a fired producer")
        if seed_id is not None and seed_id == old.get("seed_id"):
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
    prompt = _regular_file(prompt_file, "prompt_file") if prompt_file is not None else None
    active = _active_count(root)
    state = "active" if active < manifest["max_concurrent"] else "queued"
    job_root = root / "supervision/agent-workspaces" / employee_id
    if job_root.exists() or job_root.is_symlink():
        raise common.PipelineError("employee job_root already exists")
    job_root.mkdir(mode=0o700)
    value = {
        "schema_version": 1,
        "employee_id": employee_id,
        "parent_id": parent,
        "role": role,
        "department": "quality" if role == "qc" else "rollout" if role == "rollout" else "production",
        "owns_complete_task": role == "producer",
        "phase": "seed-discovery" if role == "producer" and seed_id is None else "seed-review" if role == "producer" else "assigned",
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
    assignment_dir = root / "supervision/assignments"
    assignment_dir.mkdir(exist_ok=True)
    assignment = assignment_dir / f"{employee_id}.md"
    assignment.write_text(_employment_prompt(root, manifest, value) +
                          ("\n\n## Additional work order\n" + prompt.read_text(encoding="utf-8") if prompt else ""),
                          encoding="utf-8")
    value["prompt_path"] = str(assignment)
    value["prompt_sha256"] = common.sha256_file(assignment)
    value["employment_contract_sha256"] = common.canonical_sha256(value)
    common.write_new_json(path, value)
    _append(root, "employee_hired", employee_id=employee_id, role=role,
            parent_id=parent, employment_status=state,
            replacement_of=value["replacement_of"])
    return value, 0


def _employment_prompt(root: Path, manifest: dict, employee: dict) -> str:
    bundle = Path(__file__).resolve().parents[2]
    identity = (f"# Employee {employee['employee_id']}\n"
                f"Role: {employee['role']}\nLogical parent: {employee['parent_id']}\n"
                f"Campaign: {root}\nExclusive writable job_root: {employee['job_root']}\n"
                f"Benchmark: {manifest.get('benchmark')}\n"
                f"Read the production Skill: {bundle / '.opencode/skills/edgebench-task-production/SKILL.md'}\n"
                f"Read the locked contract: {root / 'requirements-profile-lock.json'} (if present).\n"
                "Use absolute paths to your job_root for tool working directories and all staged writes. "
                "Shared campaign, dedup registry and other employees are read-only. "
                "The supervisor alone collects proposals and writes shared state.\n")
    if employee["role"] == "producer":
        return identity + (
            "You own exactly ONE complete task, not a scout job. Begin by discovering and pinning "
            "a Seed yourself. A pre-existing Seed pool is NOT a recruitment prerequisite. "
            "Stage a seed-card.json, immutable input bytes, learning-loop feasibility evidence and "
            "child-request proposals in your job_root. Ask the supervisor to dispatch bounded "
            "edgebench-seed-worker / edgebench-prototype-worker children for you only if useful. "
            "Do not launch a department-wide scout pool. The supervisor reviews and globally reserves "
            "your Seed before authoring. Stop at each approval gate.\n"
            "Retain this employee identity and this same OpenCode session (or Orca worker) across "
            "Seed discovery, r0, r1, r2, QC repairs, approved rollout and packaging preparation. "
            "Record stage-complete checkpoints, not terminal employee completion. "
                "After measured r0 the supervisor says 'too simple'; after r1 it requires a second distinct "
                "causal mechanism. Use new version workspaces and parent hashes; only r2 may enter QC. "
                "QC is independent. No rollout without current freeze and supervisor/owner authorization. "
                "While active, send a heartbeat at least every 120 seconds and before/after a long tool batch. "
                f"Write a JSON report under {employee['job_root']}/heartbeat.json with employee_id, status=heartbeat, "
                "summary, stage, revision_index and current_action/blocker, then run "
                f"`python3 {bundle / 'edgebench-task-generator/scripts/topify_pipeline.py'} employee-report {root} "
                f"--employee-id {employee['employee_id']} --input {employee['job_root']}/heartbeat.json`. "
                "A report is an active communication to the bound Supervisor; do not silently stop or leave only a completed receipt. "
                "After freeze never change scoring identity. Read Web inbox messages at checkpoints and "
            "reply in your own conversation. Return actual paths, hashes, status and blockers, "
            "never prose-only completion.\n"
        )
    if employee["role"] in CHILD_ROLES:
        return identity + (
            "You are bounded support for your named producer, not an independent task owner. "
            "Stage source verification or micro-prototype evidence only in your job_root; "
            "return to your producer. You cannot promote a Seed, approve QC or author a separate task.\n"
        )
    return identity + (
        "You are independent of the producer tree. Review the assigned candidate read-only and "
        "stage your QC/observation evidence only under your job_root. Do not edit the producer's "
        "task or approve rollout yourself.\n"
    )


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


def _report_summary(root: Path, employee: dict) -> dict | None:
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
        report_path = Path(path).resolve()
        if report_path.is_file() and not report_path.is_symlink() and report_path.is_relative_to(root):
            try:
                report = common.load_json(report_path)
                if _text(report.get("summary")):
                    value["summary"] = report["summary"][:2000]
                if _text(report.get("question")):
                    value["question"] = report["question"][:1000]
            except common.PipelineError:
                value["summary_status"] = "unreadable"
    return value


def _timeline(root: Path, employee_ids: set[str]) -> list[dict]:
    path = root / "supervision/events.jsonl"
    if not path.is_file() or path.is_symlink():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("employee_id") in employee_ids or event.get("producer_id") in employee_ids:
            rows.append({
                "event": event.get("event"),
                "recorded_at": event.get("recorded_at"),
                "status": event.get("status"),
                "round": event.get("round"),
                "dispatch_id": event.get("dispatch_id"),
                "message_id": event.get("message_id"),
                "decision": event.get("decision"),
            })
    return rows[-100:]


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
        tree = _tree(root, employee["employee_id"])
        tree_ids = {item["employee_id"] for item in tree}
        children = [item for item in tree if item["employee_id"] != employee["employee_id"]]
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
            "session_id": employee.get("session_id"),
            "phase": employee.get("phase"),
            "hardening_revision": employee.get("hardening_revision"),
            "execution_status": employee.get("execution_status"),
            "legacy_owner_form": employee.get("owner_form"),
            "report": _report_summary(root, employee),
            "qc": employee.get("last_qc_decision"),
            "qc_owner_review": _qc_owner_review(root, employee),
            "owner_qc_resolution": employee.get("owner_qc_resolution"),
            "rollout_gate": gate,
            "child_count": len(children),
            "children": [
                {"employee_id": child["employee_id"], "role": child.get("role"),
                 "status": child.get("employment_status"), "session_id": child.get("session_id"),
                 "phase": child.get("phase")}
                for child in children
            ],
            "timeline": _timeline(root, tree_ids),
        }
        if employee.get("employment_status") in {
            "needs-owner-decision", "replacement-required", "repair-required",
            "rollout-pending", "awaiting-approval",
        }:
            alerts.append({
                "kind": "employee-state",
                "employee_id": employee["employee_id"],
                "status": employee["employment_status"],
                "responsible": "supervisor",
                "report": row["report"],
                "owner_review": row["qc_owner_review"],
            })
        if gate is not None and gate.get("status") not in {"CONTINUE", "ACCEPT"}:
            alerts.append({
                "kind": "rollout-gate",
                "employee_id": employee["employee_id"],
                "status": gate.get("status"),
                "action": gate.get("action"),
            })
        # The Workforce view is one card per top-level employee. Child helpers
        # remain in the parent card's timeline and are never duplicated as cards.
        if employee.get("parent_id") == "supervisor" or employee_id is not None:
            rows.append(row)
    return {
        "schema_version": 1,
        "campaign": str(root),
        "generated_at": common.utc_now(),
        "supervisor_binding": {
            "session_id": manifest.get("supervisor_session_id"),
            "directory": manifest.get("supervisor_session_directory"),
            "bound_at": manifest.get("supervisor_session_bound_at"),
        },
        "capacity": {
            "active": _active_count(root),
            "max_concurrent": manifest["max_concurrent"],
            "available": max(0, manifest["max_concurrent"] - _active_count(root)),
        },
        "employees": rows,
        "alerts": alerts,
        "owner_action_required": bool(alerts),
    }, 0


@mutation
def fire(workspace: str | Path, employee_id: str, reason: str, evidence: str | Path | None = None) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    value = _employee(root, employee_id)
    if value.get("employment_status") == "fired":
        raise common.PipelineError("employee is already fired")
    if not _text(reason):
        raise common.PipelineError("termination reason must be nonempty")
    qc = value.get("last_qc_decision") or {}
    if value.get("role") == "producer" and qc.get("decision") in {"fail", "repair_required", "pass_with_exception"}:
        resolution = value.get("owner_qc_resolution") or {}
        bound = bool(qc.get("decision_sha256") and resolution.get("qc_decision_sha256") == qc["decision_sha256"])
        allowed = bound and (resolution.get("decision") == "fire" or (
            resolution.get("decision") == "accept_exception" and
            value.get("last_rollout_gate", {}).get("status") == "EARLY_TOO_EASY"
        ))
        if not allowed:
            raise common.PipelineError("QC 未通过不是自动淘汰条件。QC-1 先返修；QC-2 仍未通过必须交负责人，先记录负责人明确淘汰决定。")
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


@mutation
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
    if role not in CHILD_ROLES:
        raise common.PipelineError("producer children are Seed/prototype helpers, not QC or another task owner")
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


@mutation
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
    if prompt is None or common.sha256_file(prompt) != employee.get("prompt_sha256"):
        raise common.PipelineError("employee assignment is missing or changed; do not dispatch a stale prompt")
    task = {
        "dispatch_id": dispatch_id,
        "employee_id": employee_id,
        "role": employee["role"],
        "transport": employee["transport"],
        "task_id": employee.get("task_id"),
        "seed_id": employee.get("seed_id"),
        "phase": employee.get("phase", "assigned"),
        "hardening_revision": employee.get("requested_revision", employee.get("hardening_revision", 0)),
        "continue_session_id": employee.get("session_id"),
        "job_root": employee["job_root"],
        "prompt_path": prompt,
        "prompt_sha256": employee.get("prompt_sha256"),
        "contract_sha256": employee["employment_contract_sha256"],
        "execution_status": "awaiting_transport",
        "worker_contract": "send heartbeat while active; report completion exactly once; ask through supervisor gate",
        "open_code": {
            "action": "native-task-or-opencode-run",
            "agent": ROLE_AGENTS[employee["role"]],
            "argv_template": [
                "opencode", "run", "--agent",
                ROLE_AGENTS[employee["role"]],
                "--format", "json", "<signed-prompt-content>",
            ],
            "working_directory": employee["job_root"],
            "session_id": employee.get("session_id"),
            "lifecycle": "continue-same-employee-session; no-new-worker-per-stage",
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
    employee = {**employee, "employment_status": "dispatch-created", "last_dispatch_id": dispatch_id,
                "last_dispatch_path": str(path)}
    _save_employee(root, employee)
    _append(root, "employee_dispatched", employee_id=employee_id, dispatch_id=dispatch_id,
            transport=employee["transport"], dispatch_sha256=task["dispatch_sha256"])
    if output is not None:
        destination = Path(output)
        if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
            raise common.PipelineError("dispatch output must be a new file in an existing directory")
        destination.write_text(json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "prepared", "dispatch": task, "path": str(path), "manifest_modes": manifest["transport_modes"]}, 0


@mutation
def bind_session(workspace, employee_id, session_id, dispatch_id=None):
    import topify_opencode
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("employment_status") == "fired":
        raise common.PipelineError("cannot bind a fired employee")
    if employee.get("transport") != "opencode-task":
        raise common.PipelineError("session binding currently uses the OpenCode API")
    if not topify_opencode.SESSION_ID.fullmatch(session_id):
        raise common.PipelineError("invalid OpenCode session ID")
    actual_dispatch = dispatch_id or employee.get("last_dispatch_id")
    if actual_dispatch is None or actual_dispatch != employee.get("last_dispatch_id"):
        raise common.PipelineError("bind the current prepared dispatch, not a stale attempt")
    if employee.get("session_id") not in {None, session_id}:
        raise common.PipelineError("employee already has a persistent session; continue it instead of replacing it")
    if any(e.get("session_id") == session_id and e["employee_id"] != employee_id for e in _employees(root)):
        raise common.PipelineError("session already belongs to another employee")
    info = topify_opencode._get(f"/api/session/{session_id}").get("data", {})
    if info.get("id") != session_id or info.get("agent") != ROLE_AGENTS[employee["role"]]:
        raise common.PipelineError("actual session identity/agent does not match the employee role")
    directory = info.get("location", {}).get("directory")
    if directory is None or Path(directory).resolve() != Path(employee["job_root"]).resolve():
        raise common.PipelineError("move the employee session into its exclusive job_root before binding")
    receipt = {"employee_id": employee_id, "session_id": session_id,
               "runtime_parent_id": info.get("parentID"), "dispatch_id": actual_dispatch,
               "directory": directory, "bound_at": common.utc_now()}
    _save_employee(root, {**employee, "session_id": session_id, "session_binding": receipt})
    _append(root, "employee_session_bound", **receipt)
    return receipt, 0


@mutation
def bind_seed(workspace, employee_id, seed_id, screen_receipt):
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee["role"] != "producer" or employee.get("employment_status") == "fired":
        raise common.PipelineError("Seed binding needs a live producer")
    if employee.get("seed_id") not in {None, seed_id} or employee.get("hardening_revision") is not None:
        raise common.PipelineError("cannot silently change a producer's authored Seed")
    topify_seed._safe_name(seed_id)
    path, receipt = _json_file(screen_receipt, "Seed screen receipt")
    if not path.is_relative_to(root) or receipt.get("seed_id") != seed_id:
        raise common.PipelineError("Seed screen receipt must belong to this campaign and Seed")
    if receipt.get("verdict") not in {"complete", "prototype-candidate"}:
        raise common.PipelineError("Seed technical screen did not pass")
    required = {"seed.source_identity", "seed.real_work", "seed.learning_loop", "seed.feasibility"}
    passed = {row.get("id") for row in receipt.get("checks", []) if row.get("status") == "complete"}
    if not required <= passed:
        raise common.PipelineError("Seed technical checks are incomplete")
    card, current_hash = topify_seed._current_seed(root, seed_id)
    if receipt.get("seed_sha256") != current_hash:
        raise common.PipelineError("Seed bytes changed since its technical screen")
    if employee.get("replacement_of"):
        former = _employee(root, employee["replacement_of"])
        if former.get("seed_id") == seed_id:
            raise common.PipelineError("replacement must use a new Seed")
    claim = employee.get("dedup_claim")
    if claim is None:
        number, attempt = common.next_attempt(root / "supervision/seed-bindings" / employee_id)
        card_path = attempt / "seed-card.json"
        common.write_new_json(card_path, card)
        claim, code = topify_dedup.claim(manifest["dedup_root"], card_path, employee_id,
                                        campaign["config"]["campaign_id"], seed_id)
        if code:
            raise common.PipelineError("Seed dedup collision; request another Seed")
    evidence = {"seed_id": seed_id, "seed_sha256": current_hash,
                "screen_receipt": str(path), "screen_sha256": common.sha256_file(path)}
    _save_employee(root, {**employee, "seed_id": seed_id, "dedup_claim": claim,
                         "seed_binding": evidence, "phase": "authoring", "employment_status": "active"})
    _append(root, "producer_seed_bound", employee_id=employee_id, **evidence)
    return evidence, 0


@mutation
def report(workspace: str | Path, employee_id: str, report_file: str | Path) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    if employee.get("employment_status") == "fired":
        raise common.PipelineError("a fired employee cannot resume itself with a report")
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
    stage = value.get("stage", "candidate")
    seed_proposal = employee["role"] == "producer" and stage == "seed-proposal"
    candidate_complete = employee["role"] == "producer" and status_value == "completed" and not seed_proposal
    if candidate_complete:
        if not employee.get("seed_id") or not employee.get("dedup_claim"):
            raise common.PipelineError("bind the producer's screened/reserved Seed before reporting a task")
        previous = employee.get("hardening_revision")
        if previous is None and revision != 0:
            raise common.PipelineError("initial task completion must be r0, not r2")
        if previous is not None and revision != previous:
            if revision != previous + 1 or employee.get("requested_revision") != revision:
                raise common.PipelineError("new revision needs the matching supervisor hardening challenge")
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
    if status_value == "heartbeat":
        state = employee.get("employment_status")
        if state in {"active", "running", "dispatch-created"}:
            state = "running"
    else:
        state = {
            "completed": (
                "awaiting-approval" if seed_proposal else
                "hardening-r1-required" if employee.get("role") == "producer" and revision == 0
                else "hardening-r2-required" if employee.get("role") == "producer" and revision == 1
                else "awaiting-approval"
            ),
            "blocked": "needs-owner-decision",
            "question": "needs-owner-decision",
            "failed": "needs-owner-decision",
        }[status_value]
    if _unresolved_qc_failure(employee, second_round_only=True):
        # A heartbeat or another r2 completion is not the owner's decision.
        state = "needs-owner-decision"
    updated_employee = {
        **employee,
        "employment_status": state,
        "last_report": receipt,
        **({"hardening_revision": revision, "phase": "authoring" if revision < 2 else "qc-review"}
           if candidate_complete else {"phase": "seed-review"} if seed_proposal else {}),
    }
    delivery = None
    if _should_deliver_report(employee, status_value, datetime.now(timezone.utc).timestamp()):
        delivery_text = (
            f"Employee {employee_id} report: status={status_value}, phase={updated_employee.get('phase')}, "
            f"revision={revision}, report_sha256={receipt['report_sha256']}.\n"
            f"Report path: {receipt['report_path']}\n"
            f"Summary: {value.get('summary') or 'heartbeat; no summary provided'}\n"
            "This is a subordinate checkpoint. Acknowledge it, update the stage plan or ask the employee for a blocker. "
            "Do not treat a completed report as terminal employee completion; preserve the same employee/session."
        )
        delivery = _deliver_to_supervisor(root, delivery_text, "employee", employee_id,
                                          subject=f"employee-report:{employee_id}")
        updated_employee["last_supervisor_delivery"] = delivery
    _save_employee(root, updated_employee)
    _append(root, "employee_reported", employee_id=employee_id, report_status=status_value,
            report_sha256=receipt["report_sha256"],
            supervisor_delivery_status=delivery.get("status") if delivery else "throttled")
    return {**receipt, "supervisor_delivery": delivery}, 0


@mutation
def watch(workspace: str | Path, stale_after_seconds: int = HEARTBEAT_STALE_AFTER_SECONDS,
          active: dict | None = None) -> tuple[dict, int]:
    """Detect silent/stopped bound workers and notify the real Supervisor.

    This is an observation/escalation loop. It never fires, resumes or kills a
    worker. A stable incident key prevents repeated identical alerts until the
    worker emits a new report or its runtime condition changes.
    """
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    if type(stale_after_seconds) is not int or stale_after_seconds < 30:
        raise common.PipelineError("stale_after_seconds must be at least 30 seconds")
    if active is None:
        try:
            active = topify_opencode._get("/api/session/active").get("data", {})
        except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            return {"status": "runtime-unavailable", "reason": str(exc), "incidents": []}, 2
    if not isinstance(active, dict):
        active = {}
    incidents = []
    watch_root = root / "supervision/watch"
    watch_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).timestamp()
    for employee in _employees(root):
        if employee.get("employment_status") not in WORKER_MONITORED_STATES:
            continue
        runtime = topify_opencode.employee_runtime(employee, active)
        health = employee_health(employee, runtime, now, stale_after_seconds)
        if not health.get("attention"):
            continue
        report = employee.get("last_report") or {}
        incident_key = common.canonical_sha256({
            "employee_id": employee["employee_id"],
            "session_id": employee.get("session_id"),
            "health": health["status"],
            "report_sha256": report.get("report_sha256"),
        })
        pointer = watch_root / f"{employee['employee_id']}.json"
        previous = common.load_json(pointer) if pointer.is_file() and not pointer.is_symlink() else {}
        if previous.get("incident_key") == incident_key and previous.get("status") == "admitted":
            continue
        incident = {
            "schema_version": 1, "incident_key": incident_key,
            "employee_id": employee["employee_id"], "parent_id": employee.get("parent_id"),
            "employment_status": employee.get("employment_status"),
            "phase": employee.get("phase"), "session_id": employee.get("session_id"),
            "runtime": runtime, "health": health,
            "last_report": report, "detected_at": common.utc_now(),
            "status": "detected", "automatic_fire_allowed": False,
        }
        common.write_latest_json(pointer, incident)
        incidents.append(incident)
    if not incidents:
        return {"status": "ok", "incident_count": 0, "incidents": []}, 0
    lines = [
        "Worker watchdog detected employees that stopped reporting or have no bound session.",
        "This is an escalation, not a fire command. Inspect the actual session and contact/resume the worker; "
        "do not terminate from this alert.",
    ]
    for incident in incidents:
        health = incident["health"]
        lines.append(
            f"- {incident['employee_id']} parent={incident.get('parent_id')} phase={incident.get('phase')} "
            f"health={health['status']} runtime={health.get('session_status')} "
            f"age_seconds={health.get('age_seconds')} last_report={health.get('last_report_at')} "
            f"report_sha256={(incident.get('last_report') or {}).get('report_sha256')} "
            f"reason={health.get('reason')}"
        )
    delivery = _deliver_to_supervisor(root, "\n".join(lines), "watchdog", "supervisor-watchdog",
                                      subject="stalled-worker-watch")
    for incident in incidents:
        incident["status"] = delivery.get("status", "unavailable")
        incident["delivery"] = delivery
        common.write_latest_json(watch_root / f"{incident['employee_id']}.json", incident)
        _append(root, "employee_watch_alert", employee_id=incident["employee_id"],
                incident_key=incident["incident_key"], health=incident["health"]["status"],
                delivery_status=delivery.get("status"), automatic_fire_allowed=False)
    return {"status": delivery.get("status", "unavailable"), "incident_count": len(incidents),
            "incidents": incidents, "delivery": delivery}, 0 if delivery.get("status") == "admitted" else 2


def _message_target(root: Path, target_type: str, target_id: str) -> Path:
    if target_type not in {"supervisor", "employee"}:
        raise common.PipelineError("message target_type must be supervisor or employee")
    _id(target_id, "target_id")
    directory = root / "supervision/messages" / f"{target_type}--{target_id}"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise common.PipelineError("message target directory is unsafe")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@mutation
def _persist_message(
    workspace: str | Path,
    target_type: str,
    target_id: str,
    text: str,
    sender_type: str = "owner",
    sender_id: str = "owner",
    reply_to: str | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    if sender_type not in {"owner", "supervisor", "employee", "watchdog"}:
        raise common.PipelineError("message sender_type is invalid")
    if not _text(text) or len(text) > 20_000:
        raise common.PipelineError("message text must be nonempty and at most 20000 characters")
    if sender_type != "owner":
        _id(sender_id, "sender_id")
    if target_type == "employee":
        _employee(root, target_id)
    elif target_type == "supervisor":
        manifest = common.load_json(root / "supervision/manifest.json")
        supervisor_id = manifest.get("supervisor_id", "supervisor")
        if target_id != supervisor_id and target_id != "supervisor":
            raise common.PipelineError("message supervisor target does not match campaign supervisor")
    else:
        raise common.PipelineError("message target_type is invalid")
    if reply_to is not None:
        _id(reply_to, "reply_to")
    directory = _message_target(root, target_type, target_id)
    number, attempt = common.next_attempt(directory)
    message = {
        "schema_version": 1,
        "message_id": f"{target_type}-{target_id}-m{number:04d}",
        "target_type": target_type,
        "target_id": target_id,
        "sender_type": sender_type,
        "sender_id": sender_id,
        "text": text.strip(),
        "reply_to": reply_to,
        "status": "queued",
        "created_at": common.utc_now(),
    }
    message["message_sha256"] = common.canonical_sha256(message)
    common.write_new_json(attempt / "message.json", message)
    _append(root, "message_queued", message_id=message["message_id"],
            target_type=target_type, target_id=target_id,
            sender_type=sender_type, sender_id=sender_id,
            message_sha256=message["message_sha256"])
    return message, 0


@mutation
def send_message(
    workspace: str | Path, target_type: str, target_id: str, text: str,
    sender_type: str = "owner", sender_id: str = "owner", reply_to: str | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    if sender_type == "employee":
        employee = _employee(root, sender_id)
        if employee.get("employment_status") == "fired":
            raise common.PipelineError("a fired employee cannot resume communication")
    message, code = _persist_message(root, target_type, target_id, text, sender_type, sender_id, reply_to)
    if target_type == "supervisor" and sender_type == "employee":
        delivery = _deliver_to_supervisor(root, text, sender_type, sender_id,
                                          subject=f"employee-message:{sender_id}", message=message)
        return {**message, "supervisor_delivery": delivery}, code
    if target_type == "employee" and sender_type == "supervisor":
        employee = _employee(root, target_id)
        runtime = topify_opencode.employee_runtime(employee)
        if not runtime.get("can_chat"):
            delivery = {"status": "unavailable", "session_id": runtime.get("session_id"),
                        "reason": runtime.get("reason", "员工实际会话不可用。")}
        else:
            try:
                admitted = topify_opencode.send_message(
                    employee["job_root"], runtime["session_id"],
                    f"Supervisor directive from {sender_id}; ledger message {message['message_id']}.\n"
                    "Acknowledge this directive in your actual session, report what you did and send the next heartbeat.\n\n"
                    + text
                )
                delivery = {"status": "admitted", "session_id": runtime["session_id"],
                            "inbox_id": admitted["response"]["data"]["id"],
                            "execution_status": "awaiting-agent-action"}
            except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
                delivery = {"status": "unconfirmed", "session_id": runtime.get("session_id"),
                            "reason": str(exc)}
        _append(root, "supervisor_employee_directive", employee_id=target_id,
                message_id=message["message_id"], delivery_status=delivery["status"],
                session_id=delivery.get("session_id"))
        return {**message, "employee_delivery": delivery}, code
    return message, code


def list_messages(
    workspace: str | Path, target_type: str, target_id: str, limit: int = 100
) -> tuple[list[dict], int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise common.PipelineError("message limit must be between 1 and 500")
    directory = _message_target(root, target_type, target_id)
    values = []
    for path in sorted(directory.glob("attempt-*/message.json"))[-limit:]:
        values.append(common.load_json(path))
    return values, 0


@mutation
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
    _save_employee(root, {**producer, "employment_status": "repair-required", "last_hardening_challenge": challenge,
                         "requested_revision": round_number})
    _append(root, "hardening_challenge_recorded", producer_id=producer_id,
            round=round_number, verdict=challenge["verdict"],
            challenge_sha256=challenge["challenge_sha256"])
    return challenge, 0


@mutation
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


@mutation
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


@mutation
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
    if type(round_number) is not int or round_number < 1:
        raise common.PipelineError("QC round must be a positive integer")
    previous = producer.get("last_qc_decision") or {}
    previous_round = previous.get("round", 0)
    if round_number != previous_round + 1:
        raise common.PipelineError("QC 轮次必须连续；不能重置轮次或覆盖 QC-2 失败来绕过负责人。")
    if round_number > 2:
        authorization = producer.get("owner_qc_resolution") or {}
        if not (authorization.get("decision") == "continue_repair"
                and authorization.get("next_qc_round") == round_number
                and authorization.get("qc_decision_sha256") == previous.get("decision_sha256")):
            raise common.PipelineError("超过默认两轮 QC 必须由负责人明确批准追加返修/复检。")
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
    _save_employee(root, {**producer, "employment_status": next_state, "last_qc_decision": receipt,
                          "owner_review_required": round_number >= 2 and decision != "pass"})
    _append(root, "qc_decision_recorded", producer_id=producer_id, qc_employee_id=qc_id,
            round=round_number, decision=decision, severity=severity,
             next_state=next_state, decision_sha256=receipt["decision_sha256"])
    if round_number >= 2 and decision != "pass":
        _append(root, "qc_owner_review_required", employee_id=producer_id, round=round_number,
                qc_decision_sha256=receipt["decision_sha256"], automatic_fire_allowed=False)
    return {**receipt, "next_employee_state": next_state}, 0


@mutation
def qc_resolve(
    workspace: str | Path,
    producer_id: str,
    decision: str,
    note: str = "",
    evidence: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    if decision not in QC_DECISIONS:
        raise common.PipelineError(f"decision must be one of {sorted(QC_DECISIONS)}")
    if decision in {"fire", "request_user"} and not _text(note):
        raise common.PipelineError("淘汰或上报审核需要具体问题说明；接受例外和继续返修可以不填原因。")
    note = note.strip() if isinstance(note, str) else ""
    producer = _employee(root, producer_id)
    if producer.get("employment_status") != "needs-owner-decision" and not (
        decision == "fire" and producer.get("employment_status") in {"repair-required", "rollout-pending", "rollout-authorized"}
    ):
        raise common.PipelineError("producer is not waiting for an owner QC decision")
    qc = producer.get("last_qc_decision") or {}
    if producer.get("role") != "producer" or not qc or qc.get("decision") == "pass":
        raise common.PipelineError("QC resolution needs a specific unresolved independent QC finding")
    evidence_path = _regular_file(evidence, "QC resolution evidence") if evidence else None
    owner_authorization = _qc_owner_authorization(root, producer, decision, evidence_path) if decision != "request_user" else None
    if decision == "accept_exception":
        report = _regular_file(qc.get("report_path"), "current QC report")
        if common.sha256_file(report) != qc.get("report_sha256"):
            raise common.PipelineError("QC 报告已变更；不能把旧例外批准套用到新证据。")
    resolution = {
        "schema_version": 1,
        "producer_id": producer_id,
        "decision": decision,
        "note": note,
        "authority": "supervisor-escalation" if decision == "request_user" else "owner",
        "owner_authorization": owner_authorization,
        "qc_decision_sha256": qc.get("decision_sha256"),
        "qc_round": qc.get("round"),
        "qc_report_sha256": qc.get("report_sha256"),
        "next_qc_round": qc.get("round", 0) + 1 if decision == "continue_repair" else None,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_sha256": common.sha256_file(evidence_path) if evidence_path else None,
        "recorded_at": common.utc_now(),
    }
    resolution["resolution_sha256"] = common.canonical_sha256(resolution)
    number, attempt = common.next_attempt(root / "supervision/decisions" / producer_id)
    resolution["attempt"] = number
    common.write_new_json(attempt / "owner-qc-resolution.json", resolution)
    if decision == "fire":
        _save_employee(root, {**producer, "owner_qc_resolution": resolution, "owner_review_required": False})
        value, _ = fire(root, producer_id, f"owner QC resolution: {note}", evidence_path)
    elif decision == "accept_exception":
        value = {**producer, "employment_status": "rollout-pending", "qc_exception": resolution,
                 "owner_qc_resolution": resolution, "owner_review_required": False}
        _save_employee(root, value)
    elif decision == "continue_repair":
        value = {**producer, "employment_status": "repair-required", "owner_qc_resolution": resolution,
                 "owner_review_required": False}
        _save_employee(root, value)
    else:
        value = {**producer, "employment_status": "needs-owner-decision", "qc_escalation": resolution}
        _save_employee(root, value)
    _append(root, "qc_resolution_recorded", producer_id=producer_id, decision=decision,
            resolution_sha256=resolution["resolution_sha256"])
    return {"resolution": resolution, "employee": value}, 0


@mutation
def owner_decision(
    workspace: str | Path,
    employee_id: str,
    decision: str,
    note: str,
    evidence: str | Path | None = None,
) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    if decision not in {"approve_rollout", "reject_rollout", "accept_exception", "fire"}:
        raise common.PipelineError("unknown owner decision")
    if decision in {"reject_rollout", "fire"} and not _text(note):
        raise common.PipelineError("退回或淘汰必须填写说明")
    note = note.strip() if isinstance(note, str) else ""
    if not note:
        note = "Owner approved without additional note."
    employee = _employee(root, employee_id)
    qc_failure = employee.get("role") == "producer" and (employee.get("last_qc_decision") or {}).get("decision") in {"fail", "repair_required", "pass_with_exception"}
    if qc_failure and decision in {"accept_exception", "fire"}:
        return qc_resolve(root, employee_id, decision, note, evidence)
    evidence_path = _regular_file(evidence, "owner decision evidence") if evidence else None
    allowed = {
        "approve_rollout": {"rollout-pending"},
        "reject_rollout": {"rollout-pending"},
        "accept_exception": {"needs-owner-decision"},
        "fire": {"needs-owner-decision", "replacement-required", "repair-required"},
    }
    if employee.get("employment_status") not in allowed[decision]:
        raise common.PipelineError(
            f"decision {decision} is not valid for employee state {employee.get('employment_status')}"
        )
    if decision in {"approve_rollout", "reject_rollout", "accept_exception"}:
        if employee.get("role") != "producer":
            raise common.PipelineError("子任务或质检员不是 rollout 批准对象，应由上司处理其阶段报告。")
        qc = employee.get("last_qc_decision") or {}
        if decision == "approve_rollout" and (
            employee.get("hardening_revision") != 2 or
            (qc.get("decision") != "pass" and not employee.get("qc_exception"))
        ):
            raise common.PipelineError("先完成 r2 和独立 QC；通用接受选项不能代替技术门槛。")
        if decision == "accept_exception" and not qc:
            raise common.PipelineError("没有具体 QC 例外凭证；普通阻塞不能直接转为 rollout-pending。")
    receipt = {
        "schema_version": 1,
        "employee_id": employee_id,
        "decision": decision,
        "note": note,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_sha256": common.sha256_file(evidence_path) if evidence_path else None,
        "recorded_at": common.utc_now(),
    }
    receipt["decision_sha256"] = common.canonical_sha256(receipt)
    number, attempt = common.next_attempt(root / "supervision/decisions" / employee_id)
    receipt["attempt"] = number
    common.write_new_json(attempt / "owner-decision.json", receipt)
    if decision == "fire":
        result, _ = fire(root, employee_id, f"Owner decision: {note}", evidence_path)
        employee_result = result
    else:
        next_state = "rollout-authorized" if decision == "approve_rollout" else (
            "replacement-required" if decision == "reject_rollout" else "rollout-pending"
        )
        employee_result = {**employee, "employment_status": next_state, "owner_decision": receipt}
        _save_employee(root, employee_result)
    _append(root, "owner_decision_recorded", employee_id=employee_id,
            decision=decision, decision_sha256=receipt["decision_sha256"])
    return {"decision": receipt, "employee": employee_result}, 0


@mutation
def create_owner_form(workspace: str | Path, employee_id: str, note: str = "") -> tuple[dict, int]:
    """Compatibility name: request a real supervisor review, not an API Form."""
    root, campaign = _root(workspace)
    manifest = _load_manifest(root, campaign)
    if manifest.get("supervisor_session_id") and os.environ.get("OPENCODE_SESSION_ID") == manifest["supervisor_session_id"]:
        raise common.PipelineError("你就是绑定的上司：请直接调用原生 question 工具并等待答案，不要给自己递归发送审查请求。")
    employee = _employee(root, employee_id)
    if employee.get("employment_status") in {"fired", "complete"}:
        raise common.PipelineError("该员工已结束，不能请求新的生产审批。")
    report = _report_summary(root, employee) or {}
    text = (
        f"请实际审查员工 {employee_id}，不要创建通用接受/淘汰表单。\n"
        f"当前角色/阶段/台账状态：{employee.get('role')} / {employee.get('phase')} / {employee.get('employment_status')}\n"
        f"报告：{report.get('report_path')}\n摘要：{report.get('summary', '未报告')}\n"
        "常规子任务收尾、Seed 审核和 QC-1 修复由你处理并给出依据。"
        "只有具体的资源/政策或有效 rollout 门槛需要负责人时，才用原生 question 工具等待回答。"
        "询问必须说明证据、选项后果和下一项实际操作。没有当前冻结和全部技术凭证不得放行试跑。"
        "本次请求本身不批准 rollout、不接受 QC 例外、不淘汰员工。\n" + note
    )
    owner_review = _qc_owner_review(root, employee)
    if owner_review:
        preview = {key: owner_review.get(key) for key in (
            "round", "decision", "severity", "summary", "report_path", "report_sha256",
            "qc_decision_sha256", "report_integrity", "quality_checks", "notice"
        )}
        text += (
            "\n这不是常规阶段收尾：返修后的 QC-2 仍未通过，必须由负责人审核，不可自动 fired。"
            "读取下面身份绑定的完整 QC 报告，展示关闭项、剩余失败/缺证据项及具体依据和影响，"
            "用原生 question 工具等待负责人选择接受指定例外（原因可空）、追加返修/复检或明确淘汰。"
            "该 question 原文必须包含 producer_id 和完整 qc_decision_sha256，选项标签使用"
            "「接受指定例外」「继续返修复检」「淘汰员工子树」。Web 将保存绑定该 QC 凭证的回答回执。"
            "你随后以该 supervision/native-interactions 下的 reply.json 作为 qc-resolve --evidence，"
            "不得只用自己的说明声明 owner 已批准。"
            "未答复前保留员工/证据，不默认接受或放行试跑。\n" + json.dumps(preview, ensure_ascii=False)
        )
    return send_supervisor_message(root, text)


@mutation
def sync_owner_form_by_form(workspace: str | Path, form_id: str) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    employee_id = _owner_form_employee_id(root, form_id)
    if employee_id is not None:
        return sync_owner_form(root, employee_id)
    raise common.PipelineError("该原生问题没有绑定生产台账员工")


@mutation
def sync_owner_form(workspace: str | Path, employee_id: str) -> tuple[dict, int]:
    root, campaign = _root(workspace)
    _load_manifest(root, campaign)
    employee = _employee(root, employee_id)
    ref = employee.get("owner_form")
    if not isinstance(ref, dict) or not ref.get("form_id"):
        raise common.PipelineError("该员工没有原生审批问题")
    raise common.PipelineError(
        "旧 owner-form 是脱离 Agent 等待工具的表单，不能同步为生产批准。"
        "请请求真实上司审查；原生 question 回答由上司继续执行并记录具体动作。"
    )


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


@mutation
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


@mutation
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
