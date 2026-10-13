"""Campaign-scoped owner control of agent communication and dispositions.

The gate stops adapter admission, not arbitrary host processes. Existing work
and evidence are retained. Owner messages and real question answers stay open.
"""

from pathlib import Path
import json
import os
import re

import topify_common as common
import topify_opencode as runtime
from topify_ledger_lock import mutation

ACTION_LABELS = {
    "close_candidate": "批准关闭该候选", "shelve_seed": "批准搁置该 Seed",
    "replace_seed": "批准换 Seed", "fire": "批准淘汰该员工", "standby": "批准该员工待命",
}
OWNER_ONLY_PERMISSIONS = [{"action": "*", "resource": "*", "effect": "deny"}] + [
    {"action": action, "resource": "*", "effect": "allow"} for action in ("read", "glob", "grep", "question")
]


def _subject(employee: dict) -> str:
    return common.canonical_sha256({key: employee.get(key) for key in
                                    ("seed_id", "task_id", "hardening_revision", "requested_revision", "last_dispatch_id")})


def _reseed_ids(text: object) -> set[str]:
    if not isinstance(text, str):
        raise common.PipelineError("换 Seed 批准必须是负责人明确原文。")
    ids = r"(?:producer-)?\d+(?:[和、，,\s]+(?:producer-)?\d+)*"
    match = re.fullmatch(rf"\s*(?:把{ids}继续[，,]\s*)?({ids})换种子[。啊吧了！!]*\s*", text)
    if not match:
        raise common.PipelineError("这不是明确指定员工的换 Seed 批准，不能由上司转述代替。")
    return {f"producer-{int(n):04d}" for n in re.findall(r"\d+", match[1])}


def state(root: str | Path) -> dict:
    root = Path(root)
    path = root / "supervision/control.json"
    value = common.load_json(path) if path.exists() or path.is_symlink() else {
        "schema_version": 1, "owner_paused": False, "pending_requests": [], "native_form_ids": [],
        "reason": "", "generation": 0,
    }
    manifest_path = root / "supervision/manifest.json"
    if not path.exists() and manifest_path.is_file() and common.load_json(manifest_path).get("communication_control_initialized"):
        journal = sorted((root / "supervision/control-history").glob("attempt-*/control.json"))
        if journal:
            value = {k:v for k,v in common.load_json(journal[-1]).items() if k != "event"}
        else:
            value["permissions_recovery_unknown"] = True
        value["owner_paused"] = True
        value["reason"] = "已初始化的负责人闸门文件缺失，默认关闭；必须由负责人恢复，不静默重开。"
    value["worker_channel_open"] = not (
        value.get("owner_paused") or value.get("pending_requests") or value.get("native_form_ids")
    )
    return value


def _save(root: Path, value: dict, event: str) -> dict:
    value = {**value, "generation": value.get("generation", 0) + 1, "updated_at": common.utc_now()}
    value.pop("worker_channel_open", None)
    manifest = _manifest(root)
    if not manifest.get("communication_control_initialized"):
        common.write_latest_json(root / "supervision/manifest.json", {**manifest, "communication_control_initialized": True})
    _, attempt = common.next_attempt(root / "supervision/control-history")
    common.write_new_json(attempt / "control.json", {"event": event, **value})
    common.write_latest_json(root / "supervision/control.json", value)
    return state(root)


def require_worker_channel(root: str | Path) -> None:
    value = state(root)
    if not value["worker_channel_open"]:
        raise common.PipelineError("负责人沟通闸门已关闭：停止上下级自动投递和派发，等待负责人决定；消息/证据保留。")


def _manifest(root: Path) -> dict:
    return common.load_json(root / "supervision/manifest.json")


