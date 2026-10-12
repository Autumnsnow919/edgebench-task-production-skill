#!/usr/bin/env python3
"""Read-only OpenCode session adapter for the supervisor Web console."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import re
from urllib.parse import urlencode

import topify_common as common

SESSION_ID = re.compile(r"ses_[a-zA-Z0-9]{20,80}\Z")


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


def _post(path: str, payload: dict, timeout: int = 30) -> dict:
    binary = _binary()
    if not binary:
        raise common.PipelineError("OpenCode CLI is unavailable")
    result = subprocess.run(
        [binary, "api", "post", path, "--data", json.dumps(payload, ensure_ascii=False)],
        cwd="/", env=os.environ.copy(), stdin=subprocess.DEVNULL,
        capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise common.PipelineError("OpenCode API message send failed")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise common.PipelineError("OpenCode API returned invalid message JSON") from exc
    if not isinstance(value, dict):
        raise common.PipelineError("OpenCode API message response is not an object")
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


def _visible_session(directory: str | Path, session_id: str) -> dict:
    if not isinstance(session_id, str) or SESSION_ID.fullmatch(session_id) is None:
        raise common.PipelineError("invalid OpenCode session ID")
    value = sessions(directory, limit=300)
    item = next((item for item in value["sessions"] if item["session_id"] == session_id), None)
    if item is None:
        raise common.PipelineError("OpenCode session is not visible under the approved scan root")
    return item


def messages(directory: str | Path, session_id: str, limit: int = 100) -> dict:
    _visible_session(directory, session_id)
    if type(limit) is not int or not 1 <= limit <= 300:
        raise common.PipelineError("OpenCode message limit must be between 1 and 300")
    result = []
    partial = []
    # User messages are compact and always readable. Assistant messages may
    # contain very large tool payloads; query them separately so one oversized
    # response does not make the whole inspector unusable.
    for message_type in ("user", "assistant"):
        page_limit = min(limit, 20 if message_type == "user" else 3)
        try:
            raw = _get(f"/api/session/{session_id}/message?limit={page_limit}&order=desc&type={message_type}")
        except common.PipelineError as exc:
            partial.append(f"{message_type}: {exc}")
            continue
        for item in raw.get("data", []):
            if not isinstance(item, dict):
                continue
            parts = []
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
                elif content.get("type") == "tool":
                    parts.append(f"[tool: {content.get('name', 'unknown')}]")
            text = "\n".join(part for part in parts if part).strip()
            if text:
                result.append({
                    "message_id": item.get("id"),
                    "type": item.get("type", message_type),
                    "agent": item.get("agent"),
                    "text": text,
                    "created_at": item.get("time", {}).get("created"),
                })
    result.sort(key=lambda item: item.get("created_at") or 0)
    return {"status": "partial" if partial else "ok", "session_id": session_id,
            "messages": result, "limitations": partial,
            "note": "showing recent assistant messages and tool names"}


def send_message(directory: str | Path, session_id: str, text: str) -> dict:
    _visible_session(directory, session_id)
    if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
        raise common.PipelineError("OpenCode session message must be nonempty and at most 20000 characters")
    result = _post(f"/api/session/{session_id}/prompt", {"text": text.strip(), "resume": True})
    return {"status": "sent", "session_id": session_id, "response": result}
