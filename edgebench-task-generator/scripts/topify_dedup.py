#!/usr/bin/env python3
"""Content-addressed Seed/task deduplication claims.

Claims are intentionally append-only and can be shared by multiple supervisor
campaigns. A collision blocks a new producer rather than silently letting two
workers author the same semantic task.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re

import topify_common as common


SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def ensure_root(root: str | Path) -> Path:
    path = Path(root).absolute()
    if path.is_symlink() or path.exists() and not path.is_dir():
        raise common.PipelineError("dedup root is unsafe")
    path.mkdir(parents=True, exist_ok=True)
    for name in ("claims", "tasks"):
        child = path / name
        if child.is_symlink() or child.exists() and not child.is_dir():
            raise common.PipelineError(f"dedup/{name} is unsafe")
        child.mkdir(exist_ok=True)
    lock = path / ".lock"
    if lock.is_symlink() or lock.exists() and not lock.is_file():
        raise common.PipelineError("dedup lock is unsafe")
    lock.touch(exist_ok=True)
    return path


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return " ".join(value.lower().split())
    if isinstance(value, list):
        return sorted(_normalize(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    return value


def seed_fingerprint(value: dict) -> tuple[dict, str]:
    card = value.get("seed_card") if isinstance(value.get("seed_card"), dict) else value
    dedup = card.get("dedup") if isinstance(card.get("dedup"), dict) else {}
    hypothesis = card.get("task_hypothesis") if isinstance(card.get("task_hypothesis"), dict) else {}
    origins = card.get("origins") if isinstance(card.get("origins"), list) else []
    lineage = []
    for origin in origins:
        if isinstance(origin, dict):
            lineage.append({
                "kind": origin.get("kind"),
                "project": origin.get("project"),
                "revision": origin.get("revision"),
                "artifact_sha256": origin.get("artifact_sha256"),
            })
    payload = {
        "upstream_ids": dedup.get("upstream_ids", []),
        "dataset_ids": dedup.get("dataset_ids", []),
        "objective_signature": dedup.get("objective_signature", ""),
        "lineage": lineage,
        "snapshot_paths": hypothesis.get("snapshot_paths", []),
        "raw_metric": hypothesis.get("raw_metric", ""),
        "direction": hypothesis.get("direction", ""),
        "offline_verifier_plan": hypothesis.get("offline_verifier_plan", ""),
        "artifact_type": hypothesis.get("artifact_type", ""),
    }
    normalized = _normalize(payload)
    if (not payload["upstream_ids"] and not payload["dataset_ids"]
            and not _text(payload["objective_signature"])
            and not lineage):
        raise common.PipelineError("Seed fingerprint needs source lineage or objective signature")
    return normalized, common.canonical_sha256(normalized)


def _append(root: Path, event: str, **fields: object) -> None:
    record = {"schema_version": 1, "event": event, "recorded_at": common.utc_now(), **fields}
    record["event_sha256"] = common.canonical_sha256(record)
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def claim(
    root: str | Path,
    fingerprint_file: str | Path,
    employee_id: str,
    benchmark: str,
    seed_id: str,
) -> tuple[dict, int]:
    dedup_root = ensure_root(root)
    if not SLUG.fullmatch(employee_id) or not _text(benchmark) or not _text(seed_id):
        raise common.PipelineError("dedup claim identity is invalid")
    source = Path(fingerprint_file)
    if source.is_symlink() or not source.is_file():
        raise common.PipelineError("fingerprint file must be a regular file")
    value = common.load_json(source)
    payload, fingerprint = seed_fingerprint(value)
    path = dedup_root / "claims" / f"{fingerprint}.json"
    lock_path = dedup_root / ".lock"
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.exists() or path.is_symlink():
                existing = common.load_json(path)
                return {
                    "status": "collision",
                    "fingerprint": fingerprint,
                    "existing": existing,
                    "reason": "semantic Seed fingerprint is already claimed",
                }, 2
            record = {
                "schema_version": 1,
                "fingerprint": fingerprint,
                "fingerprint_payload": payload,
                "employee_id": employee_id,
                "benchmark": benchmark,
                "seed_id": seed_id,
                "source_path": str(source.resolve()),
                "source_sha256": common.sha256_file(source),
                "status": "reserved",
                "claimed_at": common.utc_now(),
            }
            record["claim_sha256"] = common.canonical_sha256(record)
            common.write_new_json(path, record)
            _append(dedup_root, "seed_claimed", fingerprint=fingerprint,
                    employee_id=employee_id, benchmark=benchmark, seed_id=seed_id,
                    claim_sha256=record["claim_sha256"])
            return record, 0
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def register_task(root: str | Path, task_sha256: str, employee_id: str, benchmark: str) -> tuple[dict, int]:
    dedup_root = ensure_root(root)
    if not common.HEX64.fullmatch(task_sha256) or not SLUG.fullmatch(employee_id) or not _text(benchmark):
        raise common.PipelineError("task dedup identity is invalid")
    path = dedup_root / "tasks" / f"{task_sha256}.json"
    lock_path = dedup_root / ".lock"
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.exists() or path.is_symlink():
                existing = common.load_json(path)
                if existing.get("employee_id") == employee_id:
                    return {**existing, "status": "already_registered"}, 0
                return {"status": "collision", "existing": existing, "task_sha256": task_sha256}, 2
            record = {
                "schema_version": 1,
                "task_sha256": task_sha256,
                "employee_id": employee_id,
                "benchmark": benchmark,
                "status": "registered",
                "registered_at": common.utc_now(),
            }
            record["registration_sha256"] = common.canonical_sha256(record)
            common.write_new_json(path, record)
            _append(dedup_root, "task_registered", task_sha256=task_sha256,
                    employee_id=employee_id, benchmark=benchmark,
                    registration_sha256=record["registration_sha256"])
            return record, 0
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