def _sync_permissions(root: Path) -> dict:
    """Use native session permissions, not a broad location/config reload."""
    value = state(root)
    manifest = _manifest(root)
    sid = manifest.get("supervisor_session_id")
    if not sid:
        return value
    guarded = value.get("permissions_guarded", False)
    if guarded and value.get("permissions_session_id") != sid:
        raise common.PipelineError("绑定会话已变化，保持停止；不能复用另一上司的权限凭据。")
    if not value["worker_channel_open"]:
        if not guarded:
            info = runtime._get(f"/api/session/{sid}").get("data", {})
            if not isinstance(info, dict) or info.get("id") != sid:
                raise common.PipelineError("闸门已关闭，但无法核验实际会话权限，不能宣称只读接管已完成。")
            value["previous_permissions"] = info.get("permissions")
            value["permissions_session_id"] = sid
            value["permissions_guarded"] = True
        # Allow only an exact evidence-import command for each pending TUI
        # question. No wildcard shell or lifecycle operation is admitted.
        rules = list(OWNER_ONLY_PERMISSIONS)
        pipeline = Path(__file__).with_name("topify_pipeline.py")
        for rid in value.get("pending_requests", []):
            rules.append({"action": "shell", "resource": f"python3 {pipeline} owner-request-sync {root} --request-id {rid}", "effect": "allow"})
        rules_hash = common.canonical_sha256(rules)
        if guarded and value.get("owner_only_rules_sha256") == rules_hash:
            return value
        runtime._patch(f"/api/session/{sid}", {"permissions": rules})
        value["owner_only_rules_sha256"] = rules_hash
        return _save(root, value, "native-owner-only-permissions")
    if value["worker_channel_open"] and guarded:
        if value.get("permissions_session_id") != sid:
            raise common.PipelineError("绑定会话已变化，不能把旧权限套用到新上司。")
        # Native session.update treats null as "no update", not "remove".
        # Empty rules restore the original no-session-override semantics while
        # retaining Agent/global rules; never replace them with allow-all.
        previous = value.get("previous_permissions")
        runtime._patch(f"/api/session/{sid}", {"permissions": [] if previous is None else previous})
        value["permissions_guarded"] = False
        return _save(root, value, "native-supervisor-permissions-restored")
    return value


def _park_worker_inbox(root: Path, manifest: dict) -> list[dict]:
    sid = manifest.get("supervisor_session_id")
    if not sid:
        return []
    automatic_ids = set()
    for path in (root / "supervision/session-deliveries").glob("attempt-*/delivery.json"):
        receipt = common.load_json(path)
        if receipt.get("session_id") == sid and receipt.get("sender_type") in {"employee", "watchdog"}:
            automatic_ids.add(receipt.get("inbox_id"))
    # Earlier adapter revisions retained the same admission evidence as events.
    events = root / "supervision/events.jsonl"
    if events.is_file():
        for line in events.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                receipt = json.loads(line)
            except ValueError:
                continue
            if (receipt.get("event") == "supervisor_update_delivered" and receipt.get("session_id") == sid
                    and receipt.get("sender_type") in {"employee", "watchdog"}):
                automatic_ids.add(receipt.get("inbox_id"))
    parked = []
    for item in runtime._get(f"/api/session/{sid}/inbox").get("data", []):
        text = item.get("payload", {}).get("text", "")
        # Only these adapter-origin messages may be parked. Never remove owner
        # input, a question answer, unrelated shell output or another campaign.
        if (item.get("id") in automatic_ids and item.get("type") == "user" and isinstance(text, str)
                and text.startswith(("watchdog update from supervisor-watchdog;", "employee update from "))
                and f"\nCampaign: {root}\n" in text and item.get("delivery") == "steer"):
            message_id = item.get("id", "")
            if re.fullmatch(r"msg_[a-zA-Z0-9]+", message_id):
                runtime._patch(f"/api/session/{sid}/inbox/{message_id}", {"delivery": "queue"})
                _, attempt = common.next_attempt(root / "supervision/held-inbox")
                common.write_new_json(attempt / "original.json", item)
                # Keep the durable ledger/original, but cancel native admission
                # so a later owner prompt cannot drain the old lifecycle queue.
                runtime._delete(f"/api/session/{sid}/inbox/{message_id}")
                parked.append(item)
    return parked


@mutation
def pause(root: str | Path, reason: str, *, owner: bool = True, interrupt: bool = True) -> dict:
    root = Path(root).resolve(strict=True)
    manifest = _manifest(root)
    value = state(root)
    if owner:
        value["owner_paused"] = True
    value["reason"] = reason
    _save(root, value, "owner-paused" if owner else "waiting-owner")
    saved = _sync_permissions(root)
    evidence = {"recorded_at": common.utc_now(), "campaign": str(root), "parked": [], "interrupt": None}
    sid = manifest.get("supervisor_session_id")
    if sid:
        info = runtime.supervisor_runtime(manifest.get("supervisor_session_directory", root), root, manifest)
        if info.get("can_chat"):
            forms = runtime._get(f"/api/session/{sid}/form").get("data", [])
            if interrupt and not forms:
                evidence["interrupt"] = runtime._post(f"/api/session/{sid}/interrupt?resume=false", {})
            evidence["parked"] = _park_worker_inbox(root, manifest)
    _, attempt = common.next_attempt(root / "supervision/control-runtime")
    common.write_new_json(attempt / "pause.json", evidence)
    return {**saved, "runtime_receipt": str(attempt / "pause.json"), "runtime": evidence}


