#!/usr/bin/env python3
"""Build and verify content-addressed multi-platform Harbor images."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import shlex
import subprocess
import tarfile
import time
import tomllib


REQUIRED_PLATFORMS = {"linux/amd64", "linux/arm64"}
MANIFEST_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
INDEX_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_bytes(value: bytes, label: str) -> dict:
    try:
        result = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{label} must be a JSON object")
    return result


def _read_safe_tar(path: Path) -> dict[str, bytes]:
    members = {}
    try:
        archive = tarfile.open(path, "r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"cannot open OCI archive: {exc}") from exc
    with archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe OCI archive member: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"OCI archive member is not a regular file: {member.name}")
            normalized = str(name)
            if normalized in members:
                raise ValueError(f"duplicate OCI archive member: {normalized}")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read OCI archive member: {normalized}")
            value = handle.read()
            if len(value) != member.size:
                raise ValueError(f"short OCI archive member: {normalized}")
            members[normalized] = value
    return members


def _descriptor_blob(members: dict[str, bytes], descriptor: dict, label: str) -> bytes:
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError(f"{label} has unsupported digest")
    hexdigest = digest.removeprefix("sha256:")
    if len(hexdigest) != 64 or any(ch not in "0123456789abcdef" for ch in hexdigest):
        raise ValueError(f"{label} has invalid sha256 digest")
    member_name = f"blobs/sha256/{hexdigest}"
    if member_name not in members:
        raise ValueError(f"missing OCI blob for {label}: {digest}")
    value = members[member_name]
    if _sha256(value) != hexdigest:
        raise ValueError(f"OCI blob digest mismatch for {label}: {digest}")
    if not isinstance(size, int) or isinstance(size, bool) or size != len(value):
        raise ValueError(f"OCI blob size mismatch for {label}: {digest}")
    return value


def _platform_name(descriptor: dict) -> str | None:
    platform = descriptor.get("platform")
    if not isinstance(platform, dict):
        return None
    os_name = platform.get("os")
    architecture = platform.get("architecture")
    if not isinstance(os_name, str) or not isinstance(architecture, str):
        return None
    return f"{os_name}/{architecture}"


def _walk_descriptor(
    members: dict[str, bytes],
    descriptor: dict,
    label: str,
    inherited_platform: str | None,
    platforms: set[str],
    platform_digests: dict[str, str],
    verified: set[str],
) -> None:
    value = _descriptor_blob(members, descriptor, label)
    digest = descriptor["digest"]
    verified.add(digest)
    document = _load_json_bytes(value, label)
    media_type = descriptor.get("mediaType") or document.get("mediaType")
    platform = _platform_name(descriptor) or inherited_platform
    if media_type in INDEX_MEDIA_TYPES:
        children = document.get("manifests")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{label} image index has no manifests")
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                raise ValueError(f"{label} manifest descriptor {index} must be an object")
            _walk_descriptor(
                members,
                child,
                f"{label}.manifests[{index}]",
                _platform_name(child) or platform,
                platforms,
                platform_digests,
                verified,
            )
        return
    if media_type not in MANIFEST_MEDIA_TYPES:
        raise ValueError(f"{label} has unsupported media type: {media_type}")
    config = document.get("config")
    layers = document.get("layers")
    if not isinstance(config, dict) or not isinstance(layers, list):
        raise ValueError(f"{label} is missing config or layers")
    config_value = _descriptor_blob(members, config, f"{label}.config")
    verified.add(config["digest"])
    config_document = _load_json_bytes(config_value, f"{label}.config")
    config_platform = None
    if isinstance(config_document.get("os"), str) and isinstance(config_document.get("architecture"), str):
        config_platform = f"{config_document['os']}/{config_document['architecture']}"
    if platform and config_platform and platform != config_platform:
        raise ValueError(f"{label} platform does not match image config")
    platform = platform or config_platform
    if platform:
        platforms.add(platform)
        platform_digests[platform] = digest
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ValueError(f"{label}.layers[{index}] must be an object")
        _descriptor_blob(members, layer, f"{label}.layers[{index}]")
        verified.add(layer["digest"])


def validate_oci_layout(path: str | Path) -> dict:
    """Validate an OCI archive without extracting it to the filesystem."""
    archive_path = Path(path).resolve()
    if not archive_path.is_file():
        raise ValueError(f"OCI archive does not exist: {archive_path}")
    members = _read_safe_tar(archive_path)
    if "oci-layout" not in members or "index.json" not in members:
        raise ValueError("OCI archive must contain oci-layout and index.json")
    layout = _load_json_bytes(members["oci-layout"], "oci-layout")
    if layout.get("imageLayoutVersion") != "1.0.0":
        raise ValueError("unsupported OCI image layout version")
    index = _load_json_bytes(members["index.json"], "index.json")
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError("OCI index has no manifests")
    platforms: set[str] = set()
    platform_digests: dict[str, str] = {}
    verified: set[str] = set()
    for item_index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict):
            raise ValueError(f"OCI index descriptor {item_index} must be an object")
        _walk_descriptor(
            members,
            descriptor,
            f"index.manifests[{item_index}]",
            _platform_name(descriptor),
            platforms,
            platform_digests,
            verified,
        )
    missing = REQUIRED_PLATFORMS - platforms
    if missing:
        raise ValueError("OCI layout is missing required platform(s): " + ", ".join(sorted(missing)))
    return {
        "archive": str(archive_path),
        "layout_sha256": _sha256_file(archive_path),
        "index_sha256": _sha256(members["index.json"]),
        "platforms": sorted(platforms),
        "platform_manifest_digests": dict(sorted(platform_digests.items())),
        "verified_blob_count": len(verified),
    }


def _base_references(task: Path) -> list[str]:
    references = set()
    for relative in ("environment/Dockerfile", "tests/Dockerfile"):
        dockerfile = task / relative
        try:
            lines = dockerfile.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot read {relative}: {exc}") from exc
        found = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = shlex.split(stripped)
            except ValueError as exc:
                raise ValueError(f"invalid Dockerfile syntax in {relative}: {exc}") from exc
            if not fields or fields[0].upper() != "FROM":
                continue
            images = [field for field in fields[1:] if not field.startswith("--")]
            if not images:
                raise ValueError(f"FROM in {relative} has no image")
            image = images[0]
            found = True
            if image.lower() == "scratch":
                continue
            if "@sha256:" not in image:
                raise ValueError(f"base image is not digest-pinned in {relative}: {image}")
            references.add(image)
        if not found:
            raise ValueError(f"{relative} contains no FROM instruction")
    return sorted(references)


def declared_build_network(task: Path) -> str:
    """Resolve build-only network access from the frozen task declaration."""
    with (task / "task.toml").open("rb") as handle:
        environment = tomllib.load(handle).get("environment", {})
    mode = environment.get("network_mode")
    if mode == "no-network":
        return "none"
    if mode == "public" and isinstance(environment.get("open_internet_justification"), str) \
            and environment["open_internet_justification"].strip():
        return "default"
    raise ValueError("online builds require a public build declaration with justification")


def _run_process(argv: list[str], cwd: Path, timeout_seconds: int, env: dict[str, str]) -> tuple[dict, bytes, bytes]:
    started = time.monotonic()
    native = platform.machine().lower()
    observed = ("linux/amd64" if native in {"x86_64", "amd64"} else
                "linux/arm64" if native in {"aarch64", "arm64"} else None)
    try:
        process = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        return ({
            "argv": argv,
            "cwd": str(cwd),
            "observed_host_architecture": observed,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "returncode": None,
            "error": "builder unavailable",
        }, b"", str(exc).encode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        return ({
            "argv": argv,
            "cwd": str(cwd),
            "observed_host_architecture": observed,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "returncode": None,
            "error": "timeout",
            "stdout_sha256": _sha256(stdout),
            "stderr_sha256": _sha256(stderr),
        }, stdout, stderr)
    record = {
        "argv": argv,
        "cwd": str(cwd),
        "observed_host_architecture": observed,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "returncode": process.returncode,
        "stdout_bytes": len(process.stdout),
        "stderr_bytes": len(process.stderr),
        "stdout_sha256": _sha256(process.stdout),
        "stderr_sha256": _sha256(process.stderr),
    }
    return record, process.stdout, process.stderr


def _manifest_list_evidence(raw: bytes, reference: str) -> dict:
    value = raw.rstrip(b"\r\n")
    document = _load_json_bytes(value, f"manifest list for {reference}")
    manifests = document.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError(f"base image is not a manifest list: {reference}")
    platforms = set()
    for descriptor in manifests:
        if isinstance(descriptor, dict):
            platform = _platform_name(descriptor)
            if platform:
                platforms.add(platform)
    missing = REQUIRED_PLATFORMS - platforms
    if missing:
        raise ValueError(
            f"base image manifest list is missing {', '.join(sorted(missing))}: {reference}"
        )
    expected = reference.rsplit("@sha256:", 1)[-1]
    actual = _sha256(value)
    if expected != actual:
        raise ValueError(f"base image manifest digest mismatch for {reference}")
    return {
        "reference": reference,
        "manifest_list_digest": "sha256:" + actual,
        "platforms": sorted(platforms),
    }


def build_task(
    workspace: str | Path,
    docker_executable: str = "docker",
    timeout_seconds: int = 3600,
) -> tuple[dict, int]:
    """Execute buildx and retain only content-addressed, validated build evidence."""
    import topify_common as common
    import topify_validate

    root, _ = common.load_workspace(workspace)
    task = root / "task"
    validation = common.load_json(root / "evidence/harbor_validation.json")
    current_task_sha256 = topify_validate.task_sha256(task)
    if validation.get("verdict") != "complete":
        raise common.PipelineError("current Harbor validation is not complete")
    if validation.get("task_sha256") != current_task_sha256:
        raise common.PipelineError("task changed after Harbor validation")
    network_mode = declared_build_network(task)
    attempt_number, attempt = common.next_attempt(root / "evidence/builds")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": "incomplete",
        "state": "HARBOR_VALIDATED",
        "task_sha256": current_task_sha256,
        "required_platforms": sorted(REQUIRED_PLATFORMS),
        "build_network": {
            "runtime_network": False,
            "dockerfile_run_network": network_mode,
            "external_dependency": "Fetch the digest-pinned base and declared build dependencies; runtime stays offline.",
        },
        "base_images": [],
        "images": [],
        "processes": [],
        "started_at": common.utc_now(),
    }

    def finish(reason: str, code: int = 2) -> tuple[dict, int]:
        report["reason"] = reason
        report["finished_at"] = common.utc_now()
        common.write_new_json(attempt / "summary.json", report)
        common.append_event(root, "build_attempt", attempt=attempt_number, status=report["status"])
        return report, code

    try:
        base_references = _base_references(task)
    except ValueError as exc:
        report["status"] = "failed"
        return finish(str(exc))

    env = common.sanitized_env()
    version_record, _, _ = _run_process(
        [docker_executable, "buildx", "version"], root, min(timeout_seconds, 60), env
    )
    report["processes"].append(version_record)
    if version_record.get("returncode") != 0:
        return finish("docker buildx is unavailable")

    for index, reference in enumerate(base_references):
        record, stdout, _ = _run_process(
            [docker_executable, "buildx", "imagetools", "inspect", "--raw", reference],
            root,
            min(timeout_seconds, 300),
            env,
        )
        report["processes"].append(record)
        if record.get("returncode") != 0:
            return finish(f"could not inspect pinned base image: {reference}")
        try:
            evidence = _manifest_list_evidence(stdout, reference)
        except ValueError as exc:
            report["status"] = "failed"
            return finish(str(exc))
        manifest_path = attempt / f"base-{index:02d}.manifest-list.json"
        manifest_path.write_bytes(stdout.rstrip(b"\r\n") + b"\n")
        evidence["capture_sha256"] = _sha256(manifest_path.read_bytes())
        report["base_images"].append(evidence)

    for name, context in (("agent", task / "environment"), ("verifier", task / "tests")):
        archive = attempt / f"{name}.oci.tar"
        output = f"type=oci,dest={archive}"
        argv = [
            docker_executable,
            "buildx",
            "build",
            "--platform",
            "linux/amd64,linux/arm64",
            "--network",
            network_mode,
            "--provenance=false",
            "--sbom=false",
            "--output",
            output,
            str(context),
        ]
        record, _, _ = _run_process(argv, root, timeout_seconds, env)
        report["processes"].append(record)
        if record.get("returncode") != 0:
            return finish(f"{name} multi-platform build did not complete")
        try:
            image = validate_oci_layout(archive)
        except ValueError as exc:
            report["status"] = "failed"
            return finish(f"{name} OCI output failed validation: {exc}")
        image["name"] = name
        report["images"].append(image)

    report["status"] = "complete"
    report["state"] = "BUILT"
    report["reason"] = "Both Harbor image contexts produced verified native amd64/arm64 OCI layouts."
    return finish(report["reason"], code=0)
