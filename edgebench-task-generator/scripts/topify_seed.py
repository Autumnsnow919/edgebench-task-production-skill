#!/usr/bin/env python3
"""Discover and screen multi-domain EdgeBench seed campaigns."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import urlsplit

import topify_common as common


CHANNELS = frozenset({
    "upstream-change",
    "public-data-research",
    "expert-workflow",
    "original-generator",
    "formalization-gap",
    "simulator-scenario",
})
SLUG = re.compile(r"[a-z0-9-]+\Z")
SEED_ID = re.compile(r"[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+\Z")
DEFAULT_VECTOR = {
    "systems-software-engineering": 26,
    "scientific-ml": 25,
    "professional-knowledge-work": 18,
    "combinatorial-optimization": 12,
    "formal-math-theorem-proving": 10,
    "interactive-games-simulators": 9,
}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list_of_text(value: object, minimum: int = 0) -> bool:
    return (
        isinstance(value, list) and len(value) >= minimum
        and all(_text(item) for item in value)
    )


def _safe_name(seed_id: str) -> str:
    if not SEED_ID.fullmatch(seed_id):
        raise common.PipelineError("seed_id must contain three lowercase slash-separated slugs")
    return seed_id.replace("/", "__") + ".json"


def _https_url(value: object) -> bool:
    if not _text(value):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https" and bool(parsed.netloc)
        and parsed.username is None and parsed.password is None
    )


def _append_campaign_event(root: Path, event: str, **fields: object) -> None:
    record = {"event": event, "recorded_at": common.utc_now(), **fields}
    common.canonical_sha256(record)
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def validate_campaign_config(config: dict) -> dict:
    required = {
        "schema_version", "campaign_id", "target_task_count", "category_targets",
        "funnel_multipliers", "allowed_licenses", "required_dedup_catalogs",
        "max_promoted_per_upstream", "max_promoted_per_dataset",
    }
    allowed = required | {"supervision"}
    if not set(config) <= allowed:
        raise common.PipelineError(f"campaign config contains unknown keys: {sorted(set(config) - allowed)}")
    if config.get("schema_version") != 1 or type(config.get("schema_version")) is not int:
        raise common.PipelineError("campaign schema_version must be integer 1")
    if not _text(config.get("campaign_id")):
        raise common.PipelineError("campaign_id must be a nonempty string")
    if type(config.get("target_task_count")) is not int or config["target_task_count"] <= 0:
        raise common.PipelineError("target_task_count must be a positive integer")
    targets = config.get("category_targets")
    if not isinstance(targets, dict) or set(targets) != common.CATEGORIES:
        raise common.PipelineError("category_targets must cover all six EdgeBench categories")
    if any(type(value) is not int or value < 0 for value in targets.values()):
        raise common.PipelineError("category targets must be nonnegative integers")
    if sum(targets.values()) != config["target_task_count"]:
        raise common.PipelineError("category targets must sum to target_task_count")
    if config["target_task_count"] >= 100 and any(value == 0 for value in targets.values()):
        raise common.PipelineError("a full EdgeBench campaign must plan for all six EdgeBench categories")
    multipliers = config.get("funnel_multipliers")
    if not isinstance(multipliers, dict) or set(multipliers) != {"leads", "screened", "prototypes"}:
        raise common.PipelineError("funnel_multipliers must contain leads, screened, and prototypes")
    if any(type(value) is not int or value <= 0 for value in multipliers.values()):
        raise common.PipelineError("funnel multipliers must be positive integers")
    if not multipliers["leads"] >= multipliers["screened"] >= multipliers["prototypes"]:
        raise common.PipelineError("funnel multipliers must narrow from leads to prototypes")
    if not _list_of_text(config.get("allowed_licenses"), 1):
        raise common.PipelineError("allowed_licenses must be a nonempty string list")
    if not _list_of_text(config.get("required_dedup_catalogs"), 1):
        raise common.PipelineError("required_dedup_catalogs must be a nonempty string list")
    for key in ("max_promoted_per_upstream", "max_promoted_per_dataset"):
        if type(config.get(key)) is not int or config[key] <= 0:
            raise common.PipelineError(f"{key} must be a positive integer")
    supervision = config.get("supervision")
    if supervision is not None:
        supervision_required = {
            "max_concurrent", "transport_modes", "employee_unit", "max_child_tasks"
        }
        if (not isinstance(supervision, dict)
                or not supervision_required <= set(supervision)
                or set(supervision) - supervision_required - {"dedup_mode"}):
            raise common.PipelineError(
                "supervision must declare max_concurrent, transport_modes, employee_unit and max_child_tasks"
            )
        if type(supervision["max_concurrent"]) is not int or supervision["max_concurrent"] < 1:
            raise common.PipelineError("supervision.max_concurrent must be a positive integer")
        if (not isinstance(supervision["transport_modes"], list)
                or not supervision["transport_modes"]
                or not all(item in {"opencode-task", "orca-cli"} for item in supervision["transport_modes"])):
            raise common.PipelineError("supervision.transport_modes must name OpenCode or Orca")
        if supervision["employee_unit"] != "one-task":
            raise common.PipelineError("supervision.employee_unit must be one-task")
        if type(supervision["max_child_tasks"]) is not int or supervision["max_child_tasks"] < 0:
            raise common.PipelineError("supervision.max_child_tasks must be nonnegative")
        if supervision.get("dedup_mode", "warn") not in {"strict", "warn"}:
            raise common.PipelineError("supervision.dedup_mode must be strict or warn")
    common.canonical_sha256(config)
    return config


def initialize_campaign(workspace: Path, config_path: Path) -> dict:
    config = validate_campaign_config(common.load_json(config_path))
    target = workspace.absolute()
    if target.exists() or target.is_symlink():
        raise common.PipelineError("campaign workspace already exists; refusing to overwrite it")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise common.PipelineError("campaign parent must be an existing real directory")
    target.mkdir()
    try:
        for name in (
            "source-captures", "leads", "refinements", "decisions", "provisionals",
            "screened", "prototypes", "promoted", "candidates", "seed-family-decisions",
        ):
            (target / name).mkdir()
        (target / "events.jsonl").touch(exist_ok=False)
        multipliers = config["funnel_multipliers"]
        campaign = {
            "schema_version": 1,
            "docx_sha256": common.DOCX_SHA256,
            "config": config,
            "config_sha256": common.canonical_sha256(config),
            "lead_targets": {
                category: count * multipliers["leads"]
                for category, count in config["category_targets"].items()
            },
            "screened_targets": {
                category: count * multipliers["screened"]
                for category, count in config["category_targets"].items()
            },
            "prototype_targets": {
                category: count * multipliers["prototypes"]
                for category, count in config["category_targets"].items()
            },
            "state": "CAMPAIGN_READY",
            "created_at": common.utc_now(),
        }
        common.write_new_json(target / "campaign.json", campaign)
        _append_campaign_event(target, "campaign_initialized", config_sha256=campaign["config_sha256"])
        return {"state": campaign["state"], "campaign": str(target)}
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def load_campaign(workspace: str | Path) -> tuple[Path, dict]:
    root = Path(workspace).absolute()
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise common.PipelineError("campaign workspace does not exist") from exc
    if root != resolved or root.is_symlink() or not root.is_dir():
        raise common.PipelineError("campaign workspace must be a canonical real directory")
    campaign = common.load_json(root / "campaign.json")
    if campaign.get("schema_version") != 1 or campaign.get("docx_sha256") != common.DOCX_SHA256:
        raise common.PipelineError("not a DOCX-only seed campaign")
    config = validate_campaign_config(campaign.get("config"))
    if campaign.get("config_sha256") != common.canonical_sha256(config):
        raise common.PipelineError("campaign config hash mismatch")
    return root, campaign


def validate_seed_lead(seed: dict) -> dict:
    required = {
        "schema_version", "seed_id", "category", "channel", "real_work", "origins",
        "task_hypothesis", "feasibility", "dedup", "risks", "status",
    }
    if set(seed) != required:
        raise common.PipelineError(f"seed card keys must be exactly {sorted(required)}")
    if seed.get("schema_version") != 1 or type(seed.get("schema_version")) is not int:
        raise common.PipelineError("seed schema_version must be integer 1")
    _safe_name(seed.get("seed_id"))
    if seed.get("category") not in common.CATEGORIES:
        raise common.PipelineError("seed category is not one of the six EdgeBench categories")
    if not seed["seed_id"].startswith(seed["category"] + "/"):
        raise common.PipelineError("seed_id must start with its category")
    if seed.get("channel") not in CHANNELS:
        raise common.PipelineError(f"unknown seed channel: {seed.get('channel')}")
    if seed.get("status") != "lead":
        raise common.PipelineError("scouted seed status must be lead")
    if not isinstance(seed.get("real_work"), dict):
        raise common.PipelineError("seed real_work must be an object")
    if not isinstance(seed.get("origins"), list) or not seed["origins"]:
        raise common.PipelineError("seed needs at least one origin")
    for key in ("task_hypothesis", "feasibility", "dedup"):
        if not isinstance(seed.get(key), dict):
            raise common.PipelineError(f"seed {key} must be an object")
    if not isinstance(seed.get("risks"), list) or not all(isinstance(item, str) for item in seed["risks"]):
        raise common.PipelineError("seed risks must be a string list")
    common.canonical_sha256(seed)
    return seed


def _import_catalog(root: Path, catalog: dict, capture_evidence: dict | None = None) -> dict:
    if set(catalog) != {"schema_version", "adapter", "seeds"} or catalog.get("schema_version") != 1:
        raise common.PipelineError("catalog must contain schema_version, adapter, and seeds")
    adapter = catalog.get("adapter")
    if not isinstance(adapter, dict) or not _text(adapter.get("id")):
        raise common.PipelineError("catalog adapter must have a nonempty id")
    if adapter.get("kind") == "catalog":
        if set(adapter) != {"kind", "id"}:
            raise common.PipelineError("catalog adapter must contain kind and id")
    elif adapter.get("kind") == "captured-source":
        required_adapter = {"kind", "id", "source_capture_sha256", "source_registry_sha256"}
        if set(adapter) != required_adapter:
            raise common.PipelineError(
                f"captured-source adapter keys must be exactly {sorted(required_adapter)}"
            )
        for key in ("source_capture_sha256", "source_registry_sha256"):
            if not isinstance(adapter.get(key), str) or not common.HEX64.fullmatch(adapter[key]):
                raise common.PipelineError(f"captured-source adapter {key} must be 64 lowercase hex")
    else:
        raise common.PipelineError("catalog adapter kind must be catalog or captured-source")
    seeds = catalog.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise common.PipelineError("catalog seeds must be a nonempty list")
    validated = [validate_seed_lead(seed) for seed in seeds]
    ids = [seed["seed_id"] for seed in validated]
    if len(ids) != len(set(ids)):
        raise common.PipelineError("catalog contains duplicate seed IDs")
    for seed_id in ids:
        if (root / "leads" / _safe_name(seed_id)).exists():
            raise common.PipelineError(f"seed lead already exists: {seed_id}")
    capture_hash = common.canonical_sha256(catalog)
    capture_path = root / "source-captures" / f"{capture_hash}.json"
    if capture_path.exists() or capture_path.is_symlink():
        raise common.PipelineError("catalog source capture already exists")
    capture_record = {
        "schema_version": 1,
        "adapter": adapter,
        "catalog_sha256": capture_hash,
        "captured_at": common.utc_now(),
        "catalog": catalog,
    }
    if capture_evidence is not None:
        capture_record["source_evidence"] = capture_evidence
    common.write_new_json(capture_path, capture_record)
    for seed in validated:
        common.write_new_json(root / "leads" / _safe_name(seed["seed_id"]), seed)
    counts = Counter(seed["category"] for seed in validated)
    _append_campaign_event(root, "catalog_scouted", catalog_sha256=capture_hash, seed_ids=ids)
    return {
        "state": "LEADS_COLLECTED",
        "imported": len(validated),
        "by_category": dict(sorted(counts.items())),
        "catalog_sha256": capture_hash,
    }


def scout_catalog(workspace: Path, catalog_path: Path) -> dict:
    root, _ = load_campaign(workspace)
    catalog = common.load_json(catalog_path)
    if not isinstance(catalog.get("adapter"), dict) or catalog["adapter"].get("kind") != "catalog":
        raise common.PipelineError("scout --catalog accepts only the named catalog adapter")
    return _import_catalog(root, catalog)


def validate_source_registry(registry: dict) -> dict:
    required = {
        "schema_version", "checked_at", "acceptance_note", "dedup_sources", "sources",
    }
    if (set(registry) != required or registry.get("schema_version") != 1
            or type(registry.get("schema_version")) is not int):
        raise common.PipelineError(f"source registry keys must be exactly {sorted(required)}")
    if not _text(registry.get("checked_at")) or not _text(registry.get("acceptance_note")):
        raise common.PipelineError("source registry needs checked_at and acceptance_note")
    dedup_sources = registry.get("dedup_sources")
    if not isinstance(dedup_sources, list) or not dedup_sources:
        raise common.PipelineError("source registry needs dedup_sources")
    dedup_ids = []
    for entry in dedup_sources:
        if not isinstance(entry, dict) or set(entry) != {"id", "url"}:
            raise common.PipelineError("dedup source must contain id and url")
        if not _text(entry.get("id")) or not _https_url(entry.get("url")):
            raise common.PipelineError("dedup source needs an ID and credential-free HTTPS URL")
        dedup_ids.append(entry["id"])
    if len(dedup_ids) != len(set(dedup_ids)):
        raise common.PipelineError("source registry contains duplicate dedup source IDs")

    sources = registry.get("sources")
    if not isinstance(sources, list) or not sources:
        raise common.PipelineError("source registry needs sources")
    source_ids = []
    source_categories = set()
    source_keys = {
        "id", "category", "channel", "official_url", "object_kinds",
        "candidate_transformations", "retrieval_status", "rights_policy", "cautions",
        "artifact_hosts",
    }
    for source in sources:
        if not isinstance(source, dict) or set(source) != source_keys:
            raise common.PipelineError(f"registry source keys must be exactly {sorted(source_keys)}")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not SLUG.fullmatch(source_id):
            raise common.PipelineError("registry source id must be a lowercase slug")
        if source.get("category") not in common.CATEGORIES:
            raise common.PipelineError("registry source category is invalid")
        if source.get("channel") not in CHANNELS:
            raise common.PipelineError("registry source channel is invalid")
        if not _https_url(source.get("official_url")):
            raise common.PipelineError("registry source URL must be credential-free HTTPS")
        hosts = source.get("artifact_hosts")
        if (not _list_of_text(hosts, 1) or len(hosts) != len(set(hosts))
                or urlsplit(source["official_url"]).hostname not in hosts
                or any(not re.fullmatch(r"[a-z0-9.-]+", host) or host.startswith(".")
                       or host.endswith(".") or ".." in host for host in hosts)):
            raise common.PipelineError("registry source needs distinct official artifact hosts")
        if not _list_of_text(source.get("object_kinds"), 1):
            raise common.PipelineError("registry source needs object_kinds")
        if not _list_of_text(source.get("candidate_transformations"), 1):
            raise common.PipelineError("registry source needs candidate_transformations")
        if source.get("retrieval_status") not in {"reachable", "blocked", "unverified"}:
            raise common.PipelineError("registry source retrieval_status is invalid")
        if source.get("rights_policy") != "per-artifact":
            raise common.PipelineError("registry sources must use per-artifact rights review")
        if not _list_of_text(source.get("cautions"), 1):
            raise common.PipelineError("registry source needs cautions")
        source_ids.append(source_id)
        source_categories.add(source["category"])
    if len(source_ids) != len(set(source_ids)):
        raise common.PipelineError("source registry contains duplicate source IDs")
    if source_categories != common.CATEGORIES:
        raise common.PipelineError("source registry must cover all six EdgeBench categories")
    common.canonical_sha256(registry)
    return registry


def validate_source_capture(capture: dict, source: dict) -> dict:
    required = {
        "schema_version", "source_id", "capture_id", "captured_at", "retrieval_url", "records",
    }
    if (set(capture) != required or capture.get("schema_version") != 1
            or type(capture.get("schema_version")) is not int):
        raise common.PipelineError(f"source capture keys must be exactly {sorted(required)}")
    if capture.get("source_id") != source["id"]:
        raise common.PipelineError("source capture does not match the selected registry source")
    if not isinstance(capture.get("capture_id"), str) or not SLUG.fullmatch(capture["capture_id"]):
        raise common.PipelineError("source capture id must be a lowercase slug")
    if not _text(capture.get("captured_at")) or not _https_url(capture.get("retrieval_url")):
        raise common.PipelineError("source capture needs a time and credential-free HTTPS retrieval URL")
    records = capture.get("records")
    if not isinstance(records, list) or not records:
        raise common.PipelineError("source capture records must be a nonempty list")
    record_keys = {
        "record_id", "title", "url", "project", "revision", "kind",
        "artifact_sha256", "offline_mirror", "license",
    }
    record_ids = []
    for record in records:
        if not isinstance(record, dict) or set(record) != record_keys:
            raise common.PipelineError(f"source capture record keys must be exactly {sorted(record_keys)}")
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not SLUG.fullmatch(record_id):
            raise common.PipelineError("source capture record_id must be a lowercase slug")
        if not all(_text(record.get(key)) for key in ("title", "project", "kind")):
            raise common.PipelineError("source capture record needs title, project, and kind")
        if record["kind"] not in source["object_kinds"]:
            raise common.PipelineError("source capture record kind is not allowed by the registry source")
        if not _https_url(record.get("url")):
            raise common.PipelineError("source capture record URL must be credential-free HTTPS")
        for key in ("revision", "offline_mirror", "license"):
            if record.get(key) is not None and not _text(record.get(key)):
                raise common.PipelineError(f"source capture record {key} must be null or nonempty text")
        digest = record.get("artifact_sha256")
        if digest is not None and (not isinstance(digest, str) or not common.HEX64.fullmatch(digest)):
            raise common.PipelineError("source capture artifact_sha256 must be null or 64 lowercase hex")
        mirror = record.get("offline_mirror")
        if mirror is not None and not Path(mirror).is_absolute():
            raise common.PipelineError("source capture offline_mirror must be null or an absolute path")
        record_ids.append(record_id)
    if len(record_ids) != len(set(record_ids)):
        raise common.PipelineError("source capture contains duplicate record IDs")
    common.canonical_sha256(capture)
    return capture


def _lead_from_capture(source: dict, record: dict) -> dict:
    source_id = source["id"]
    record_id = record["record_id"]
    pinned_artifact = bool(record["revision"] and record["artifact_sha256"]
                           and record["offline_mirror"] and record["license"])
    cautions = list(dict.fromkeys([*source["cautions"], "unreviewed-discovery-lead"]))
    return {
        "schema_version": 1,
        "seed_id": f"{source['category']}/{source_id}/{record_id}",
        "category": source["category"],
        "channel": source["channel"],
        "real_work": {
            "role": "",
            "scenario": record["title"],
            "deliverable": "",
            "material_consequence": "",
        },
        "origins": [{
            "kind": record["kind"] if pinned_artifact else "discovery",
            "project": record["project"],
            "url": record["url"],
            "revision": record["revision"] or "",
            "artifact_sha256": record["artifact_sha256"] or "",
            "offline_mirror": record["offline_mirror"] or "",
            "license": record["license"] or "",
            "redistribution_status": "pending",
        }],
        "task_hypothesis": {
            "snapshot_paths": [],
            "raw_metric": "",
            "direction": None,
            "improvement_levers": [],
            "development_feedback": [],
            "baseline_strategy": "",
            "upper_strategy": "",
            "reference_mode": None,
            "offline_verifier_plan": "",
            "binary_only": None,
            "requires_waiting": None,
        },
        "feasibility": {
            "runtime_network": None,
            "architectures": [],
            "estimated_cold_start_seconds": None,
            "estimated_score_seconds": None,
            "estimated_interaction_hours": None,
            "data_bytes": None,
        },
        "dedup": {
            "upstream_ids": [f"{source_id}:{record_id}"],
            "dataset_ids": [f"{source_id}:{record_id}"] if record["kind"] == "dataset" else [],
            "objective_signature": "",
            "compared_catalogs": [],
            "collisions": [],
            "status": "pending",
        },
        "risks": cautions,
        "status": "lead",
    }


def scout_source(
    workspace: Path,
    registry_path: Path,
    source_id: str,
    capture_path: Path,
) -> dict:
    root, _ = load_campaign(workspace)
    registry = validate_source_registry(common.load_json(registry_path))
    sources = {source["id"]: source for source in registry["sources"]}
    if source_id not in sources:
        raise common.PipelineError(f"source registry has no source: {source_id}")
    source = sources[source_id]
    capture = validate_source_capture(common.load_json(capture_path), source)
    capture_sha256 = common.canonical_sha256(capture)
    registry_sha256 = common.canonical_sha256(registry)
    catalog = {
        "schema_version": 1,
        "adapter": {
            "kind": "captured-source",
            "id": f"{source_id}-{capture['capture_id']}",
            "source_capture_sha256": capture_sha256,
            "source_registry_sha256": registry_sha256,
        },
        "seeds": [_lead_from_capture(source, record) for record in capture["records"]],
    }
    report = _import_catalog(root, catalog, {
        "source_capture": capture,
        "source_registry_entry": source,
        "source_registry_sha256": registry_sha256,
    })
    return {**report, "source_id": source_id, "capture_sha256": capture_sha256}


def _refinement_root(root: Path, seed_id: str) -> Path:
    return root / "refinements" / _safe_name(seed_id).removesuffix(".json")


def _current_seed(root: Path, seed_id: str) -> tuple[dict, str]:
    original = validate_seed_lead(common.load_json(root / "leads" / _safe_name(seed_id)))
    original_sha256 = common.canonical_sha256(original)
    latest_path = _refinement_root(root, seed_id) / "latest.json"
    if not latest_path.is_file() or latest_path.is_symlink():
        return original, original_sha256
    latest = common.load_json(latest_path)
    required = {
        "schema_version", "attempt", "seed_id", "original_seed_sha256",
        "previous_seed_sha256", "seed_sha256", "seed", "refined_at",
    }
    if set(latest) != required or latest.get("schema_version") != 1:
        raise common.PipelineError("latest seed refinement is malformed")
    if latest.get("seed_id") != seed_id or latest.get("original_seed_sha256") != original_sha256:
        raise common.PipelineError("latest seed refinement is not bound to the original lead")
    seed = validate_seed_lead(latest.get("seed"))
    seed_sha256 = common.canonical_sha256(seed)
    if latest.get("seed_sha256") != seed_sha256:
        raise common.PipelineError("latest seed refinement hash mismatch")
    return seed, seed_sha256


def refine_seed(workspace: Path, seed_id: str, input_path: Path) -> tuple[dict, int]:
    root, _ = load_campaign(workspace)
    if (root / "screened" / _safe_name(seed_id)).exists():
        raise common.PipelineError("a screened seed cannot be refined")
    original = validate_seed_lead(common.load_json(root / "leads" / _safe_name(seed_id)))
    current, current_sha256 = _current_seed(root, seed_id)
    candidate = validate_seed_lead(common.load_json(input_path))
    if candidate["seed_id"] != seed_id:
        raise common.PipelineError("refinement seed_id does not match the selected lead")
    for key in ("category", "channel"):
        if candidate[key] != original[key]:
            raise common.PipelineError(f"refinement cannot change captured {key}")
    if len(candidate["origins"]) < len(original["origins"]):
        raise common.PipelineError("refinement cannot remove captured origins")
    pinned_origin_fields = (
        "kind", "project", "url", "revision", "artifact_sha256", "offline_mirror", "license",
    )
    for original_origin, candidate_origin in zip(original["origins"], candidate["origins"]):
        if candidate_origin.get("kind") != original_origin.get("kind"):
            # Earlier catalog imports sometimes labeled a mutable issue or
            # landing page as an artifact. Preserve the captured bytes and URL;
            # only its classification may be corrected, and only if no source
            # digest or offline artifact was ever captured for that origin.
            if (candidate_origin.get("kind") != "discovery"
                    or original_origin.get("kind") == "discovery"
                    or original_origin.get("artifact_sha256")
                    or original_origin.get("offline_mirror")
                    or not _text(original_origin.get("project"))
                    or not _https_url(original_origin.get("url"))
                    or any(candidate_origin.get(key) != original_origin.get(key)
                           for key in (*pinned_origin_fields[1:], "redistribution_status"))):
                raise common.PipelineError("refinement cannot change captured origin kind")
            continue
        for key in pinned_origin_fields:
            if original_origin.get(key) not in {None, ""} and candidate_origin.get(key) != original_origin[key]:
                raise common.PipelineError(f"refinement cannot change captured origin {key}")
    for origin in candidate["origins"][len(original["origins"]):]:
        if _source_identity({"origins": [origin]})[0] != "complete":
            raise common.PipelineError("appended source origin needs pinned HTTPS revision, digest, matching offline mirror and license status")
    for key in ("upstream_ids", "dataset_ids"):
        original_ids = original["dedup"].get(key)
        candidate_ids = candidate["dedup"].get(key)
        if (not isinstance(original_ids, list) or not isinstance(candidate_ids, list)
                or not set(original_ids).issubset(candidate_ids)):
            raise common.PipelineError(f"refinement must retain captured dedup {key}")
    attempt_number, attempt = common.next_attempt(_refinement_root(root, seed_id))
    record = {
        "schema_version": 1,
        "attempt": attempt_number,
        "seed_id": seed_id,
        "original_seed_sha256": common.canonical_sha256(original),
        "previous_seed_sha256": current_sha256,
        "seed_sha256": common.canonical_sha256(candidate),
        "seed": candidate,
        "refined_at": common.utc_now(),
    }
    common.write_new_json(attempt / "refinement.json", record)
    common.write_latest_json(attempt.parent / "latest.json", record)
    _append_campaign_event(
        root,
        "seed_refined",
        seed_id=seed_id,
        attempt=attempt_number,
        seed_sha256=record["seed_sha256"],
    )
    return {
        "state": "LEAD_REFINED",
        "seed_id": seed_id,
        "attempt": attempt_number,
        "seed_sha256": record["seed_sha256"],
    }, 0


def _result(check_id: str, status: str, detail: str) -> dict:
    return {"id": check_id, "status": status, "detail": detail}


def _source_identity(seed: dict, *, require_license: bool = True) -> tuple[str, str]:
    incomplete = False
    pinned_count = 0
    for origin in seed["origins"]:
        required = {
            "kind", "project", "url", "revision", "artifact_sha256", "offline_mirror",
            "license", "redistribution_status",
        }
        if not isinstance(origin, dict) or set(origin) != required:
            return "incomplete", "An origin is missing immutable identity fields."
        if origin["kind"] == "discovery":
            if not _text(origin.get("project")) or not _https_url(origin.get("url")):
                return "failed", "Discovery references must have an official credential-free HTTPS identity."
            continue
        identity_fields = ("kind", "project", "url", "revision", "license") if require_license else (
            "kind", "project", "url", "revision")
        if not all(_text(origin.get(key)) for key in identity_fields):
            incomplete = True
            continue
        parsed = urlsplit(origin["url"])
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            return "failed", "Origin URLs must be credential-free HTTPS URLs."
        digest = origin.get("artifact_sha256")
        mirror_value = origin.get("offline_mirror")
        if not isinstance(digest, str) or not common.HEX64.fullmatch(digest):
            incomplete = True
            continue
        if not isinstance(mirror_value, str) or not Path(mirror_value).is_absolute():
            incomplete = True
            continue
        mirror = Path(mirror_value)
        try:
            if mirror.is_symlink() or mirror.resolve(strict=True) != mirror or common.sha256_file(mirror) != digest:
                return "failed", "Offline mirror bytes do not match the pinned source digest."
        except (common.PipelineError, OSError):
            return "incomplete", "The pinned offline mirror is unavailable."
        pinned_count += 1
    if incomplete or not pinned_count:
        return "incomplete", "One or more origins still need immutable identity evidence."
    return "complete", "Every origin has an immutable URL/revision/hash and matching offline mirror."


def _rights(seed: dict, config: dict) -> tuple[str, str]:
    artifacts = [origin for origin in seed["origins"] if isinstance(origin, dict)
                 and origin.get("kind") != "discovery"]
    if not artifacts:
        return "incomplete", "No pinned artifact exists; discovery links do not prove redistribution rights."
    statuses = [origin.get("redistribution_status") for origin in artifacts]
    licenses = [origin.get("license") for origin in artifacts]
    if any(status == "rejected" for status in statuses):
        return "failed", "An origin explicitly forbids intended redistribution."
    if any(status != "confirmed" for status in statuses):
        return "incomplete", "Written redistribution confirmation is still pending."
    if any(license not in config["allowed_licenses"] for license in licenses):
        return "failed", "An origin license is outside the campaign allowlist."
    return "complete", "Licenses and intended redistribution are confirmed."


def _dedup(seed: dict, config: dict) -> tuple[str, str]:
    dedup = seed["dedup"]
    collisions = dedup.get("collisions")
    if dedup.get("status") == "collision" or isinstance(collisions, list) and collisions:
        return "failed", "Source or dataset lineage collides with an existing benchmark task."
    catalogs = dedup.get("compared_catalogs")
    if (dedup.get("status") != "clear" or not isinstance(catalogs, list)
            or not set(config["required_dedup_catalogs"]).issubset(catalogs)):
        return "incomplete", "Required source-level benchmark comparisons are incomplete."
    if not _list_of_text(dedup.get("upstream_ids")) or not isinstance(dedup.get("dataset_ids"), list):
        return "incomplete", "Canonical upstream and dataset identities are missing."
    if not _text(dedup.get("objective_signature")):
        return "incomplete", "Objective/verifier signature is missing."
    return "complete", "No source, dataset, or objective-signature collision was reported."


def _workflow(seed: dict) -> tuple[str, str]:
    work = seed["real_work"]
    if not all(_text(work.get(key)) for key in ("role", "scenario", "deliverable", "material_consequence")):
        return "incomplete", "Real role, scenario, deliverable, or consequence is missing."
    return "complete", "The lead represents a concrete real workflow and consequence."


def _learning_loop(seed: dict) -> tuple[str, str]:
    task = seed["task_hypothesis"]
    if task.get("binary_only") is True or task.get("requires_waiting") is True:
        return "failed", "The proposed task is binary-only or rewards elapsed time."
    paths = task.get("snapshot_paths")
    path_ok = isinstance(paths, list) and bool(paths) and all(
        isinstance(path, str) and path.startswith("/workspace/")
        and ".." not in PurePosixPath(path).parts for path in paths
    )
    required_text = ("raw_metric", "baseline_strategy", "upper_strategy", "offline_verifier_plan")
    if (not path_ok or task.get("direction") not in {"maximize", "minimize"}
            or not all(_text(task.get(key)) for key in required_text)
            or task.get("reference_mode") not in common.REFERENCE_MODES
            or not _list_of_text(task.get("improvement_levers"), 2)
            or not _list_of_text(task.get("development_feedback"), 1)
            or task.get("binary_only") is not False or task.get("requires_waiting") is not False):
        return "incomplete", "Snapshot, continuous metric, feedback, levers, or endpoint strategy is incomplete."
    if "theoretical" in task["upper_strategy"].lower():
        return "failed", "A theoretical metric maximum is not an achievable upper basis."
    hard_risks = {
        "public-solution-copy", "forced-waiting", "binary-cliff", "private-feedback",
        "runtime-network", "single-architecture", "target-leakage", "label-in-work",
    }
    if hard_risks.intersection(seed["risks"]):
        return "failed", "The lead contains a known non-learning or contamination risk."
    return "complete", "The hypothesis supports snapshot, feedback, and continuous improvement."


def _feasibility(seed: dict) -> tuple[str, str]:
    value = seed["feasibility"]
    if value.get("runtime_network") is True:
        return "failed", "Task runtime must not require network access."
    if value.get("runtime_network") is not False:
        return "incomplete", "Task runtime network feasibility is still unknown."
    architectures = value.get("architectures")
    if not isinstance(architectures, list) or not architectures:
        return "incomplete", "Target architecture feasibility is still unknown."
    if set(architectures) != common.ARCHITECTURES:
        return "failed", "The task must be feasible on linux/amd64 and linux/arm64."
    numbers = ("estimated_cold_start_seconds", "estimated_score_seconds", "estimated_interaction_hours", "data_bytes")
    if not all(common.finite_number(value.get(key)) and value[key] >= 0 for key in numbers):
        return "incomplete", "Runtime and data estimates are incomplete."
    if value["estimated_score_seconds"] <= 0 or value["estimated_interaction_hours"] < 1:
        return "failed", "Scoring must execute and the interaction estimate must be at least one hour."
    if value["estimated_interaction_hours"] < 3:
        return "incomplete", "The estimated task duration is below the acceptable three-hour lower bound."
    score_budget = value["estimated_interaction_hours"] * 3600
    if value["estimated_cold_start_seconds"] + value["estimated_score_seconds"] >= score_budget:
        return "failed", "One verifier invocation consumes the whole interaction window."
    return "complete", "The task is offline, dual-architecture, and repeatedly scoreable."


def screen_seed(workspace: Path, seed_id: str, for_prototype: bool = False,
                *, owner_sample: bool = False) -> tuple[dict, int]:
    if owner_sample and not for_prototype:
        raise common.PipelineError("owner sample Seed screen requires --for-prototype; no rights-based promotion is claimed")
    root, campaign = load_campaign(workspace)
    if (root / "seed-family-decisions" / _safe_name(seed_id)).exists():
        raise common.PipelineError("seed was shelved by the main reviewer; choose a different Seed")
    if (root / "screened" / _safe_name(seed_id)).exists():
        raise common.PipelineError("seed already passed screening")
    seed, seed_sha256 = _current_seed(root, seed_id)
    config = campaign["config"]
    check_values = [
        ("seed.source_identity", *_source_identity(seed, require_license=not owner_sample)),
        ("seed.rights", *(('not_required_by_owner', 'Redistribution authorization is not requested for this sample.')
                          if owner_sample else _rights(seed, config))),
        ("seed.dedup", *(('not_required_by_owner', 'Catalog deduplication is not requested for this sample.')
                         if owner_sample else _dedup(seed, config))),
        ("seed.real_work", *_workflow(seed)),
        ("seed.learning_loop", *_learning_loop(seed)),
        ("seed.feasibility", *_feasibility(seed)),
    ]
    checks = [_result(check_id, status, detail) for check_id, status, detail in check_values]
    statuses = {item["status"] for item in checks}
    verdict = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "complete"
    provisional = (
        for_prototype
        and all(item["status"] == "complete" for item in checks if item["id"] not in {"seed.rights", "seed.dedup"})
        and (owner_sample or (verdict == "incomplete"
             and all(item["status"] == "incomplete" for item in checks if item["id"] in {"seed.rights", "seed.dedup"})
             and seed["dedup"].get("status") != "collision"
             and not seed["dedup"].get("collisions")))
    )
    if provisional:
        verdict = "prototype-candidate"
    report = {
        "schema_version": 1,
        "attempt": None,
        "seed_id": seed_id,
        "seed_sha256": seed_sha256,
        "verdict": verdict,
        "checks": checks,
        "screened_at": common.utc_now(),
    }
    decision_root = root / "decisions" / _safe_name(seed_id).removesuffix(".json")
    attempt_number, attempt = common.next_attempt(decision_root)
    report["attempt"] = attempt_number
    decision_path = attempt / "report.json"
    common.write_new_json(decision_path, report)
    common.write_latest_json(decision_root / "latest.json", report)
    if verdict == "complete":
        common.write_new_json(root / "screened" / _safe_name(seed_id), {
            **seed,
            "status": "rights-and-dedup-screened",
            "screen_report_sha256": common.sha256_file(decision_path),
        })
    elif provisional:
        common.write_new_json(root / "provisionals" / _safe_name(seed_id), {
            **seed,
            "status": "prototype-candidate",
            "prototype_only": True,
            "screen_report_sha256": common.sha256_file(decision_path),
        })
    _append_campaign_event(root, "seed_screened", seed_id=seed_id, verdict=verdict)
    return report, 0 if verdict in {"complete", "prototype-candidate"} else 2


def shelve_seed(workspace: Path, seed_id: str, reviewer_id: str, reason: str) -> tuple[dict, int]:
    root, _ = load_campaign(workspace)
    if (root / "supervision/manifest.json").is_file():
        import topify_control as control
        owners = [common.load_json(p) for p in (root / "supervision/employees").glob("*.json") if p.name != "latest.json"]
        owners = [e for e in owners if e.get("role") == "producer" and e.get("seed_id") == seed_id]
        if not owners:
            raise common.PipelineError("分层批次中搁置 Seed 必须先定位负责人和取得 Owner 决定。")
        for employee in owners:
            control.require_disposition(root, employee["employee_id"], "shelve_seed")
    decisions = root / "seed-family-decisions"
    if decisions.is_symlink() or decisions.exists() and not decisions.is_dir():
        raise common.PipelineError("seed-family-decisions must be a real directory")
    decisions.mkdir(exist_ok=True)
    if not _text(reviewer_id) or not _text(reason):
        raise common.PipelineError("seed shelving requires a main reviewer ID and concrete rationale")
    lead = validate_seed_lead(common.load_json(root / "leads" / _safe_name(seed_id)))
    if (root / "promoted" / _safe_name(seed_id)).exists():
        raise common.PipelineError("a promoted Seed needs a new identity review, not silent shelving")
    latest_candidates = {}
    for path in sorted((root / "candidates" / _safe_name(seed_id).removesuffix(".json")).glob("attempt-*/report.json")):
        item = common.load_json(path)
        if item.get("seed_id") == seed_id and isinstance(item.get("task_sha256"), str):
            latest_candidates[item["task_sha256"]] = {
                "task_sha256": item["task_sha256"], "status": item.get("status"),
                "receipt_sha256": common.sha256_file(path),
            }
    candidates = [latest_candidates[key] for key in sorted(latest_candidates)]
    report = {
        "schema_version": 1, "seed_id": seed_id, "category": lead["category"],
        "status": "shelved", "reviewer_role": "main-agent", "reviewer_id": reviewer_id,
        "reason": reason, "lead_sha256": common.sha256_file(root / "leads" / _safe_name(seed_id)),
        "candidates": candidates, "reviewed_at": common.utc_now(),
    }
    common.write_new_json(decisions / _safe_name(seed_id), report)
    _append_campaign_event(root, "seed_shelved", seed_id=seed_id, candidate_count=len(candidates))
    return report, 0


def _candidate_source_bound(card: dict, request: dict) -> bool:
    """A discovery link is context; every pinned non-discovery origin must bind."""
    origins = [
        origin for origin in card.get("origins", [])
        if isinstance(origin, dict) and origin.get("kind") != "discovery"
    ]
    source_projects = request["source"]["upstream_projects"]
    return request["source"].get("mode") != "source-derived" or all(
        isinstance(origin.get("revision"), str) and origin["revision"]
        and any(origin["revision"] in source for source in source_projects)
        for origin in origins
    )


def _source_identity_sha256(card: dict) -> str:
    """Freeze pinned source bytes, not mutable rights/dedup review metadata."""
    pinned = [{key: origin.get(key) for key in
               ("kind", "project", "url", "revision", "artifact_sha256")}
              for origin in card.get("origins", []) if origin.get("kind") != "discovery"]
    return common.canonical_sha256(sorted(pinned, key=lambda row: common.canonical_sha256(row)))


def _hardening_identity_limit_reached(workspace: Path, seed_id: str,
                                     task_sha256: str | None = None) -> bool:
    """Initial candidate plus two constrained hardening identities is the cap."""
    folder = workspace / "candidates" / _safe_name(seed_id).removesuffix(".json")
    versions = {}
    for path in sorted(folder.glob("attempt-*/report.json")):
        report = common.load_json(path)
        if report.get("seed_id") != seed_id:
            continue
        candidate_hash = report.get("task_sha256")
        status = report.get("status")
        if isinstance(candidate_hash, str) and status in {"revise", "review-needed"}:
            versions[candidate_hash] = status
    return len(versions) >= 3 and (task_sha256 is None or task_sha256 not in versions)


def _two_hardenings_measured(workspace: Path, seed_id: str, task_sha256: str) -> bool:
    """Do not promote a real family before r0→r1→r2 are individually audited."""
    folder = Path(workspace) / "candidates" / _safe_name(seed_id).removesuffix(".json")
    versions = []
    for path in sorted(folder.glob("attempt-*/report.json")):
        row = common.load_json(path)
        if row.get("seed_id") != seed_id or not common.HEX64.fullmatch(str(row.get("task_sha256"))):
            return False
        if versions and versions[-1]["task_sha256"] == row["task_sha256"]:
            versions[-1] = row
        else:
            versions.append(row)
    if len(versions) != 3 or versions[-1]["task_sha256"] != task_sha256:
        return False
    mechanisms = set()
    for index, row in enumerate(versions):
        if (row.get("revision_index") != index
                or row.get("status") not in {"revise", "review-needed"}
                or row.get("parent_task_sha256") != (
                    versions[index - 1]["task_sha256"] if index else None)):
            return False
        if index:
            mechanism = row.get("mechanism_id")
            if (not common.HEX64.fullmatch(str(row.get("hardening_sha256")))
                    or not isinstance(mechanism, str) or mechanism in mechanisms):
                return False
            mechanisms.add(mechanism)
    return True


def validate_hardening_lineage(workspace: Path, seed_id: str, task_root: Path,
                               task_sha256: str, task_id: str, diagnostic: dict, *,
                               source_identity_sha256: str) -> dict:
    """Bind two source-grounded revisions to measured parents and current probes.

    A checked receipt is not a human approval of causal difficulty. Candidate
    review still determines whether the claimed change is a legitimate task.
    """
    root = Path(workspace)
    path = task_root / "authoring/hardening.json"
    folder = root / "candidates" / _safe_name(seed_id).removesuffix(".json")
    versions = []
    for receipt in sorted(folder.glob("attempt-*/report.json")):
        item = common.load_json(receipt)
        if item.get("seed_id") != seed_id or not common.HEX64.fullmatch(str(item.get("task_sha256"))):
            raise common.PipelineError("hardening candidate history is inconsistent")
        if not versions or item["task_sha256"] != versions[-1]["task_sha256"]:
            versions.append(item)
        else:
            versions[-1] = item
    if not versions:
        if path.exists() or path.is_symlink():
            raise common.PipelineError("initial candidate must not invent a hardening parent")
        return {"revision_index": 0, "parent_task_sha256": None}
    parent = versions[-1]
    if parent.get("source_identity_sha256") != source_identity_sha256:
        raise common.PipelineError("hardening source identity changed or needs legacy lineage review")
    if parent["task_sha256"] == task_sha256:
        if parent.get("task_id") != task_id:
            raise common.PipelineError("one task hash cannot have two task IDs")
        return {key: parent[key] for key in ("revision_index", "parent_task_sha256",
                                              "hardening_sha256", "mechanism_id") if key in parent}
    if len(versions) >= 3 or len(versions) > 1 and any("revision_index" not in row for row in versions):
        raise common.PipelineError("hardening limit reached or legacy identities need lineage review")
    revision = parent.get("revision_index", 0)
    if type(revision) is not int or revision not in (0, 1) or parent.get("status") not in {"revise", "review-needed"}:
        raise common.PipelineError("hardening needs a measured and reviewable parent")
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("hardening needs authoring/hardening.json")
    claim = common.load_json(path)
    keys = {"schema_version", "seed_id", "revision_index", "parent_task_sha256",
            "parent_task_id", "causal_delta", "source_grounding",
            "failure_observable", "mechanism_id", "evidence"}
    if (set(claim) != keys or type(claim.get("schema_version")) is not int
            or claim["schema_version"] != 1 or claim["seed_id"] != seed_id
            or type(claim["revision_index"]) is not int or claim["revision_index"] != revision + 1
            or claim["parent_task_sha256"] != parent["task_sha256"]
            or claim["parent_task_id"] != parent.get("task_id") or task_id == parent.get("task_id")):
        raise common.PipelineError("hardening parent hash, sequence or task identity mismatch")
    if (not _text(claim["causal_delta"]) or len(claim["causal_delta"].strip()) < 30
            or not _text(claim["source_grounding"]) or len(claim["source_grounding"].strip()) < 15
            or not _text(claim["failure_observable"]) or len(claim["failure_observable"].strip()) < 15):
        raise common.PipelineError("hardening causal_delta, source_grounding or failure_observable is cosmetic")
    mechanism = claim["mechanism_id"]
    if (not isinstance(mechanism, str)
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", mechanism) is None
            or parent.get("mechanism_id") == mechanism):
        raise common.PipelineError("hardening needs a distinct named causal mechanism")
    evidence = claim["evidence"]
    if (not isinstance(evidence, list) or not evidence or len(evidence) > 8
            or len(evidence) != len(set(evidence)) or not all(isinstance(v, str) for v in evidence)):
        raise common.PipelineError("hardening needs distinct measured evidence receipts")
    if diagnostic.get("task_sha256") != task_sha256 or diagnostic.get("status") not in {"revise", "review-needed"}:
        raise common.PipelineError("hardening needs the current candidate-audit diagnosis")
    probes = diagnostic.get("ablation_probes")
    if not isinstance(probes, list):
        raise common.PipelineError("hardening candidate lacks measured ablation probes")
    found = False
    for value in evidence:
        relative = PurePosixPath(value)
        if (not value.startswith("evidence/") or relative.is_absolute() or ".." in relative.parts
                or str(relative) != value):
            raise common.PipelineError("hardening evidence path escapes task workspace")
        candidate = task_root.joinpath(*relative.parts)
        if not candidate.is_file() or any(part.is_symlink() for part in (candidate, *candidate.parents)
                                                if part.is_relative_to(task_root)):
            raise common.PipelineError("hardening evidence is missing or symlinked")
        digest = common.sha256_file(candidate)
        if any(probe.get("mechanism_id") == mechanism and probe.get("receipt") == value
               and probe.get("receipt_sha256") == digest
               and common.finite_number(probe.get("score_effect")) and probe["score_effect"] > 0
               for probe in probes):
            found = True
    if not found:
        raise common.PipelineError("hardening mechanism lacks an audited positive matched probe")
    return {"revision_index": revision + 1,
            "parent_task_sha256": parent["task_sha256"],
            "hardening_sha256": common.sha256_file(path),
            "mechanism_id": mechanism}


def register_candidate(workspace: Path, seed_id: str, task_workspace: Path) -> tuple[dict, int]:
    """Retain every task-version diagnostic without claiming seed promotion."""
    import topify_validate

    root, _ = load_campaign(workspace)
    if (root / "seed-family-decisions" / _safe_name(seed_id)).exists():
        raise common.PipelineError("seed was shelved; register a new Seed identity instead")
    name = _safe_name(seed_id)
    screened = root / "screened" / name
    provisional = root / "provisionals" / name
    seed_path = screened if screened.is_file() else provisional
    card = common.load_json(seed_path)
    if card.get("seed_id") != seed_id or card.get("status") not in {
        "rights-and-dedup-screened", "prototype-candidate",
    }:
        raise common.PipelineError("candidate needs a reviewed screened or provisional Seed Card")
    if _hardening_identity_limit_reached(root, seed_id):
        candidate_task = Path(task_workspace) / "task"
        same_scored_identity = (candidate_task.is_dir() and not candidate_task.is_symlink()
                                and not _hardening_identity_limit_reached(
                                    root, seed_id, topify_validate.task_sha256(candidate_task)))
        if not same_scored_identity:
            raise common.PipelineError(
                "initial candidate plus two constrained hardening identities already measured; shelve or choose a different Seed"
            )
    task_root, metadata = common.load_workspace(task_workspace)
    request = metadata["request"]
    if request["category"] != card["category"]:
        raise common.PipelineError("candidate task category does not match the Seed Card")
    origins = card.get("origins", [])
    if not _candidate_source_bound(card, request):
        raise common.PipelineError("candidate request is not bound to the Seed Card's pinned source revision")
    source_identity_sha256 = _source_identity_sha256(card)
    task_sha256 = topify_validate.task_sha256(task_root / "task")
    validation = common.load_json(task_root / "evidence/harbor_validation.json")
    if validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256:
        raise common.PipelineError("candidate requires current internal task validation")
    paths = sorted((task_root / "evidence/candidate-audits").glob("attempt-*/report.json"))
    diagnostic = common.load_json(paths[-1]) if paths else {}
    if diagnostic:
        payload = {key: value for key, value in diagnostic.items() if key != "report_payload_sha256"}
        if (diagnostic.get("task_sha256") != task_sha256
                or diagnostic.get("report_payload_sha256") != common.canonical_sha256(payload)):
            raise common.PipelineError("candidate diagnostic does not match current task bytes")
    status = diagnostic.get("status", "incomplete")
    if status not in {"incomplete", "revise", "review-needed"}:
        raise common.PipelineError("unrecognized candidate diagnostic status")
    lineage = validate_hardening_lineage(root, seed_id, task_root, task_sha256,
                                          request["task_id"], diagnostic,
                                          source_identity_sha256=source_identity_sha256)
    destination = root / "candidates" / name.removesuffix(".json")
    number, attempt = common.next_attempt(destination)
    report = {
        "schema_version": 1, "attempt": number,
        "seed_id": seed_id, "category": card["category"],
        "source_screen_status": card["status"],
        "seed_sha256": common.sha256_file(seed_path),
        "source_identity_sha256": source_identity_sha256,
        "task_id": request["task_id"], "task_sha256": task_sha256,
        **lineage,
        "task_workspace": str(task_root),
        "status": status,
        "candidate_audit_sha256": common.sha256_file(paths[-1]) if paths else None,
        "next_action": (
            "return to author for causal redesign on a new task identity"
            if status == "revise" else
            "main-agent review of measured learning loop; no automatic promotion"
            if status == "review-needed" else
            "run generic controls, then candidate-audit on this exact task version"
        ),
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "report.json", report)
    _append_campaign_event(root, "candidate_recorded", seed_id=seed_id,
                           task_sha256=task_sha256, status=status)
    return report, 0 if status == "review-needed" else 2


def register_prototype(workspace: Path, seed_id: str, task_workspace: Path) -> tuple[dict, int]:
    import topify_validate

    root, _ = load_campaign(workspace)
    screened_path = root / "screened" / _safe_name(seed_id)
    screened = common.load_json(screened_path)
    if screened.get("seed_id") != seed_id or screened.get("status") != "rights-and-dedup-screened":
        raise common.PipelineError("seed must pass rights and dedup screening before prototyping")
    task_root, task_metadata = common.load_workspace(task_workspace)
    request = task_metadata["request"]
    task_design = common.load_json(task_root / "task_design.json")
    validation = common.load_json(task_root / "evidence/harbor_validation.json")
    current_task_sha256 = topify_validate.task_sha256(task_root / "task")
    measurement_paths = sorted(
        (task_root / "evidence/measurements").glob("attempt-*/summary.json"),
        reverse=True,
    )
    measurement = common.load_json(measurement_paths[0]) if measurement_paths else None
    checks = []

    def add(check_id: str, status: str, detail: str) -> None:
        checks.append(_result(check_id, status, detail))

    add(
        "prototype.category",
        "complete" if request.get("category") == screened.get("category") else "failed",
        "Prototype request category must match the screened Seed Card.",
    )
    design_ok = (
        task_design.get("request_sha256") == task_metadata.get("request_sha256")
        and isinstance(task_design.get("design_sha256"), str)
    )
    add(
        "prototype.design_binding",
        "complete" if design_ok else "failed",
        "Task design must remain content-bound to the prototype request.",
    )
    validation_ok = (
        validation.get("verdict") == "complete"
        and validation.get("task_sha256") == current_task_sha256
    )
    add(
        "prototype.harbor_validation",
        "complete" if validation_ok else "failed",
        "Generated Harbor bytes must have a current complete DOCX validation.",
    )
    if request.get("plugin") != "mini_continuous":
        candidate_paths = sorted((root / "candidates" / _safe_name(seed_id).removesuffix(".json")).glob("attempt-*/report.json"))
        matching = [common.load_json(path) for path in candidate_paths
                    if common.load_json(path).get("task_sha256") == current_task_sha256]
        latest_candidate = matching[-1] if matching else {}
        candidate_status = latest_candidate.get("status")
        add(
            "prototype.generic_control_screen",
            "complete" if candidate_status == "review-needed" else
            "failed" if candidate_status == "revise" else "incomplete",
            "A real source-derived task needs current, receipt-backed generic controls and main-agent review; cheap saturation requires revision.",
        )
        add(
            "prototype.same_family_two_hardenings",
            "complete" if _two_hardenings_measured(root, seed_id, current_task_sha256)
            else "incomplete",
            "A real task family needs an initial measured candidate and two parent-bound causal revisions before promotion.",
        )
    if measurement is None:
        add(
            "prototype.executed_measurement",
            "incomplete",
            "A real local NOP, score ladder, and five-repeat execution is still required.",
        )
        measurement_ok = False
        process_ok = False
    else:
        measure_checks = {
            item.get("id"): item.get("status")
            for item in measurement.get("checks", [])
            if isinstance(item, dict)
        }
        required_checks = {
            "measure.nop_script",
            "measure.process_observation",
            "measure.nop",
            "measure.interior_ladder",
            "measure.reference_repeat",
            "measure.timing",
        }
        if request.get("reference_mode") == "reference":
            required_checks.add("measure.reference_script")
        measurement_ok = (
            measurement.get("task_sha256") == current_task_sha256
            and required_checks.issubset(measure_checks)
            and all(measure_checks[item] == "complete" for item in required_checks)
        )
        add(
            "prototype.executed_measurement",
            "complete" if measurement_ok else "failed",
            "Real local NOP, interior ladder, timing, and five-repeat evidence must pass.",
        )
        attempt_value = measurement.get("attempt")
        run_root = task_root / str(attempt_value) / "runs" if isinstance(attempt_value, str) else task_root / "missing"
        try:
            run_root.resolve(strict=True).relative_to(task_root.resolve(strict=True))
            process_paths = sorted(run_root.glob("*/process.json"))
        except (OSError, ValueError):
            process_paths = []
        expected_processes = 10 if request.get("reference_mode") == "reference" else 9
        process_ok = len(process_paths) == expected_processes and all(
            common.load_json(path).get("exit_code") == 0 for path in process_paths
        )
        add(
            "prototype.process_receipts",
            "complete" if process_ok else "failed",
            "Prototype evidence must contain executable snapshot scripts and eight successful scoring processes.",
        )
    statuses = {item["status"] for item in checks}
    verdict = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "complete"
    report = {
        "schema_version": 1,
        "seed_id": seed_id,
        "category": screened["category"],
        "verdict": verdict,
        "checks": checks,
        "seed_screen_sha256": common.sha256_file(screened_path),
        "task_workspace": str(task_root),
        "request_sha256": task_metadata["request_sha256"],
        "design_sha256": task_design.get("design_sha256"),
        "task_sha256": current_task_sha256,
        "measurement_sha256": common.canonical_sha256(measurement) if measurement is not None else None,
        "request": request,
        "design": {
            key: value
            for key, value in task_design.items()
            if key not in {"request_sha256", "design_sha256"}
        },
        "source_identity": {
            "upstream_ids": screened["dedup"]["upstream_ids"],
            "dataset_ids": screened["dedup"]["dataset_ids"],
            "objective_signature": screened["dedup"]["objective_signature"],
        },
        "registered_at": common.utc_now(),
    }
    # An incomplete review is an append-only attempt, not the immutable
    # promoted-candidate slot. Otherwise a premature call permanently blocks
    # the same Seed after its two causal revisions are measured.
    _, attempt = common.next_attempt(root / "prototype-attempts" /
                                     _safe_name(seed_id).removesuffix(".json"))
    common.write_new_json(attempt / "report.json", report)
    if verdict == "complete":
        common.write_new_json(root / "prototypes" / _safe_name(seed_id), report)
    _append_campaign_event(root, "seed_prototyped", seed_id=seed_id, verdict=verdict)
    return report, 0 if verdict == "complete" else 2


def promote_seed(workspace: Path, seed_id: str) -> tuple[dict, int]:
    root, campaign = load_campaign(workspace)
    if (root / "seed-family-decisions" / _safe_name(seed_id)).exists():
        raise common.PipelineError("seed was shelved; refusing promotion")
    prototype_path = root / "prototypes" / _safe_name(seed_id)
    prototype = common.load_json(prototype_path)
    if prototype.get("seed_id") != seed_id or prototype.get("verdict") != "complete":
        raise common.PipelineError("only a complete measured prototype can be promoted")
    identity = prototype.get("source_identity")
    if not isinstance(identity, dict):
        raise common.PipelineError("prototype source identity is missing")
    upstream_counts = Counter()
    dataset_counts = Counter()
    objective_signatures = set()
    for path in sorted((root / "promoted").glob("*.json")):
        existing = common.load_json(path).get("source_identity", {})
        upstream_counts.update(existing.get("upstream_ids", []))
        dataset_counts.update(existing.get("dataset_ids", []))
        objective_signatures.add(existing.get("objective_signature"))
    if identity.get("objective_signature") in objective_signatures:
        raise common.PipelineError("objective/verifier signature is already promoted")
    if any(
        upstream_counts[item] >= campaign["config"]["max_promoted_per_upstream"]
        for item in identity.get("upstream_ids", [])
    ):
        raise common.PipelineError("campaign upstream promotion cap would be exceeded")
    if any(
        dataset_counts[item] >= campaign["config"]["max_promoted_per_dataset"]
        for item in identity.get("dataset_ids", [])
    ):
        raise common.PipelineError("campaign dataset promotion cap would be exceeded")
    promotion = {
        "schema_version": 1,
        "state": "PROMOTED",
        "seed_id": seed_id,
        "category": prototype["category"],
        "prototype_sha256": common.sha256_file(prototype_path),
        "task_sha256": prototype["task_sha256"],
        "request": prototype["request"],
        "design": prototype["design"],
        "source_identity": identity,
        "promoted_at": common.utc_now(),
    }
    common.write_new_json(root / "promoted" / _safe_name(seed_id), promotion)
    _append_campaign_event(root, "seed_promoted", seed_id=seed_id, task_sha256=prototype["task_sha256"])
    return promotion, 0


def batch_nop(workspace: Path) -> tuple[dict, int]:
    import topify_measure
    import topify_validate

    root, campaign = load_campaign(workspace)
    promoted_paths = sorted((root / "promoted").glob("*.json"))
    if not promoted_paths:
        raise common.PipelineError("campaign has no promoted tasks to scan")
    attempt_number, attempt = common.next_attempt(root / "batch-nop")
    runs = attempt / "runs"
    runs.mkdir()
    tasks = []
    seen_task_hashes = set()
    for promoted_path in promoted_paths:
        promoted = common.load_json(promoted_path)
        prototype = common.load_json(root / "prototypes" / _safe_name(promoted["seed_id"]))
        task_root, metadata = common.load_workspace(prototype["task_workspace"])
        task = task_root / "task"
        task_sha256 = topify_validate.task_sha256(task)
        if task_sha256 != promoted.get("task_sha256") or task_sha256 in seen_task_hashes:
            tasks.append({
                "seed_id": promoted["seed_id"],
                "task_sha256": task_sha256,
                "status": "failed",
                "reason": "promoted task identity changed or is duplicated",
            })
            continue
        seen_task_hashes.add(task_sha256)
        output = runs / promoted["seed_id"].replace("/", "__")
        try:
            snapshot, script_observation = topify_measure.run_nop_script(
                task,
                output / "nop-script",
                metadata["request"]["verifier_timeout_seconds"],
            )
            score_report, score_observation = topify_measure._run(
                task,
                snapshot,
                output / "score",
                metadata["request"]["verifier_timeout_seconds"],
            )
            score = float(score_report["total_score"])
            baseline = float(metadata["request"]["scoring"]["baseline"])
            passed = score == baseline
            tasks.append({
                "seed_id": promoted["seed_id"],
                "task_id": metadata["request"]["task_id"],
                "task_sha256": task_sha256,
                "status": "complete" if passed else "failed",
                "score": score,
                "baseline": baseline,
                "nop_script_sha256": script_observation["script_sha256"],
                "snapshot_sha256": script_observation["snapshot_sha256"],
                "script_process_sha256": common.sha256_file(output / "nop-script/process.json"),
                "score_process_sha256": common.sha256_file(output / "score/process.json"),
            })
        except (common.PipelineError, OSError, ValueError) as exc:
            tasks.append({
                "seed_id": promoted["seed_id"],
                "task_sha256": task_sha256,
                "status": "failed",
                "reason": str(exc),
            })
    complete = bool(tasks) and all(item["status"] == "complete" for item in tasks)
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "campaign_id": campaign["config"]["campaign_id"],
        "status": "complete" if complete else "failed",
        "task_count": len(tasks),
        "task_sha256s": sorted(item["task_sha256"] for item in tasks),
        "tasks": tasks,
        "scanned_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    common.write_latest_json(attempt.parent / "latest.json", report)
    campaign_report_sha256 = common.sha256_file(attempt / "summary.json")
    for item in tasks:
        if item["status"] != "complete":
            continue
        prototype = common.load_json(root / "prototypes" / _safe_name(item["seed_id"]))
        task_root, _ = common.load_workspace(prototype["task_workspace"])
        task_attempt_number, task_attempt = common.next_attempt(task_root / "evidence/batch-nop")
        task_report = {
            "schema_version": 1,
            "attempt": task_attempt_number,
            "status": "complete",
            "task_sha256": item["task_sha256"],
            "task_sha256s": report["task_sha256s"],
            "campaign_id": report["campaign_id"],
            "campaign_attempt": attempt_number,
            "campaign_report_sha256": campaign_report_sha256,
            "score": item["score"],
            "baseline": item["baseline"],
            "recorded_at": common.utc_now(),
        }
        common.write_new_json(task_attempt / "summary.json", task_report)
        common.write_latest_json(task_attempt.parent / "latest.json", task_report)
        common.append_event(task_root, "batch_nop_scanned", campaign_id=report["campaign_id"])
    _append_campaign_event(root, "batch_nop_scanned", attempt=attempt_number, status=report["status"])
    return report, 0 if complete else 2


def campaign_status(workspace: str | Path) -> dict:
    root, campaign = load_campaign(workspace)

    def counts(directory: str) -> dict:
        values = Counter()
        for path in sorted((root / directory).glob("*.json")):
            try:
                value = common.load_json(path)
            except common.PipelineError:
                continue
            category = value.get("category")
            if category in common.CATEGORIES:
                values[category] += 1
        return dict(sorted(values.items()))

    stages = {
        "leads": counts("leads"),
        "shelved": counts("seed-family-decisions"),
        "provisionals": counts("provisionals"),
        "screened": counts("screened"),
        "prototypes": counts("prototypes"),
        "promoted": counts("promoted"),
    }
    candidate_counts = Counter()
    candidate_seed_ids = {category: set() for category in common.CATEGORIES}
    candidate_outcomes = Counter()
    candidate_latest = {}
    for path in sorted((root / "candidates").glob("*/attempt-*/report.json")):
        try:
            value = common.load_json(path)
        except common.PipelineError:
            continue
        if value.get("category") in common.CATEGORIES and isinstance(value.get("task_sha256"), str):
            candidate_latest[value["task_sha256"]] = value
    for value in candidate_latest.values():
        candidate_counts[value["category"]] += 1
        candidate_outcomes[value.get("status", "unknown")] += 1
        if isinstance(value.get("seed_id"), str) and SEED_ID.fullmatch(value["seed_id"]):
            candidate_seed_ids[value["category"]].add(value["seed_id"])
    stages["candidates"] = dict(sorted(candidate_counts.items()))
    stages["candidate_seeds"] = {
        category: len(identifiers) for category, identifiers in sorted(candidate_seed_ids.items())
        if identifiers
    }
    targets = {
        "leads": campaign["lead_targets"],
        "provisionals": campaign["screened_targets"],
        "screened": campaign["screened_targets"],
        "candidates": campaign["prototype_targets"],
        "prototypes": campaign["prototype_targets"],
        "promoted": campaign["config"]["category_targets"],
    }
    shortfalls = {
        stage: {
            category: max(0, target - stages[
                "candidate_seeds" if stage == "candidates" else stage
            ].get(category, 0))
            for category, target in stage_targets.items()
        }
        for stage, stage_targets in targets.items()
    }
    next_stage = "complete"
    for stage, action in (
        ("leads", "scout"),
        ("screened", "seed-screen"),
        ("prototypes", "seed-prototype"),
        ("promoted", "seed-promote"),
    ):
        if any(shortfalls[stage].values()):
            next_stage = action
            break
    return {
        "campaign_id": campaign["config"]["campaign_id"],
        "counts": stages,
        "candidate_outcomes": dict(sorted(candidate_outcomes.items())),
        "targets": targets,
        "shortfalls": shortfalls,
        "next_stage": next_stage,
    }
