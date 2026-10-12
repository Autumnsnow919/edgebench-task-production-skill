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
FORM_ID = re.compile(r"frm_[a-zA-Z0-9]+\Z")


class FormSettledError(common.PipelineError):
    """The native question was answered/cancelled elsewhere."""


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
    # Native form reply returns HTTP 204: successful admission has no JSON body.
    if not result.stdout.strip():
        return {}
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise common.PipelineError("OpenCode API returned invalid message JSON") from exc
    if not isinstance(value, dict):
        raise common.PipelineError("OpenCode API message response is not an object")
    if value.get("_tag") == "FormAlreadySettledError":
        raise FormSettledError("问题已在其他界面处理，请刷新后查看。")
    if value.get("_tag") in {"FormInvalidAnswerError", "InvalidRequestError"}:
        raise common.PipelineError("回答不符合 OpenCode 问题规则，请核对选项和输入。")
    if value.get("_tag"):
        raise common.PipelineError("OpenCode 未接受操作，请检查会话和问题状态。")
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
    root = Path(directory).resolve(strict=True)
    item = _get(f"/api/session/{session_id}").get("data", {})
    location = item.get("location", {}) if isinstance(item, dict) else {}
    session_directory = location.get("directory")
    if (item.get("id") != session_id or not isinstance(session_directory, str)
            or not Path(session_directory).resolve().is_relative_to(root)):
        raise common.PipelineError("OpenCode session is not visible under the approved scan root")
    return {
        "session_id": session_id, "title": item.get("title") or "Untitled session",
        "agent": item.get("agent"), "parent_id": item.get("parentID"),
        "directory": session_directory, "model": item.get("model"),
        "created_at": item.get("time", {}).get("created"),
        "updated_at": item.get("time", {}).get("updated"), "outcome": item.get("outcome"),
    }


def employee_runtime(employee: dict, active: dict | None = None) -> dict:
    """Verify a ledger binding; never associate employees by title or proximity."""
    session_id = employee.get("session_id")
    if not session_id:
        return {"status": "unbound", "session_id": None,
                "reason": "Only a work order exists. The supervisor must start and bind the actual employee session.",
                "can_chat": False}
    if employee.get("transport") != "opencode-task":
        return {"status": "unsupported", "session_id": session_id,
                "reason": "This transport has no verified runtime adapter.", "can_chat": False}
    try:
        job_root = Path(employee["job_root"]).resolve(strict=True)
        info = _visible_session(job_root, session_id)
        if Path(info["directory"]).resolve() != job_root:
            return {"status": "binding-mismatch", "session_id": session_id,
                    "reason": "Actual session directory differs from the exclusive employee job_root.",
                    "can_chat": False}
        if active is None:
            active = _get("/api/session/active").get("data", {})
        return {**info, "status": "running" if session_id in active else "idle",
                "can_chat": employee.get("status", employee.get("employment_status")) != "fired",
                "source": "opencode-api"}
    except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "session_id": session_id,
                "reason": "The bound session could not be verified; no runtime activity is assumed.",
                "can_chat": False}


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
                    state = content.get("state", {}) if isinstance(content.get("state"), dict) else {}
                    tool_input = state.get("input", {})
                    if isinstance(tool_input, dict):
                        input_text = " ".join(
                            f"{key}={value}" for key, value in tool_input.items()
                            if key in {"command", "path", "pattern", "url", "workdir"}
                        )
                    else:
                        input_text = ""
                    parts.append(f"[工具调用: {content.get('name', 'unknown')}{(' · ' + input_text) if input_text else ''}]")
                    output = []
                    for part in state.get("content", []) if isinstance(state.get("content"), list) else []:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            output.append(part["text"][:800])
                    if output:
                        parts.append("[工具输出]\n" + "\n".join(output))
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


