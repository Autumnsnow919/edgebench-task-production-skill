"""Copy a domain-authored family from a hashed, isolated workspace source.

Unlike importing arbitrary Python supplied by a scout, this adapter only
copies author-written files after checking their paths, modes and hashes.
The existing pipeline then validates and executes the generated task.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
import stat

import topify_common as common


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\n" in value:
        raise common.PipelineError("author source path must be a relative POSIX file path")
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in (".", "..", "") for part in path.parts)
            or str(path) != value or path.parts[0] not in
            {"environment", "tests", "solution", "nop"}
            and value not in {"task.toml", "instruction.md"}):
        raise common.PipelineError("author source path escapes the task-family contexts")
    return value


def prepare(staging: Path, request: dict, design: dict, seed: int) -> dict:
    del seed
    authoring = staging.parent / "authoring"
    if not authoring.is_dir() or authoring.is_symlink():
        raise common.PipelineError("authored_context needs a real authoring directory")
    source = authoring / "task-source"
    if not source.is_dir() or source.is_symlink():
        raise common.PipelineError("authored_context requires authoring/task-source")
    manifest_path = source / "manifest.json"
    manifest = common.load_json(manifest_path)
    expected = {"schema_version", "files", "private_files",
                "snapshot_artifacts", "reference_mode"}
    if (set(manifest) != expected or manifest["schema_version"] != 1
            or manifest["reference_mode"] != request["reference_mode"]
            or manifest["snapshot_artifacts"] != design.get("snapshot_artifacts")):
        raise common.PipelineError("author family manifest is not bound to task design and request")
    records = manifest["files"]
    if not isinstance(records, list) or not records:
        raise common.PipelineError("author family manifest needs source file hashes")
    names = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise common.PipelineError("author family file record needs path and sha256")
        name = _relative(record["path"])
        if (not isinstance(record["sha256"], str)
                or not common.HEX64.fullmatch(record["sha256"])):
            raise common.PipelineError("author family file hash must be lowercase SHA-256")
        names.append(name)
    if names != sorted(set(names)):
        raise common.PipelineError("author family source records must be sorted and unique")
    private = manifest["private_files"]
    if (not isinstance(private, list) or not all(isinstance(name, str)
            and name.startswith("tests/") and name in names for name in private)
            or len(private) != len(set(private))):
        raise common.PipelineError("author family private files belong only to tests/")
    if not isinstance(manifest["snapshot_artifacts"], list):
        raise common.PipelineError("author family needs declared snapshot artifacts")
    for record in records:
        relative = Path(record["path"])
        item = source / relative
        parents = (source, *(source / Path(*relative.parts[:index])
                             for index in range(1, len(relative.parts))))
        if any(parent.is_symlink() or not parent.is_dir() for parent in parents):
            raise common.PipelineError("author family contains a linked or missing directory")
        try:
            mode = item.lstat().st_mode
        except OSError as exc:
            raise common.PipelineError("author family source file is missing") from exc
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise common.PipelineError("author family source must be a regular file")
        if common.sha256_file(item) != record["sha256"]:
            raise common.PipelineError("author family source hash drifted")
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        if common.sha256_file(destination) != record["sha256"]:
            raise common.PipelineError("author family changed during copy")
    return {"schema_version": 1, "files": names,
            "agent_context": "environment", "verifier_context": "tests",
            "private_files": private,
            "snapshot_artifacts": manifest["snapshot_artifacts"],
            "reference_mode": manifest["reference_mode"],
            "source_manifest_sha256": common.sha256_file(manifest_path)}