@mutation
def resume(root: str | Path) -> dict:
    root = Path(root).resolve(strict=True)
    value = state(root)
    if value.get("permissions_recovery_unknown"):
        raise common.PipelineError("原始负责人闸门/权限凭据缺失，不能静默恢复；先恢复真实控制回执。")
    value["owner_paused"] = False
    value["reason"] = "负责人允许恢复；未解决的具体决策仍保留闸门。"
    # Deliberately do not replay queued lifecycle messages or wake any session.
    _save(root, value, "owner-resumed")
    return _sync_permissions(root)


@mutation
def sync_forms(root: str | Path, forms: list[dict]) -> dict:
    root = Path(root).resolve(strict=True)
    value = state(root)
    ids = sorted(f.get("form_id", f.get("id")) for f in forms
                 if f.get("can_reply") and f.get("form_id", f.get("id")))
    if ids != value.get("native_form_ids", []):
        value["native_form_ids"] = ids
        value = _save(root, value, "owner-question-gate")
    return _sync_permissions(root)


@mutation
def request(root: str | Path, employee_id: str, action: str, report_file: str | Path,
            reason: str) -> dict:
    root = Path(root).resolve(strict=True)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", employee_id):
        raise common.PipelineError("invalid employee_id")
    if action not in ACTION_LABELS or not reason.strip():
        raise common.PipelineError("关闭/搁置/换 Seed/淘汰/待命提案必须说明具体依据。")
    employee = common.load_json(root / "supervision/employees" / f"{employee_id}.json")
    if employee.get("employee_id") != employee_id:
        raise common.PipelineError("员工身份不一致")
    report = common._regular_file(report_file)
    if not report.is_relative_to(Path(employee["job_root"])):
        raise common.PipelineError("决策证据必须位于该员工的独占目录。")
    for existing in requests(root):
        if (existing["employee_id"] == employee_id and existing["action"] == action
                and existing["report_sha256"] == common.sha256_file(report)):
            return {**existing, "request_path": str(root / "supervision/owner-requests" /
                     existing["request_id"].removeprefix("owner-request-") / "request.json"),
                    "instruction": "此提案已在等待负责人，不要重复创建或发送待命指令。"}
    _, attempt = common.next_attempt(root / "supervision/owner-requests")
    rid = f"owner-request-{attempt.name}"
    value = {"schema_version": 1, "request_id": rid, "employee_id": employee_id, "action": action,
             "reason": reason, "report_path": str(report), "report_sha256": common.sha256_file(report),
             "subject_sha256": _subject(employee),
             "previous_state": employee.get("employment_status"), "previous_phase": employee.get("phase"),
             "recorded_at": common.utc_now(), "status": "pending",
             "option_labels": [ACTION_LABELS[action], "保留候选继续工作", "补充证据再决定"]}
    value["request_sha256"] = common.canonical_sha256(value)
    common.write_new_json(attempt / "request.json", value)
    common.write_latest_json(root / "supervision/employees" / f"{employee_id}.json", {
        **employee, "employment_status": "needs-owner-decision", "phase": "awaiting-owner-disposition",
        "pending_owner_request": rid,
    })
    control = state(root)
    control["pending_requests"] = list(dict.fromkeys([*control.get("pending_requests", []), rid]))
    control["reason"] = f"{employee_id} 请求负责人决定：{action}；未批准不得执行。"
    _save(root, control, "disposition-requested")
    _sync_permissions(root)
    return {**value, "request_path": str(attempt / "request.json"),
            "instruction": "在实际上司 session 调用 question；问题原文包含 request_id 和 request_sha256，使用上述选项并等待负责人。"}


def requests(root: str | Path) -> list[dict]:
    root = Path(root)
    pending = set(state(root).get("pending_requests", []))
    return [v for p in sorted((root / "supervision/owner-requests").glob("attempt-*/request.json"))
            if (v := common.load_json(p)).get("request_id") in pending]


