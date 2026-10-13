#!/usr/bin/env python3
"""OpenCode runtime adapter; native questions remain owned by their agent tools."""

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


def _delete(path: str, timeout: int = 30) -> None:
    binary = _binary()
    if not binary:
        raise common.PipelineError("OpenCode CLI is unavailable")
    result = subprocess.run(
        [binary, "api", "delete", path], cwd="/", env=os.environ.copy(),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise common.PipelineError("OpenCode API delete failed")


def cancel_form(session_id: str, form_id: str, message: str) -> None:
    if not SESSION_ID.fullmatch(session_id) or not FORM_ID.fullmatch(form_id):
        raise common.PipelineError("invalid OpenCode form identity")
    from urllib.parse import quote
    _delete(f"/api/session/{session_id}/form/{form_id}?message={quote(message)}")


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
        # The listing is directory-scoped, but the active endpoint is global.
        # Keep the IDs so callers can reuse one active-session query when they
        # verify many employee bindings.
        "active_session_ids": sorted(active),
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


def supervisor_runtime(directory: str | Path, campaign: str | Path, manifest: dict,
                       active: dict | None = None) -> dict:
    session_id = manifest.get("supervisor_session_id")
    if not session_id:
        return {"status": "unbound", "session_id": None, "can_chat": False,
                "reason": "未绑定真实上司会话；登记台账不代表上司正在运行。"}
    try:
        info = _visible_session(directory, session_id)
        actual = Path(info["directory"]).resolve()
        recorded = manifest.get("supervisor_session_directory")
        if not recorded or actual != Path(recorded).resolve() or not Path(campaign).resolve().is_relative_to(actual):
            return {"status": "binding-mismatch", "session_id": session_id, "can_chat": False,
                    "reason": "上司会话目录与 campaign 的绑定不一致。"}
        if active is None:
            active = _get("/api/session/active").get("data", {})
        status = "running" if session_id in active else (
            info.get("outcome") if info.get("outcome") in {"interrupted", "failed"} else "idle"
        )
        return {**info, "status": status, "can_chat": True, "source": "opencode-api",
                "reason": "消息将提交给此会话；接收消息与执行完成是两件事。"}
    except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "session_id": session_id, "can_chat": False,
                "reason": "无法核验上司会话；不会推断其正在消费审批。"}


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
    if not isinstance(result.get("data"), dict) or not result["data"].get("id"):
        raise common.PipelineError("OpenCode 未返回原生消息接收凭证；不能声称上司/员工已收到。")
    return {"status": "sent", "session_id": session_id, "response": result}


def _forms_from_responses(root: Path, responses: list[dict]) -> dict:
    forms = []
    seen = set()
    session_info = {}
    for response in responses:
        if response.get("_tag"):
            raise common.PipelineError("无法读取 OpenCode 待回答问题。")
        for form in response.get("data", []):
            if not isinstance(form, dict) or form.get("id") in seen:
                continue
            if not isinstance(form.get("id"), str) or FORM_ID.fullmatch(form["id"]) is None:
                raise common.PipelineError("OpenCode 返回了无效的问题编号。")
            session_id = form.get("sessionID")
            if session_id not in session_info:
                session_info[session_id] = _visible_session(root, session_id)
            info = session_info[session_id]
            fields = form.get("fields")
            if not isinstance(fields, list):
                raise common.PipelineError("OpenCode 返回了无效的问题字段。")
            # Only expose the form contract, not arbitrary metadata/tool reasoning.
            metadata = form.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise common.PipelineError("OpenCode 返回了无效的问题来源。")
            tool = metadata.get("tool") or {}
            agent_question = metadata.get("kind") == "question" and isinstance(tool, dict) and bool(tool.get("messageID") and tool.get("id"))
            consumer = None
            if agent_question:
                try:
                    consumer = question_consumer(session_id, form)
                except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
                    pass
            forms.append({"form_id": form["id"], "session_id": info["session_id"],
                          "session_title": info["title"], "title": form.get("title", "待回答问题"),
                          "fields": fields, "metadata": metadata,
                          "interaction": "agent-question" if agent_question else "detached-form",
                          "can_reply": consumer is not None,
                          "consumer": consumer,
                          "status": "pending", "source": "opencode-form"})
            seen.add(form["id"])
    return {"status": "ok", "forms": forms}


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
    return _forms_from_responses(root, responses)


def pending_forms_for_sessions(directory: str | Path, session_ids) -> dict:
    """Read pending forms from a small, explicitly selected session set."""
    root = Path(directory).resolve(strict=True)
    responses = []
    seen_sessions = set()
    for session_id in session_ids:
        if not isinstance(session_id, str) or session_id in seen_sessions:
            continue
        _visible_session(root, session_id)
        responses.append(_get(f"/api/session/{session_id}/form"))
        seen_sessions.add(session_id)
    return _forms_from_responses(root, responses)


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


def question_consumer(session_id: str, form: dict) -> dict:
    """Verify the tool actually waiting for this answer, not merely a Form ID."""
    metadata = form.get("metadata") or {}
    tool = metadata.get("tool", {}) if isinstance(metadata, dict) else {}
    if not isinstance(metadata, dict) or metadata.get("kind") != "question" or not isinstance(tool, dict) or not tool.get("messageID") or not tool.get("id"):
        raise common.PipelineError("此表单没有正在等待回答的 Agent question 工具，不能用它放行生产。请联系上司重新审查。")
    message_id = tool["messageID"]
    if not isinstance(message_id, str) or not re.fullmatch(r"msg_[a-zA-Z0-9]+", message_id):
        raise common.PipelineError("原生提问的工具来源无效。")
    message = _get(f"/api/session/{session_id}/message/{message_id}").get("data", {})
    for part in message.get("content", []):
        if (part.get("type") == "tool" and part.get("id") == tool["id"]
                and part.get("name") == "question" and part.get("state", {}).get("status") == "running"):
            return {"message_id": message_id, "tool_id": tool["id"], "status": "waiting",
                    "agent": message.get("agent"),
                    "questions": part.get("state", {}).get("input", {}).get("questions", [])}
    raise FormSettledError("上司已不在该 question 工具中等待；请刷新，不能把旧表单当作有效批准。")


def reply_form(directory, session_id, form_id, answer):
    _visible_session(directory, session_id)
    if not isinstance(form_id, str) or not FORM_ID.fullmatch(form_id):
        raise common.PipelineError("无效的 OpenCode 问题编号。")
    form = _get(f"/api/session/{session_id}/form/{form_id}").get("data", {})
    if form.get("id") != form_id or form.get("sessionID") != session_id:
        raise common.PipelineError("问题不属于选中的会话。")
    if form.get("state", {}).get("status") != "pending":
        raise FormSettledError("问题已在其他界面处理，请刷新后查看。")
    consumer = question_consumer(session_id, form)
    validate_form_answer(form.get("fields", []), answer)
    _post(f"/api/session/{session_id}/form/{form_id}/reply", {"answer": answer})
    return {"status": "answered", "form_id": form_id, "session_id": session_id,
            "consumer": consumer, "execution_status": "awaiting-agent-action",
            "note": "已交给真实 question 工具继续执行；后续动作以会话工具调用和生产回执为准。"}
