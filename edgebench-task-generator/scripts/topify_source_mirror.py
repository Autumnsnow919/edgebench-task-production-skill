#!/usr/bin/env python3
"""Capture immutable source bytes as lead-only campaign evidence.

Use ``capture`` in the authoring stage, then place its ``mirror_path`` and
``sha256`` in a separately reviewed, normalized Seed Card. Captures convey no
license, redistribution, deduplication, or task-approval decision.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import tempfile
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import topify_common as common
import topify_seed


REGISTRY = Path(__file__).resolve().parents[1] / "references/source-registry.json"
CHUNK = 64 * 1024
TIMEOUT = 10
MAX_REDIRECTS = 5
COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")
DATASET_VERSION = re.compile(
    r"dataset:[A-Za-z0-9][A-Za-z0-9._/-]*@[vV]?\d+(?:[._-]\d+)*\Z"
)


def _url(value: str, allowed_hosts: set[str] | None = None) -> tuple[str, str]:
    """Validate a credential-free public HTTPS URL without opening a connection."""
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise common.PipelineError("source URL must be a public credential-free HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise common.PipelineError("source URL must be a public credential-free HTTPS URL") from exc
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or "?" in value or "#" in value or port not in (None, 443)
            or not parsed.path.startswith("/")):
        raise common.PipelineError("source URL must be a public credential-free HTTPS URL")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise common.PipelineError("source URL host is not a registered artifact host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject unusual DNS names before any lookup. Redirects may use a CDN host.
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or host.endswith(".") or ".." in host:
            raise common.PipelineError("source URL host is invalid")
    else:
        if not address.is_global:
            raise common.PipelineError("source URL host is not public")
    return host, parsed.path


def _public_address(host: str) -> str:
    """Resolve once, reject any private answer, then pin the connection to a checked IP."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses
        ):
            raise common.PipelineError("download host resolves to a non-public address")
        return addresses[0][4][0]
    except (OSError, ValueError) as exc:
        raise common.PipelineError("download host cannot be verified as public") from exc


