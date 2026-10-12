#!/usr/bin/env python3
"""Read-only OpenCode session adapter for the supervisor Web console."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlencode

import topify_common as common


def _binary() -> str | None:
    configured = os.environ.get("EDGEBENCH_OPENCODE_BIN")
    if configured:
        path = Path(configured)
        if path.is_file() and not path.is_symlink():
            return str(path)
        return None
    return shutil.which("opencode") or "/root/.opencode/bin/opencode"


def _get(path: str, timeout: int = 15) -> dict:
    binary = _binary()
    if not binary:
        raise common.PipelineError("OpenCode CLI is unavailable")
    result = subprocess.run(
        [binary, "api", "get", path],
        cwd="/",
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise common.PipelineError("OpenCode API session query failed")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise common.PipelineError("OpenCode API returned invalid session JSON") from exc
    if not isinstance(value, dict):
        raise common.PipelineError("OpenCode API session response is not an object")
    return value


def sessions(directory: str | Path, limit: int = 200) -> dict:
    root = Path(directory).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise common.PipelineError("OpenCode session directory is not a real directory")
    query = urlencode({"directory": str(root), "limit": str(limit), "order": "desc"})
    listing = _get(f"/api/session?{query}")
    active = _get("/api/session/active").get("data", {})
    if not isinstance(active, dict):
        active = {}
    result = []
    for item in listing.get("data", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        session_id = item["id"]
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        session_directory = location.get("directory")
        if session_directory and Path(session_directory).resolve() != root:
            continue
        result.append({
            "session_id": session_id,
            "title": item.get("title") or "Untitled session",
            "agent": item.get("agent"),
            "model": item.get("model"),
            "parent_id": item.get("parentID"),
            "directory": session_directory or str(root),
            "created_at": item.get("time", {}).get("created"),
            "updated_at": item.get("time", {}).get("updated"),
            "status": "running" if session_id in active else "idle",
            "outcome": item.get("outcome"),
        })
    return {
        "status": "ok",
        "directory": str(root),
        "sessions": result,
        "active_count": sum(item["status"] == "running" for item in result),
    }