@mutation
def record_reseed_chat(root: str | Path, employee_id: str, report_file: str | Path,
                       session_id: str, message_id: str) -> dict:
    """Honor an explicit numbered reseed instruction without re-asking it."""
    root = Path(root).resolve(strict=True)
    manifest = _manifest(root)
    if session_id not in {manifest.get("supervisor_session_id"), os.environ.get("OPENCODE_SESSION_ID")}:
        raise common.PipelineError("换 Seed 批准须来自实际上司或当前负责人会话的原始 user 消息。")
    if not re.fullmatch(r"msg_[a-zA-Z0-9]+", message_id):
        raise common.PipelineError("invalid owner message ID")
    directory = manifest.get("supervisor_session_directory") if session_id == manifest.get("supervisor_session_id") else Path.cwd()
    info = runtime._visible_session(directory, session_id)
    message = runtime._get(f"/api/session/{session_id}/message/{message_id}").get("data", {})
    text = message.get("text", "")
    if message.get("type") != "user" or message.get("id") != message_id:
        raise common.PipelineError("这不是明确指定员工的换 Seed 批准，不能由上司转述代替。")
    approved = _reseed_ids(text)
    if employee_id not in approved:
        raise common.PipelineError("换 Seed 指令没有批准该员工。")
    employee = common.load_json(root / "supervision/employees" / f"{employee_id}.json")
    report = common._regular_file(report_file)
    if not report.is_relative_to(Path(employee["job_root"])):
        raise common.PipelineError("指定员工的证据不在其工作目录。")
    value = {"source": "opencode-owner-chat", "session_id": session_id, "source_directory": info["directory"],
             "message_id": message_id, "message_sha256": common.canonical_sha256(message), "message_text": text,
             "employee_id": employee_id, "action": "replace_seed", "approved": True,
             "subject_sha256": _subject(employee), "report_path": str(report), "report_sha256": common.sha256_file(report),
             "recorded_at": common.utc_now(), "rollout_authorized": False, "fire_authorized": False}
    _, attempt = common.next_attempt(root / "supervision/owner-dispositions")
    common.write_new_json(attempt / "resolution.json", value)
    return value


@mutation
def resolve_native(root: str | Path, consumer: dict, answer: dict, reply_file: str | Path) -> list[dict]:
    """Consume only the authenticated real native answer, never an agent note."""
    root = Path(root).resolve(strict=True)
    reply = common.load_json(reply_file)
    digest = reply.get("reply_sha256")
    if (reply.get("status") != "answered" or reply.get("session_id") != _manifest(root).get("supervisor_session_id")
            or reply.get("answer") != answer or reply.get("consumer") != consumer
            or not digest or common.canonical_sha256({k:v for k,v in reply.items() if k != "reply_sha256"}) != digest):
        raise common.PipelineError("必须使用绑定上司的真实负责人回答回执。")
    resolved = []
    for req in requests(root):
        for index, question in enumerate(consumer.get("questions", [])):
            text = question.get("question", "")
            choice = answer.get(f"q{index}")
            if (req["request_id"] not in text or req["request_sha256"] not in text
                    or choice not in req["option_labels"]):
                continue
            message = runtime._get(f"/api/session/{reply['session_id']}/message/{consumer['message_id']}").get("data", {})
            actual_tool = next((p for p in message.get("content", []) if p.get("type") == "tool"
                                and p.get("name") == "question" and p.get("id") == consumer.get("tool_id")), {})
            actual_state = actual_tool.get("state", {})
            output = "\n".join(p.get("text", "") for p in actual_state.get("content", []) if isinstance(p, dict))
            if (actual_state.get("status") != "completed" or actual_state.get("input", {}).get("questions") != consumer.get("questions")
                    or "User has answered your questions:" not in output or f'"{text}"="{choice}"' not in output):
                raise common.PipelineError("尚未核验到真实 question 工具的完成回答；回执留存，候选处置仍不执行。")
            if common.sha256_file(req["report_path"]) != req["report_sha256"]:
                raise common.PipelineError("提案证据已变化，旧回答不能关闭新候选。")
            resolution = {"request_id": req["request_id"], "request_sha256": req["request_sha256"],
                          "employee_id": req["employee_id"], "action": req["action"], "choice": choice,
                          "approved": choice == ACTION_LABELS[req["action"]], "report_path": req["report_path"],
                          "report_sha256": req["report_sha256"], "reply_path": str(reply_file),
                          "subject_sha256": req["subject_sha256"],
                          "reply_sha256": common.sha256_file(reply_file), "recorded_at": common.utc_now()}
            _, attempt = common.next_attempt(root / "supervision/owner-dispositions")
            common.write_new_json(attempt / "resolution.json", resolution)
            value = state(root)
            if choice != "补充证据再决定":
                value["pending_requests"] = [rid for rid in value.get("pending_requests", []) if rid != req["request_id"]]
            _save(root, value, "owner-disposition-answered")
            if choice == "保留候选继续工作":
                employee_path = root / "supervision/employees" / f"{req['employee_id']}.json"
                employee = common.load_json(employee_path)
                common.write_latest_json(employee_path, {**employee, "employment_status": "active",
                    "phase": req.get("previous_phase"), "pending_owner_request": None})
            resolved.append(resolution)
            break
    _sync_permissions(root)
    return resolved


