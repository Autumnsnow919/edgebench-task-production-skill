#!/usr/bin/env python3
"""Secretary mailbox for hierarchical production.

Workers report to this mailbox. The secretary coalesces those reports into one
evidence-linked digest before the digest is admitted to the bound Supervisor.
The mailbox is deliberately deterministic: it does not invent decisions,
change employee state, or replace the Supervisor's real OpenCode session.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import topify_common as common


DEFAULT_INTERVAL_SECONDS = 180
URGENT_STATUSES = frozenset({"blocked", "failed", "question"})
ARCHIVED_STATES = frozenset({"fired", "complete", "shelved", "abandoned"})


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _safe(root: Path) -> Path:
    directory = root / "supervision/secretary"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise common.PipelineError("secretary path is unsafe")
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("inbox", "batches"):
        path = directory / name
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise common.PipelineError(f"secretary/{name} path is unsafe")
        path.mkdir(exist_ok=True)
    return directory


def _load_latest(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        return {}
    return common.load_json(path)


def _write_latest(path: Path, value: dict) -> None:
    common.write_latest_json(path, value)


def ingest(root: Path, employee: dict, report: dict, receipt: dict) -> dict:
    """Put one worker report in the secretary mailbox without waking anyone."""
    directory = _safe(root)
    message = {
        "schema_version": 1,
        "message_id": f"{employee['employee_id']}-{receipt['attempt']:06d}-{receipt['report_sha256'][:12]}",
        "employee_id": employee["employee_id"],
        "parent_id": employee.get("parent_id"),
        "role": employee.get("role"),
        "task_id": employee.get("task_id"),
        "seed_id": employee.get("seed_id"),
        "status": receipt["report_status"],
        "revision": report.get("revision_index", 0),
        "stage": report.get("stage", employee.get("phase")),
        "summary": str(report.get("summary") or "heartbeat; no summary provided")[:4000],
        "report_path": receipt["report_path"],
        "report_sha256": receipt["report_sha256"],
        "recorded_at": receipt["recorded_at"],
        "received_at": common.utc_now(),
        "delivery": "secretary-mailbox",
    }
    message["message_sha256"] = common.canonical_sha256(message)
    path = directory / "inbox" / f"{message['message_id']}.json"
    if not path.exists() and not path.is_symlink():
        common.write_new_json(path, message)
    return message


def ingest_event(root: Path, sender_id: str, status: str, summary: str,
                 evidence: dict | None = None) -> dict:
    """Put a watchdog/adapter event in the same mailbox as worker reports."""
    directory = _safe(root)
    evidence = evidence or {}
    message = {
        "schema_version": 1,
        "message_id": f"{sender_id}-{int(time.time_ns())}",
        "employee_id": sender_id,
        "parent_id": "supervisor",
        "role": "watchdog",
        "status": status,
        "summary": summary[:4000],
        "evidence": evidence,
        "recorded_at": common.utc_now(),
        "received_at": common.utc_now(),
        "delivery": "secretary-mailbox",
    }
    message["message_sha256"] = common.canonical_sha256(message)
    path = directory / "inbox" / f"{message['message_id']}.json"
    common.write_new_json(path, message)
    return message


def _messages(root: Path) -> list[dict]:
    directory = _safe(root) / "inbox"
    rows = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise common.PipelineError("secretary inbox cannot contain symlinks")
        rows.append(common.load_json(path))
    return rows


def _consumed(root: Path) -> set[str]:
    value = _load_latest(_safe(root) / "consumed.json")
    ids = value.get("message_ids", [])
    return {item for item in ids if isinstance(item, str)} if isinstance(ids, list) else set()


def _employee_states(root: Path) -> dict[str, str]:
    result = {}
    directory = root / "supervision/employees"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if path.name == "latest.json" or path.is_symlink():
            continue
        try:
            value = common.load_json(path)
        except common.PipelineError:
            continue
        if isinstance(value.get("employee_id"), str):
            result[value["employee_id"]] = value.get("employment_status", "unknown")
    return result


def status(root: Path, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> dict:
    directory = _safe(root)
    messages = _messages(root)
    consumed = _consumed(root)
    pending = [item for item in messages if item.get("message_id") not in consumed]
    last = _load_latest(directory / "last-flush.json")
    return {
        "role": "secretary",
        "status": "ready",
        "interval_seconds": interval_seconds,
        "pending_count": len(pending),
        "pending_urgent_count": sum(item.get("status") in URGENT_STATUSES for item in pending),
        "received_count": len(messages),
        "consumed_count": len(consumed),
        "last_flush": last or None,
        "mailbox": str(directory / "inbox"),
    }


def flush(root: Path, deliver, interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
          force: bool = False) -> dict:
    """Coalesce pending reports and call ``deliver(text, sender, subject)`` once."""
    if type(interval_seconds) is not int or interval_seconds < 30:
        raise common.PipelineError("secretary interval must be at least 30 seconds")
    directory = _safe(root)
    mailbox = _messages(root)
    consumed = _consumed(root)
    pending = [item for item in mailbox if item.get("message_id") not in consumed]
    if not pending:
        return {**status(root, interval_seconds), "status": "idle"}
    last = _load_latest(directory / "last-flush.json")
    elapsed = _now() - float(last.get("flushed_at_epoch", 0) or 0)
    urgent = any(item.get("status") in URGENT_STATUSES for item in pending)
    if not force and not urgent and elapsed < interval_seconds:
        return {**status(root, interval_seconds), "status": "throttled",
                "next_flush_in_seconds": max(0, int(interval_seconds - elapsed))}

    states = _employee_states(root)
    active = [item for item in pending if states.get(item.get("employee_id")) not in ARCHIVED_STATES]
    archived = [item for item in pending if item not in active]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in active:
        grouped[item.get("employee_id", "unknown")].append(item)
    lines = [
        "Secretary digest: worker reports were collected and coalesced; this is one batched management update.",
        f"Collected={len(pending)} active={len(active)} archived_or_discarded={len(archived)}.",
        "Do not wake or message workers one-by-one. Handle only the active items below, using their evidence paths and hashes.",
    ]
    for employee_id in sorted(grouped):
        reports = grouped[employee_id]
        latest = reports[-1]
        statuses = ", ".join(sorted({str(item.get("status")) for item in reports}))
        lines.append(
            f"- employee={employee_id} task={latest.get('task_id')} reports={len(reports)} "
            f"statuses={statuses} latest={latest.get('recorded_at')} "
            f"report={latest.get('report_path')} sha256={latest.get('report_sha256')}"
        )
        lines.append(f"  summary: {latest.get('summary', '')}")
    if archived:
        lines.append(f"- {len(archived)} report(s) belong to discarded/completed employees and were retained only in the audit mailbox; do not reopen them.")
    digest = {
        "schema_version": 1,
        "status": "prepared",
        "message_ids": [item.get("message_id") for item in pending],
        "active_message_ids": [item.get("message_id") for item in active],
        "discarded_message_ids": [item.get("message_id") for item in archived],
        "employee_count": len(grouped),
        "report_count": len(pending),
        "urgent": urgent,
        "created_at": common.utc_now(),
        "body": "\n".join(lines),
    }
    digest["digest_sha256"] = common.canonical_sha256(digest)
    number, attempt = common.next_attempt(directory / "batches")
    common.write_new_json(attempt / "digest.json", digest)
    delivery = deliver(digest["body"], "secretary", "secretary-digest")
    receipt = {"status": delivery.get("status", "unconfirmed"), "batch": number,
               "digest_sha256": digest["digest_sha256"], "message_ids": digest["message_ids"],
               "active_message_ids": digest["active_message_ids"], "delivery": delivery,
               "flushed_at": common.utc_now(), "flushed_at_epoch": _now()}
    common.write_new_json(attempt / "delivery.json", receipt)
    if receipt["status"] == "admitted":
        consumed.update(item.get("message_id") for item in pending if item.get("message_id"))
        _write_latest(directory / "consumed.json", {"schema_version": 1,
                     "message_ids": sorted(consumed), "updated_at": common.utc_now()})
    _write_latest(directory / "last-flush.json", receipt)
    return {**status(root, interval_seconds), "status": receipt["status"],
            "batch": number, "digest_sha256": digest["digest_sha256"], "delivery": delivery}