def pending_forms(directory, extra_directories=(), session_id=None):
    """Read native OpenCode questions; never invent choices from a ledger status."""
    root = Path(directory).resolve(strict=True)
    if session_id is not None:
        _visible_session(root, session_id)
        responses = [_get(f"/api/session/{session_id}/form")]
    else:
        directories = {root}
        for value in extra_directories:
            path = Path(value).resolve(strict=True)
            if not path.is_relative_to(root):
                raise common.PipelineError("问题目录超出批准的扫描范围。")
            directories.add(path)
        responses = [_get("/api/form?" + urlencode({"location[directory]": str(path)}))
                     for path in sorted(directories)]
    forms = []
    seen = set()
    for response in responses:
        if response.get("_tag"):
            raise common.PipelineError("无法读取 OpenCode 待回答问题。")
        for form in response.get("data", []):
            if not isinstance(form, dict) or form.get("id") in seen:
                continue
            if not isinstance(form.get("id"), str) or FORM_ID.fullmatch(form["id"]) is None:
                raise common.PipelineError("OpenCode 返回了无效的问题编号。")
            info = _visible_session(root, form.get("sessionID"))
            fields = form.get("fields")
            if not isinstance(fields, list):
                raise common.PipelineError("OpenCode 返回了无效的问题字段。")
            # Only expose the form contract, not arbitrary metadata/tool reasoning.
            forms.append({"form_id": form["id"], "session_id": info["session_id"],
                          "session_title": info["title"], "title": form.get("title", "待回答问题"),
                          "fields": fields, "status": "pending", "source": "opencode-form"})
            seen.add(form["id"])
    return {"status": "ok", "forms": forms}


def validate_form_answer(fields, answer):
    if not isinstance(answer, dict) or len(answer) > 100:
        raise common.PipelineError("回答必须是问题字段对应的对象。")
    known = {field["key"] for field in fields if isinstance(field, dict) and "key" in field}
    if set(answer) - known:
        raise common.PipelineError("回答包含不属于该问题的字段。")
    for field in fields:
        key = field["key"]
        enabled = not field.get("hidden", False)
        for condition in field.get("when", []):
            actual = answer.get(condition["key"])
            enabled = enabled and (actual == condition["value"] if condition["op"] == "eq"
                                   else actual != condition["value"])
        if not enabled:
            continue
        value = answer.get(key)
        if value is None:
            if field.get("required"):
                raise common.PipelineError(f"请回答：{field.get('title', key)}")
            continue
        kind = field["type"]
        if kind == "string":
            if not isinstance(value, str):
                raise common.PipelineError("文本回答格式错误。")
            if len(value) < field.get("minLength", 0) or len(value) > field.get("maxLength", 20_000):
                raise common.PipelineError("文本回答长度不符合要求。")
            if field.get("options") and not field.get("custom"):
                if value not in {o["value"] for o in field["options"]}:
                    raise common.PipelineError("该问题不允许自定义回答，请选择现有选项。")
        elif kind == "multiselect":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise common.PipelineError("多选回答格式错误。")
            if len(set(value)) != len(value) or not field.get("minItems", 0) <= len(value) <= field.get("maxItems", 100):
                raise common.PipelineError("多选数量不符合要求。")
            if not field.get("custom") and set(value) - {o["value"] for o in field["options"]}:
                raise common.PipelineError("多选包含不允许的自定义内容。")
        elif kind == "boolean":
            if type(value) is not bool:
                raise common.PipelineError("请选择是或否。")
        elif kind in {"number", "integer"}:
            if not common.finite_number(value) or (kind == "integer" and type(value) is not int):
                raise common.PipelineError("数值回答格式错误。")
            for bound, op in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b)):
                if common.finite_number(field.get(bound)) and op(value, field[bound]):
                    raise common.PipelineError("数值不在允许范围内。")
        else:
            raise common.PipelineError("该字段需要在原 OpenCode 界面处理。")
    common.canonical_sha256(answer)


def reply_form(directory, session_id, form_id, answer):
    _visible_session(directory, session_id)
    if not isinstance(form_id, str) or FORM_ID.fullmatch(form_id) is None:
        raise common.PipelineError("无效的 OpenCode 问题编号。")
    form = _get(f"/api/session/{session_id}/form/{form_id}").get("data", {})
    if form.get("id") != form_id or form.get("sessionID") != session_id:
        raise common.PipelineError("问题不属于选中的会话。")
    if form.get("state", {}).get("status") != "pending":
        raise FormSettledError("问题已在其他界面处理，请刷新后查看。")
    validate_form_answer(form.get("fields", []), answer)
    _post(f"/api/session/{session_id}/form/{form_id}/reply", {"answer": answer})
    return {"status": "answered", "form_id": form_id, "session_id": session_id}