@mutation
def sync_request_answer(root: str | Path, request_id: str) -> list[dict]:
    """Import a completed native TUI question without inventing a Web form."""
    root = Path(root).resolve(strict=True)
    req = next((r for r in requests(root) if r["request_id"] == request_id), None)
    if req is None:
        raise common.PipelineError("没有这一待负责人决定的提案。")
    manifest = _manifest(root)
    sid = manifest.get("supervisor_session_id")
    runtime._visible_session(manifest.get("supervisor_session_directory", root), sid)
    messages = runtime._get(f"/api/session/{sid}/message?limit=5&order=desc&type=assistant").get("data", [])
    for message in messages:
        for part in message.get("content", []):
            s = part.get("state", {})
            if part.get("type") != "tool" or part.get("name") != "question" or s.get("status") != "completed":
                continue
            questions = s.get("input", {}).get("questions", [])
            output = "\n".join(p.get("text", "") for p in s.get("content", []) if isinstance(p, dict))
            for index, question in enumerate(questions):
                text = question.get("question", "")
                if req["request_id"] not in text or req["request_sha256"] not in text:
                    continue
                for label in req["option_labels"]:
                    if "User has answered your questions:" not in output or f'"{text}"="{label}"' not in output:
                        continue
                    consumer = {"message_id": message.get("id"), "tool_id": part.get("id"), "questions": questions,
                                "agent": message.get("agent"), "status": "completed"}
                    reply = {"schema_version": 1, "session_id": sid, "status": "answered", "consumer": consumer,
                             "answer": {f"q{index}": label}, "source": "opencode-question-tool-result",
                             "recorded_at": common.utc_now()}
                    reply["reply_sha256"] = common.canonical_sha256(reply)
                    _, attempt = common.next_attempt(root / "supervision/native-interactions" / request_id)
                    path = attempt / "reply.json"
                    common.write_new_json(path, reply)
                    return resolve_native(root, consumer, reply["answer"], path)
    raise common.PipelineError("未找到负责人对当前提案的实际 question 完成回答。")


def require_disposition(root: str | Path, employee_id: str, action: str) -> None:
    root = Path(root)
    employee = common.load_json(root / "supervision/employees" / f"{employee_id}.json")
    subject = _subject(employee)
    if employee.get("pending_owner_request") in state(root).get("pending_requests", []):
        raise common.PipelineError("当前候选提案仍在等负责人，不能复用旧处置批准。")
    for path in sorted((root / "supervision/owner-dispositions").glob("attempt-*/resolution.json"), reverse=True):
        value = common.load_json(path)
        if value.get("employee_id") == employee_id and value.get("action") == action:
            if value.get("source") == "opencode-owner-chat":
                original = runtime._get(f"/api/session/{value['session_id']}/message/{value['message_id']}").get("data", {})
                proof_matches = (original.get("type") == "user" and common.canonical_sha256(original) == value.get("message_sha256")
                                 and employee_id in _reseed_ids(original.get("text")) and action == "replace_seed")
            else:
                proof_matches = (employee.get("pending_owner_request") == value.get("request_id")
                                 and common.sha256_file(value["reply_path"]) == value["reply_sha256"])
            if (value.get("approved") and value.get("subject_sha256") == subject
                    and common.sha256_file(value["report_path"]) == value["report_sha256"]
                    and proof_matches):
                return
            break
    raise common.PipelineError("没有负责人对该对象的明确处置批准；请先说明证据、暂停上下级通道并提交真实 question。")
