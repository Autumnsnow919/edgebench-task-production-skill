#!/usr/bin/env python3
"""Verify a frozen Debian Packages.xz index during an online Work image build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request


def verify_index(lock: dict, architecture: str, *, opener=None) -> str:
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("unsupported apt index architecture")
    snapshot = lock.get("snapshot")
    distribution = lock.get("distribution")
    indexes = lock.get("package_index_sha256")
    if (not isinstance(snapshot, str) or not re.fullmatch(r"\d{8}T\d{6}Z", snapshot)
            or not isinstance(distribution, str) or not re.fullmatch(r"[a-z0-9-]+", distribution)
            or not isinstance(indexes, dict)
            or not isinstance(indexes.get("linux/" + architecture), str)
            or not re.fullmatch(r"[0-9a-f]{64}", indexes["linux/" + architecture])):
        raise ValueError("apt lock has no pinned architecture index")
    url = (f"https://snapshot.debian.org/archive/debian/{snapshot}/dists/{distribution}"
           f"/main/binary-{architecture}/Packages.xz")
    checksum = hashlib.sha256()
    with (opener or urllib.request.urlopen)(url, timeout=60) as response:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != indexes["linux/" + architecture]:
        raise ValueError("apt index digest mismatch")
    return checksum.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise ValueError("expected exactly one Docker TARGETARCH")
    lock = json.loads(Path("/tmp/apt-lock.json").read_text(encoding="utf-8"))
    print("Verified pinned Debian index SHA-256:", verify_index(lock, argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