def _download(url: str, output, max_bytes: int, allowed_hosts: set[str]) -> None:
    """Stream with manual, validated redirects; no ambient proxy or URL credentials."""
    context = ssl.create_default_context()
    for redirect in range(MAX_REDIRECTS + 1):
        host, path = _url(url, allowed_hosts)
        address = _public_address(host)
        connection = http.client.HTTPSConnection(host, port=443, timeout=TIMEOUT, context=context)
        # HTTPSConnection still verifies the certificate against host, not address.
        connection._create_connection = lambda _destination, timeout=TIMEOUT, source_address=None: (
            socket.create_connection((address, 443), timeout=timeout, source_address=source_address)
        )
        try:
            connection.request("GET", path, headers={"Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location or redirect == MAX_REDIRECTS:
                    raise common.PipelineError("download redirect limit or missing location")
                next_url = urljoin(url, location)
                _url(next_url, allowed_hosts)
                url = next_url
                continue
            if response.status != 200:
                raise common.PipelineError("download returned a non-success status")
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_bytes:
                        raise common.PipelineError("source exceeds max_bytes")
                except ValueError as exc:
                    raise common.PipelineError("invalid download Content-Length") from exc
            total = 0
            while True:
                chunk = response.read(min(CHUNK, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise common.PipelineError("source exceeds max_bytes")
                output.write(chunk)
            return
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise common.PipelineError("source download failed") from exc
        finally:
            connection.close()


def _local_copy(source: Path, output, max_bytes: int) -> None:
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise common.PipelineError("local_file must be a regular non-symlink file")
            if details.st_size > max_bytes:
                raise common.PipelineError("source exceeds max_bytes")
            total = 0
            while True:
                chunk = handle.read(min(CHUNK, max_bytes - total + 1))
                if not chunk:
                    return
                total += len(chunk)
                if total > max_bytes:
                    raise common.PipelineError("source exceeds max_bytes")
                output.write(chunk)
    except OSError as exc:
        raise common.PipelineError("local_file must be a readable regular non-symlink file") from exc


def _verified_blob(path: Path, digest: str, size: int) -> None:
    """Never trust an existing path merely because its name is a digest."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_size != size:
                raise common.PipelineError("existing mirror collision or tamper")
            hasher = hashlib.sha256()
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                hasher.update(chunk)
            if hasher.hexdigest() != digest:
                raise common.PipelineError("existing mirror collision or tamper")
    except OSError as exc:
        raise common.PipelineError("existing mirror collision or tamper") from exc


def _directory(path: Path) -> None:
    if path.is_symlink() or path.exists() and not path.is_dir():
        raise common.PipelineError("mirror directory must be a real directory")
    path.mkdir(exist_ok=True)


def capture(
    campaign: Path,
    source_id: str,
    source_url: str,
    revision: str,
    expected_sha256: str | None = None,
    local_file: Path | None = None,
    max_bytes: int = 50_000_000,
) -> tuple[dict, int]:
    """Capture a source artifact; return (lead-only report, 0) or (rejection, 2).

    Replaying the same bytes reuses the verified blob and appends a new capture
    record. No Seed Card or rights assertion is created here.
    """
    temporary: str | None = None
    try:
        root, _ = topify_seed.load_campaign(campaign)
        registry = topify_seed.validate_source_registry(common.load_json(REGISTRY))
        source = next((entry for entry in registry["sources"] if entry["id"] == source_id), None)
        if source is None:
            raise common.PipelineError("source_id is not registered")
        allowed_hosts = set(source["artifact_hosts"])
        _url(source_url, allowed_hosts)
        if not isinstance(revision, str) or not (COMMIT.fullmatch(revision) or DATASET_VERSION.fullmatch(revision)):
            raise common.PipelineError("revision must be a full Git commit or versioned dataset identifier")
        if COMMIT.fullmatch(revision) and revision.lower() not in urlsplit(source_url).path.lower():
            raise common.PipelineError("Git artifact URL must include its pinned full revision")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str) or not common.HEX64.fullmatch(expected_sha256)
        ):
            raise common.PipelineError("expected_sha256 must be 64 lowercase hex")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise common.PipelineError("max_bytes must be a positive integer")
        mirror_dir = root / "mirrors"
        _directory(mirror_dir)
        captures_dir = mirror_dir / "captures"
        _directory(captures_dir)
        descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=mirror_dir)
        with os.fdopen(descriptor, "wb") as output:
            if local_file is not None:
                _local_copy(Path(local_file), output, max_bytes)
            else:
                _download(source_url, output, max_bytes, allowed_hosts)
        hasher = hashlib.sha256()
        size = 0
        with open(temporary, "rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                hasher.update(chunk)
                size += len(chunk)
        digest = hasher.hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise common.PipelineError("source SHA-256 does not match expected_sha256")
        mirror = mirror_dir / f"{digest}.blob"
        try:
            os.link(temporary, mirror, follow_symlinks=False)
        except FileExistsError:
            _verified_blob(mirror, digest, size)
        record_path = captures_dir / f"{uuid4().hex}.json"
        record = {
            "schema_version": 1,
            "source_id": source_id,
            "source_url": source_url,
            "revision": revision,
            "sha256": digest,
            "byte_count": size,
            "mirror_path": str(mirror),
            "source_registry_entry_sha256": common.canonical_sha256(source),
            "captured_at": common.utc_now(),
            "status": "lead-only",
        }
        common.write_new_json(record_path, record)
        return {**record, "capture_path": str(record_path)}, 0
    except (common.PipelineError, OSError, ValueError, TypeError) as exc:
        # No untrusted URL or exception string is echoed to reports or logs.
        reason = str(exc) if isinstance(exc, common.PipelineError) else "source capture I/O failed"
        return {"status": "rejected", "reason": reason}, 2
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
