#!/usr/bin/env python3
"""Multi-supervisor registry for one shared Web console.

The registry is runtime configuration, not customer package input. It scopes
each Benchmark supervisor to one campaign and one supervisor token while a
single owner token can inspect and override all supervisors.
"""

from __future__ import annotations

from pathlib import Path
import re
import hashlib

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


def _legacy_id(campaign: Path, scan_root: Path) -> str:
    relative = campaign.relative_to(scan_root).parts
    text = "-".join(part.lower() for part in relative if part)
    text = re.sub(r"[^a-z0-9-]+", "-", text).strip("-") or "campaign"
    return ("legacy-" + text)[:70] + "-" + hashlib.sha256(str(campaign).encode()).hexdigest()[:8]


def _legacy_benchmark(campaign: Path, default: str) -> str:
    try:
        value = common.load_json(campaign / "campaign.json")
        categories = set(value.get("config", {}).get("category_targets", {}))
        if categories == {
            "systems-software-engineering", "scientific-ml", "professional-knowledge-work",
            "combinatorial-optimization", "formal-math-theorem-proving", "interactive-games-simulators",
        }:
            return "EdgeBench"
    except (common.PipelineError, OSError, ValueError, TypeError):
        pass
    return default


def discover(
    scan_roots: list[str | Path],
    adopt_legacy: bool = False,
    dedup_root: str | Path | None = None,
    legacy_benchmark: str = "Unclassified",
) -> dict:
    """Discover stamped campaigns below explicit production scan roots."""
    if not scan_roots:
        raise common.PipelineError("at least one scan root is required")
    stamps = []
    seen = set()
    for raw_root in scan_roots:
        root = Path(raw_root).absolute()
        if root.is_symlink() or not root.is_dir():
            raise common.PipelineError(f"scan root is not a directory: {root}")
        if adopt_legacy:
            import topify_supervisor
            for campaign_json in sorted(root.rglob("campaign.json")):
                campaign = campaign_json.parent
                if (campaign / "supervision/supervisor-stamp.json").exists():
                    continue
                try:
                    topify_seed.load_campaign(campaign)
                except (common.PipelineError, OSError, ValueError, TypeError):
                    continue
                supervisor_id = _legacy_id(campaign, root)
                benchmark = _legacy_benchmark(campaign, legacy_benchmark)
                topify_supervisor.initialize(
                    campaign, mode="dual", dedup_root=dedup_root or (root / ".edgebench-dedup"),
                    supervisor_id=supervisor_id, benchmark=benchmark,
                )
                stamp_path = campaign / "supervision/supervisor-stamp.json"
                stamp = common.load_json(stamp_path)
                common.write_latest_json(stamp_path, {
                    **stamp, "status": "legacy-adopted", "needs_owner_review": True,
                    "adopted_at": common.utc_now(),
                })
        for stamp_path in sorted(root.rglob("supervision/supervisor-stamp.json")):
            if stamp_path.is_symlink() or not stamp_path.is_file():
                continue
            stamp = common.load_json(stamp_path)
            if stamp.get("watermark_type") != "edgebench-supervisor" or stamp.get("schema_version") != 1:
                continue
            campaign = stamp_path.parent.parent
            resolved_campaign = campaign.resolve(strict=True)
            if resolved_campaign in seen:
                continue
            seen.add(resolved_campaign)
            topify_seed.load_campaign(resolved_campaign)
            manifest_path = resolved_campaign / "supervision/manifest.json"
            manifest = common.load_json(manifest_path)
            if stamp.get("campaign_root") != str(resolved_campaign):
                raise common.PipelineError(f"supervisor stamp campaign_root mismatch: {stamp_path}")
            if stamp.get("campaign_config_sha256") != manifest.get("campaign_config_sha256"):
                raise common.PipelineError(f"supervisor stamp config hash mismatch: {stamp_path}")
            supervisor_id = stamp.get("supervisor_id")
            benchmark = stamp.get("benchmark")
            if not isinstance(supervisor_id, str) or ID_RE.fullmatch(supervisor_id) is None:
                raise common.PipelineError(f"invalid supervisor_id in stamp: {stamp_path}")
            if not isinstance(benchmark, str) or not benchmark.strip():
                raise common.PipelineError(f"invalid benchmark in stamp: {stamp_path}")
            stamps.append({
                "supervisor_id": supervisor_id,
                "benchmark": benchmark,
                "campaign": str(resolved_campaign),
                "supervisor_token_file": None,
                "stamp_file": str(stamp_path.resolve()),
                "dedup_root": manifest.get("dedup_root"),
            })
    if not stamps:
        raise common.PipelineError("no valid supervisor-stamp.json found below scan roots")
    ids = [item["supervisor_id"] for item in stamps]
    if len(ids) != len(set(ids)):
        raise common.PipelineError("discovered supervisor_id values are not unique")
    dedup_roots = {item["dedup_root"] for item in stamps}
    return {
        "schema_version": 1,
        "discovered": True,
        "scan_roots": [str(Path(item).absolute()) for item in scan_roots],
        "dedup_root": next(iter(dedup_roots)) if len(dedup_roots) == 1 else None,
        "supervisors": stamps,
    }
