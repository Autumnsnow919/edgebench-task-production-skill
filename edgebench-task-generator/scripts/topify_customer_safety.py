#!/usr/bin/env python3
"""Fail closed on private provider, host, credential and customer spill in shipped bytes."""

from __future__ import annotations

import re
import io
import tarfile
import base64
import json
from pathlib import Path

import topify_common as common
import topify_sforge_delivery as delivery


# These are customer-data hygiene boundaries, not a proof that arbitrary source
# code has no security defects. Never print matched bytes (they may be secrets).
PRIVATE = (
    re.compile(rb"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b"),
    re.compile(rb"(?i)(?:/root/(?:terminal_bench|edgebench)|/tmp/opencode|/home/(?:runner|opencode)|"
               rb"/root/\.local/share/edgebench-sforge|10\.2\.39\.3(?::[0-9]+)?|"
               rb"(?:www\.)?dmxapi\.cn|open\.bigmodel\.cn|host\.docker\.internal(?::[0-9]+)?)"),
    re.compile(rb"(?i)(?:LITELLM_API_KEY|DMX_API_KEY|BIGMODEL_API_KEY|"
               rb"SFORGE_AGENT_API_KEY|OPENAI_API_KEY|credential[_-]?variable|"
               rb"api[_-]?base[_-]?url)"),
    re.compile(rb"(?i)(?:\b(?:another|other)[_-]?customer\b|"
               rb"internal[_ -]?(?:provider|proxy|session|reviewer|workflow)|"
               rb"pre[_-]?rollout[_-]?contract[_-]?review|author[_-]?responses)"),
)

BLOCKED_PARTS = {
    ".env", ".git", ".ssh", "__pycache__", "run_config.json",
    "customer-copy.json", "quality-review.json", "non-format-audit.json",
    "source-identity.json", "owner-scope.json", "AGENTS.md", "CLAUDE.md",
}


def check(name: str, value: Path | bytes, *, allow_chinese: bool = False) -> None:
    parts = name.split("/")
    if (any(part in BLOCKED_PARTS or part.endswith((".pem", ".key", ".pyc", ".log"))
            for part in parts)
            or name.startswith("/") or ".." in parts or "\\" in name):
        raise common.PipelineError("customer member name is outside the delivery allowlist")
    if not delivery._owner_secret_check(value):
        raise common.PipelineError("customer content includes credential-like bytes")
    if isinstance(value, Path):
        try:
            data = value.read_bytes()
        except OSError as exc:
            raise common.PipelineError("customer source file is unavailable") from exc
    else:
        data = value
    if any(pattern.search(data) for pattern in PRIVATE):
        raise common.PipelineError("customer content includes internal or personal-data identifiers")
    if not allow_chinese and (name.lower().endswith(".md") or name.lower().endswith("instruction.txt")):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise common.PipelineError("English customer document must be UTF-8") from exc
        if any("\u4e00" <= char <= "\u9fff" for char in text):
            raise common.PipelineError("English customer document contains untranslated Chinese text")
    if name.endswith(".json") and b'"setup_cmds"' in data:
        try:
            definition = json.loads(data)
            commands = [command for role in ("work", "judge")
                        for command in definition.get(role, {}).get("setup_cmds", [])]
            if not all(isinstance(command, str) for command in commands):
                raise ValueError("invalid setup command")
            for command in commands:
                for encoded in re.findall(r"[A-Za-z0-9+/]{128,}={0,2}", command):
                    decoded = base64.b64decode(encoded, validate=True)
                    if (len(encoded) >= 2048 or decoded.startswith((b"\x1f\x8b", b"PK\x03\x04"))
                            or any(pattern.search(decoded) for pattern in PRIVATE)):
                        raise common.PipelineError("customer task contains opaque or internal encoded build payload")
        except (ValueError, TypeError, KeyError, base64.binascii.Error) as exc:
            raise common.PipelineError("customer task has uninspectable build commands") from exc
    if name.endswith(".tar.gz"):
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive:
                    if (not member.isfile() or member.size > 64 * 1024 * 1024
                            or member.name.startswith("/") or ".." in member.name.split("/")):
                        raise common.PipelineError("customer snapshot contains unsafe archived members")
                    if (any(part in BLOCKED_PARTS for part in member.name.split("/"))
                            or any(pattern.search(member.name.encode("utf-8")) for pattern in PRIVATE)):
                        raise common.PipelineError("customer snapshot contains private archived member names")
                    content = archive.extractfile(member)
                    if content is None:
                        raise common.PipelineError("customer snapshot contains unreadable file")
                    payload = content.read()
                    if any(pattern.search(payload) for pattern in PRIVATE):
                        raise common.PipelineError("customer snapshot contains internal identifiers")
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise common.PipelineError("customer snapshot is unreadable") from exc
