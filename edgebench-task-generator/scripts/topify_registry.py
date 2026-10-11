#!/usr/bin/env python3
"""Multi-supervisor registry for one shared Web console.

The registry is runtime configuration, not customer package input. It scopes
each Benchmark supervisor to one campaign and one supervisor token while a
single owner token can inspect and override all supervisors.
"""

from __future__ import annotations

from pathlib import Path
import re

import topify_common as common
import topify_seed


ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}\Z")


def _path(base: Path, value: object, label: str, directory: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise common.PipelineError(f"{label} must be a nonempty path")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    path = path.absolute()
    if directory:
        if path.is_symlink() or not path.is_dir():
            raise common.PipelineError(f"{label} must be an existing directory")
    return path


def load(path: str | Path) -> dict:
    source = Path(path).resolve(strict=True)
    if source.is_symlink() or not source.is_file():
        raise common.PipelineError("supervisor registry must be a regular file")
    value = common.load_json(source)
    if set(value) - {"schema_version", "owner_token_file", "dedup_root", "supervisors"}:
        raise common.PipelineError("supervisor registry contains unknown keys")
    if value.get("schema_version") != 1:
        raise common.PipelineError("supervisor registry schema_version must be 1")
    supervisors = value.get("supervisors")
    if not isinstance(supervisors, list) or not supervisors:
        raise common.PipelineError("supervisor registry needs supervisors")
    base = source.parent
    owner_token_file = value.get("owner_token_file")
    if owner_token_file is not None:
        owner_token_file = _path(base, owner_token_file, "owner_token_file")
    dedup_root = _path(base, value.get("dedup_root", "dedup-global"), "dedup_root")
    seen = set()
    result = []
    for item in supervisors:
        if not isinstance(item, dict) or set(item) - {
            "supervisor_id", "benchmark", "campaign", "supervisor_token_file"
        }:
            raise common.PipelineError("invalid supervisor registry entry")
        supervisor_id = item.get("supervisor_id")
        if not isinstance(supervisor_id, str) or ID_RE.fullmatch(supervisor_id) is None or supervisor_id in seen:
            raise common.PipelineError("supervisor IDs must be unique lowercase slugs")
        benchmark = item.get("benchmark")
        if not isinstance(benchmark, str) or not benchmark.strip():
            raise common.PipelineError("supervisor benchmark must be nonempty")
        campaign = _path(base, item.get("campaign"), f"campaign for {supervisor_id}", directory=True)
        topify_seed.load_campaign(campaign)
        if not (campaign / "supervision/manifest.json").is_file():
            raise common.PipelineError(f"supervisor {supervisor_id} has no supervisor manifest")
        manifest = common.load_json(campaign / "supervision/manifest.json")
        if Path(manifest.get("dedup_root", "")).absolute() != dedup_root:
            raise common.PipelineError(
                f"supervisor {supervisor_id} dedup_root does not match the shared registry"
            )
        token_file = item.get("supervisor_token_file")
        if token_file is None:
            raise common.PipelineError(f"supervisor {supervisor_id} needs supervisor_token_file")
        token_file = _path(base, token_file, f"supervisor token for {supervisor_id}")
        seen.add(supervisor_id)
        result.append({
            "supervisor_id": supervisor_id,
            "benchmark": benchmark,
            "campaign": str(campaign),
            "supervisor_token_file": str(token_file) if token_file else None,
        })
    return {
        "schema_version": 1,
        "registry_file": str(source),
        "owner_token_file": str(owner_token_file) if owner_token_file else None,
        "dedup_root": str(dedup_root),
        "supervisors": result,
    }
