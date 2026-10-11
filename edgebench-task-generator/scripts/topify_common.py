#!/usr/bin/env python3
"""Shared primitives for the DOCX-only EdgeBench task generator."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time


DOCX_SHA256 = "1c4f513f9bb561e1a3abafbf6ec76576d2ef9088ce62468afe5a072eec8cad6b"
CATEGORIES = frozenset({
    "systems-software-engineering",
    "scientific-ml",
    "professional-knowledge-work",
    "combinatorial-optimization",
    "formal-math-theorem-proving",
    "interactive-games-simulators",
})
REFERENCE_MODES = frozenset({"reference", "open-solution"})
ARCHITECTURES = frozenset({"linux/amd64", "linux/arm64"})
TASK_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*/[A-Za-z][A-Za-z0-9_-]*\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class PipelineError(ValueError):
    """Raised when authoring input or evidence is unsafe or malformed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sanitized_env() -> dict[str, str]:
    """Return the small credential-free environment allowed for infrastructure tools."""
    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "XDG_RUNTIME_DIR",
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "BUILDX_BUILDER",
        "SSL_CERT_FILE", "SSL_CERT_DIR",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment.setdefault("LANG", "C.UTF-8")
    environment.setdefault("LC_ALL", "C.UTF-8")
    return environment


def finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _regular_file(path: str | Path) -> Path:
    candidate = Path(path)
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        raise PipelineError(f"missing file: {candidate}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise PipelineError(f"path must be a regular non-symlink file: {candidate}")
    return candidate.resolve(strict=True)


def load_json(path: str | Path) -> dict:
    source = _regular_file(path)

    def reject_constant(value: str) -> None:
        raise PipelineError(f"nonfinite JSON constant is forbidden: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise PipelineError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except PipelineError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise PipelineError(f"cannot parse JSON object {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON root must be an object: {source}")
    canonical_sha256(value)
    return value


def canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"value is not finite canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str | Path) -> str:
    source = _regular_file(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new_json(path: str | Path, value: object) -> None:
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise PipelineError(f"refusing to overwrite existing path: {destination}")
    canonical_sha256(value)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except OSError as exc:
        raise PipelineError(f"cannot write {destination}: {exc}") from exc


def write_latest_json(path: str | Path, value: object) -> None:
    """Atomically update a convenience pointer while immutable attempts retain history."""
    destination = Path(path)
    canonical_sha256(value)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PipelineError(f"cannot update {destination}: {exc}") from exc


def next_attempt(parent: str | Path) -> tuple[int, Path]:
    directory = Path(parent)
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        candidate = directory / f"attempt-{number:04d}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise PipelineError(f"cannot create evidence attempt: {exc}") from exc
        return number, candidate
    raise PipelineError(f"too many evidence attempts below {directory}")


def append_event(workspace: str | Path, event: str, **fields: object) -> None:
    root = Path(workspace).resolve(strict=True)
    path = root / "evidence/events.jsonl"
    record = {"event": event, "recorded_at": utc_now(), **fields}
    canonical_sha256(record)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    except OSError as exc:
        raise PipelineError(f"cannot append authoring event: {exc}") from exc


def budget_assessment(seconds: int, *, legacy_edgebench_floor: bool = True) -> str:
    if type(seconds) is not int:
        raise PipelineError("interaction_budget_seconds must be an integer")
    if seconds <= 0:
        raise PipelineError("interaction budget must be positive")
    if seconds < 3600 and legacy_edgebench_floor:
        raise PipelineError("interaction budget below one hour is rejected")
    if seconds < 3600:
        return "profile-dependent"
    if seconds < 10800:
        return "below-acceptable-lower-bound"
    if seconds == 10800:
        return "acceptable-lower-bound"
    if seconds < 43200:
        return "acceptable"
    if seconds == 43200:
        return "preferred"
    return "above-preferred"


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{label} must be a nonempty string")
    return value


def validate_request(request: dict) -> dict:
    required = {
        "schema_version", "task_id", "category", "plugin", "seed", "source",
        "resources", "interaction_budget_seconds", "verifier_timeout_seconds",
        "reference_mode", "scoring", "rollout",
    }
    allowed = required | {"benchmark_profile"}
    if set(request) not in (required, allowed):
        raise PipelineError(f"authoring request keys must be exactly {sorted(required)} or include benchmark_profile")
    if request.get("schema_version") != 1 or type(request.get("schema_version")) is not int:
        raise PipelineError("schema_version must be integer 1")
    task_id = _nonempty_string(request.get("task_id"), "task_id")
    if not TASK_ID.fullmatch(task_id):
        raise PipelineError("task_id must use project/task slugs")
    if request.get("category") not in CATEGORIES:
        raise PipelineError(f"category must be one of {sorted(CATEGORIES)}")
    plugin = _nonempty_string(request.get("plugin"), "plugin")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", plugin):
        raise PipelineError("plugin must be a lowercase Python slug")
    if type(request.get("seed")) is not int:
        raise PipelineError("seed must be an integer")
    declared_profile = request.get("benchmark_profile")
    if declared_profile is not None and (not isinstance(declared_profile, str) or not declared_profile.strip()):
        raise PipelineError("benchmark_profile must be a nonempty string when supplied")
    assessment = budget_assessment(
        request.get("interaction_budget_seconds"),
        legacy_edgebench_floor=declared_profile in (None, "edgebench-v1"),
    )
    timeout = request.get("verifier_timeout_seconds")
    if type(timeout) is not int or timeout <= 0:
        raise PipelineError("verifier_timeout_seconds must be a positive integer")
    if request.get("reference_mode") not in REFERENCE_MODES:
        raise PipelineError("reference_mode must be reference or open-solution")

    resources = request.get("resources")
    resource_keys = {
        "agent_cpu", "agent_memory_mb", "verifier_cpu", "verifier_memory_mb",
        "gpu_types", "scoring_uses_gpu", "architectures",
    }
    if not isinstance(resources, dict) or set(resources) != resource_keys:
        raise PipelineError(f"resources keys must be exactly {sorted(resource_keys)}")
    for key in ("agent_cpu", "agent_memory_mb", "verifier_cpu", "verifier_memory_mb"):
        if type(resources.get(key)) is not int or resources[key] <= 0:
            raise PipelineError(f"resources.{key} must be a positive integer")
    if not isinstance(resources.get("gpu_types"), list) or not all(
        isinstance(item, str) and item for item in resources["gpu_types"]
    ):
        raise PipelineError("resources.gpu_types must be a list of strings")
    if type(resources.get("scoring_uses_gpu")) is not bool:
        raise PipelineError("resources.scoring_uses_gpu must be Boolean")
    architectures = resources.get("architectures")
    if not isinstance(architectures, list) or not architectures or not all(isinstance(item, str) for item in architectures):
        raise PipelineError("resources.architectures must be a nonempty list of architecture names")
    if request.get("benchmark_profile") in (None, "edgebench-v1") and set(architectures) != ARCHITECTURES:
        raise PipelineError("EdgeBench resources.architectures must contain linux/amd64 and linux/arm64")

    source = request.get("source")
    source_keys = {
        "mode", "upstream_projects", "public_datasets", "license",
        "redistribution_confirmation", "dedup_targets",
    }
    if not isinstance(source, dict) or set(source) != source_keys:
        raise PipelineError(f"source keys must be exactly {sorted(source_keys)}")
    if source.get("mode") not in {"original-generator", "source-derived"}:
        raise PipelineError("source.mode must be original-generator or source-derived")
    for key in ("upstream_projects", "public_datasets", "dedup_targets"):
        if not isinstance(source.get(key), list) or not all(isinstance(item, str) for item in source[key]):
            raise PipelineError(f"source.{key} must be a list of strings")
    if not isinstance(source.get("license"), str) or not isinstance(source.get("redistribution_confirmation"), str):
        raise PipelineError("source license and redistribution fields, if supplied, must be strings")
    if source["mode"] == "source-derived" and not source["upstream_projects"]:
        raise PipelineError("source-derived tasks require at least one upstream project")

    scoring = request.get("scoring")
    scoring_keys = {
        "direction", "raw_metric", "formula", "constants", "baseline", "upper",
        "upper_basis", "batch_mechanism",
    }
    if not isinstance(scoring, dict) or set(scoring) != scoring_keys:
        raise PipelineError(f"scoring keys must be exactly {sorted(scoring_keys)}")
    if scoring.get("direction") not in {"maximize", "minimize"}:
        raise PipelineError("scoring.direction must be maximize or minimize")
    for key in ("raw_metric", "formula", "upper_basis", "batch_mechanism"):
        _nonempty_string(scoring.get(key), f"scoring.{key}")
    if scoring["batch_mechanism"] != "run-once":
        raise PipelineError("this delivery batch must use the run-once scoring mechanism")
    if not isinstance(scoring.get("constants"), dict) or not scoring["constants"]:
        raise PipelineError("scoring.constants must be a nonempty object")
    baseline, upper = scoring.get("baseline"), scoring.get("upper")
    if not finite_number(baseline) or not finite_number(upper) or baseline == upper:
        raise PipelineError("scoring baseline and upper must be distinct finite numbers")
    if scoring["direction"] == "maximize" and not baseline < upper:
        raise PipelineError("maximize scoring requires baseline < upper")
    if scoring["direction"] == "minimize" and not baseline > upper:
        raise PipelineError("minimize scoring requires baseline > upper")

    rollout = request.get("rollout")
    rollout_keys = {"model", "agent", "provider", "effort"}
    if not isinstance(rollout, dict) or set(rollout) != rollout_keys:
        raise PipelineError(f"rollout keys must be exactly {sorted(rollout_keys)}")
    for key in rollout_keys:
        _nonempty_string(rollout.get(key), f"rollout.{key}")

    canonical_sha256(request)
    return {"request": request, "budget_assessment": assessment}


def load_workspace(workspace: str | Path) -> tuple[Path, dict]:
    root = Path(workspace)
    if not root.is_absolute():
        root = root.absolute()
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PipelineError(f"workspace does not exist: {root}") from exc
    if root != resolved or root.is_symlink() or not root.is_dir():
        raise PipelineError("workspace must be a canonical real directory")
    data = load_json(root / "workspace.json")
    if data.get("schema_version") != 1 or data.get("docx_sha256") != DOCX_SHA256:
        raise PipelineError("workspace is not a DOCX-only EdgeBench workspace")
    validated = validate_request(data.get("request"))
    if data.get("task_id") != validated["request"]["task_id"]:
        raise PipelineError("workspace task identity does not match its request")
    if data.get("request_sha256") != canonical_sha256(validated["request"]):
        raise PipelineError("workspace request hash mismatch")
    return root, data


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
