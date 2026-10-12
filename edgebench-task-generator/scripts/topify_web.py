#!/usr/bin/env python3
"""Small authenticated web console for the supervisor ledger.

The console is intentionally dependency-free. It reads the same immutable
receipts as the CLI and calls supervisor functions directly for approved
actions; it never accepts arbitrary shell commands or arbitrary PIDs.
"""

from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import stat
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

import topify_common as common
import topify_opencode
import topify_registry
import topify_seed
import topify_supervisor


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
MAX_BODY = 1_000_000


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


def _read_token_file(value: str | Path | None, label: str) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_symlink() or not path.is_file():
        raise common.PipelineError(f"{label} must be an existing regular file")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise common.PipelineError(f"{label} must be owner-only (mode 0600 or stricter)")
    if path.stat().st_size > 4096:
        raise common.PipelineError(f"{label} is too large")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise common.PipelineError(f"{label} is empty")
    return token


class SupervisorContext:
    def __init__(self, supervisor_id: str, benchmark: str, campaign: str | Path,
                 session_directory: str | Path | None = None):
        self.supervisor_id = supervisor_id
        self.benchmark = benchmark
        self.root, self.campaign = topify_seed.load_campaign(campaign)
        if not (self.root / "supervision/manifest.json").is_file():
            raise common.PipelineError(f"run supervisor-init before adding {supervisor_id}")
        self._opencode_at = 0.0
        self._opencode_snapshot = {"status": "not-queried", "sessions": [], "active_count": 0}
        self._employee_runtime_cache = {}
        self._runtime_lock = threading.Lock()
        self._forms_at = 0.0
        self._forms_snapshot = {"status": "ok", "forms": []}
        self._forms_cache = {}
        self.session_directory = Path(session_directory).resolve() if session_directory else self.root

    def describe(self) -> dict:
        campaign = common.load_json(self.root / "campaign.json")
        lock_path = self.root / "requirements-profile-lock.json"
        lock = common.load_json(lock_path) if lock_path.is_file() and not lock_path.is_symlink() else None
        config = campaign.get("config", {})
        return {
            "campaign_id": config.get("campaign_id"),
            "campaign_state": campaign.get("state"),
            "target_task_count": config.get("target_task_count"),
            "profile_id": lock.get("profile", {}).get("profile_id") if lock else None,
            "delivery_format": lock.get("delivery", {}).get("format") if lock else None,
            "lock_state": lock.get("state") if lock else "not-locked",
        }

    def dashboard(self, employee_id: str | None = None, runtime_snapshot: dict | None = None) -> dict:
        value, _ = topify_supervisor.dashboard(self.root, employee_id)
        now = time.monotonic()
        with self._runtime_lock:
            if runtime_snapshot is not None:
                self._opencode_snapshot = runtime_snapshot
                self._opencode_at = now
            active = None
            if isinstance(self._opencode_snapshot, dict) and self._opencode_snapshot.get("status") == "ok":
                active_ids = self._opencode_snapshot.get("active_session_ids")
                if isinstance(active_ids, list):
                    active = {session_id: {"type": "running"} for session_id in active_ids
                              if isinstance(session_id, str)}
            for employee in value["employees"]:
                key = (employee["employee_id"], employee.get("session_id"), employee["job_root"], employee["status"])
                cached = self._employee_runtime_cache.get(key)
                if cached is None or now - cached[0] >= 5:
                    runtime = topify_opencode.employee_runtime(employee, active)
                    self._employee_runtime_cache[key] = (now, runtime)
                employee["runtime"] = self._employee_runtime_cache[key][1]
        value["workforce"] = {
            "registered": len(value["employees"]),
            "runtime_running": sum(e["runtime"]["status"] == "running" for e in value["employees"]),
            "unbound": sum(e["runtime"]["status"] == "unbound" for e in value["employees"]),
            "production_started": any(e["role"] == "producer" and e["runtime"]["status"] == "running" for e in value["employees"]),
        }
        if runtime_snapshot is not None:
            self._opencode_snapshot = runtime_snapshot
            self._opencode_at = now
        return {
            "supervisor_id": self.supervisor_id,
            "benchmark": self.benchmark,
            "workspace": self.describe(),
            "opencode_sessions": self._opencode_snapshot,
            **value,
        }

    def questions(self, session_id=None, employee_id=None, force=False):
        if session_id:
            return topify_opencode.pending_forms(self.session_directory, session_id=session_id)
        now = time.monotonic()
        cache_key = ("employee", employee_id) if employee_id else ("global",)
        with self._runtime_lock:
            cached = self._forms_cache.get(cache_key)
            if not force and cached is not None and now - cached[0] < 5:
                return cached[1]
            try:
                if employee_id:
                    employee = topify_supervisor._employee(self.root, employee_id)
                    manifest = common.load_json(self.root / "supervision/manifest.json")
                    session_ids = [manifest.get("supervisor_session_id"), employee.get("session_id")]
                    snapshot = topify_opencode.pending_forms_for_sessions(
                        self.session_directory, session_ids
                    )
                else:
                    # Owner approvals are native forms in the bound
                    # supervisor session directory. Child sessions are queried
                    # lazily when their employee is opened; scanning every
                    # worktree on each page refresh made the console slow.
                    snapshot = topify_opencode.pending_forms(self.session_directory)
            except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
                snapshot = {"status": "unavailable", "forms": [],
                             "reason": "无法读取 OpenCode 待回答问题，请稍后刷新。"}
            self._forms_cache[cache_key] = (now, snapshot)
            self._forms_snapshot = snapshot
            self._forms_at = now
            return snapshot


class ConsoleState:
    def __init__(
        self,
        campaign: str | Path | None,
        owner_token: str | None,
        supervisor_token: str | None,
        owner_token_file: str | Path | None = None,
        supervisor_token_file: str | Path | None = None,
        registry_file: str | Path | None = None,
        scan_roots: list[str | Path] | None = None,
        adopt_legacy: bool = False,
        dedup_root: str | Path | None = None,
        legacy_benchmark: str = "Unclassified",
        auth_enabled: bool = True,
    ):
        self.tokens = {}
        self.contexts = {}
        self.auth_enabled = auth_enabled
        self.scan_roots = list(scan_roots or [])
        self.adopt_legacy = adopt_legacy
        self.discovery_dedup_root = dedup_root
        self.legacy_benchmark = legacy_benchmark
        self._discovery_at = 0.0
        # Discovery walks the owner-approved scan root recursively. It is
        # configuration refresh, not request-time runtime monitoring; doing it
        # every few seconds serializes clicks, SSE and dashboard reads behind a
        # filesystem scan. Existing contexts remain available immediately.
        self._discovery_interval = 30.0
        self._discovery_lock = threading.Lock()
        self.discovery_error = None
        self._runtime_at = 0.0
        self._runtime_interval = 5.0
        self._runtime_lock = threading.Lock()
        self._runtime_snapshot = {"status": "not-queried", "sessions": [], "active_count": 0}
        if registry_file is not None or scan_roots:
            registry = (topify_registry.load(registry_file) if registry_file is not None else
                        topify_registry.discover(scan_roots or [], adopt_legacy, dedup_root, legacy_benchmark))
            if self.auth_enabled:
                owner_token = owner_token or _read_token_file(registry.get("owner_token_file"), "registry owner_token_file")
            for item in registry["supervisors"]:
                token = (_read_token_file(item.get("supervisor_token_file"), f"{item['supervisor_id']} supervisor token")
                         if self.auth_enabled else None)
                context = SupervisorContext(
                    item["supervisor_id"], item["benchmark"], item["campaign"],
                    self.scan_roots[0] if self.scan_roots else None,
                )
                self.contexts[context.supervisor_id] = context
                if token is not None:
                    self.tokens[_token_hash(token)] = ("supervisor", context.supervisor_id)
            self.registry_file = str(registry["registry_file"])
            self.dedup_root = registry["dedup_root"]
        else:
            if campaign is None:
                raise common.PipelineError("campaign or registry_file is required")
            if self.auth_enabled:
                owner_token = owner_token or _read_token_file(owner_token_file, "owner_token_file")
                supervisor_token = supervisor_token or _read_token_file(supervisor_token_file, "supervisor_token_file")
            context = SupervisorContext("default", "Benchmark", campaign)
            self.contexts["default"] = context
            self.supervisor_token = (supervisor_token or _token()) if self.auth_enabled else None
            if self.auth_enabled:
                self.tokens[_token_hash(self.supervisor_token)] = ("supervisor", "default")
            self.registry_file = None
            self.dedup_root = None
        self.owner_token = (owner_token or _token()) if self.auth_enabled else None
        if self.auth_enabled:
            self.tokens[_token_hash(self.owner_token)] = ("owner", None)

    def auth(self, value: str | None) -> tuple[str, str | None] | None:
        if not self.auth_enabled:
            return ("owner", None)
        if not value:
            return None
        return self.tokens.get(_token_hash(value))

    def runtime_snapshot(self) -> dict:
        now = time.monotonic()
        if now - self._runtime_at < self._runtime_interval:
            return self._runtime_snapshot
        with self._runtime_lock:
            now = time.monotonic()
            if now - self._runtime_at < self._runtime_interval:
                return self._runtime_snapshot
            if not self.contexts:
                self._runtime_snapshot = {
                    "status": "unavailable", "reason": "没有已登记的 supervisor",
                    "sessions": [], "active_count": 0,
                }
                self._runtime_at = now
                return self._runtime_snapshot
            directory = self.scan_roots[0] if self.scan_roots else next(iter(self.contexts.values())).session_directory
            try:
                self._runtime_snapshot = topify_opencode.sessions(directory)
            except (common.PipelineError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
                self._runtime_snapshot = {
                    "status": "unavailable", "reason": str(exc),
                    "sessions": [], "active_count": 0, "active_session_ids": [],
                }
            self._runtime_at = now
            return self._runtime_snapshot

    def refresh_discovery(self) -> None:
        if not self.scan_roots:
            return
        now = time.monotonic()
        if now - self._discovery_at < self._discovery_interval:
            return
        with self._discovery_lock:
            now = time.monotonic()
            if now - self._discovery_at < self._discovery_interval:
                return
            try:
                registry = topify_registry.discover(
                    self.scan_roots, self.adopt_legacy,
                    self.discovery_dedup_root, self.legacy_benchmark,
                )
                refreshed = {}
                for item in registry["supervisors"]:
                    existing = self.contexts.get(item["supervisor_id"])
                    if existing is not None and existing.root == Path(item["campaign"]).resolve():
                        refreshed[item["supervisor_id"]] = existing
                    else:
                        refreshed[item["supervisor_id"]] = SupervisorContext(
                            item["supervisor_id"], item["benchmark"], item["campaign"],
                            self.scan_roots[0] if self.scan_roots else None,
                        )
                self.contexts = refreshed
                self.dedup_root = registry.get("dedup_root")
                self.discovery_error = None
                self._discovery_at = now
            except (common.PipelineError, OSError, ValueError, TypeError) as exc:
                self.discovery_error = str(exc)
                self._discovery_at = now
                if not self.contexts:
                    raise

    def context(self, supervisor_id: str | None, auth: tuple[str, str | None], required: bool = True) -> SupervisorContext | None:
        self.refresh_discovery()
        role, token_scope = auth
        scope = supervisor_id or token_scope
        if role == "supervisor" and scope != token_scope:
            raise PermissionError("supervisor token is scoped to another supervisor")
        if scope is None:
            if required:
                raise common.PipelineError("supervisor_id is required for this action")
            return None
        value = self.contexts.get(scope)
        if value is None:
            raise common.PipelineError(f"unknown supervisor_id: {scope}")
        return value

    def supervisors(self) -> list[dict]:
        self.refresh_discovery()
        return [
            {
                "supervisor_id": context.supervisor_id,
                "benchmark": context.benchmark,
                "campaign": str(context.root),
                "workspace": context.describe(),
            }
            for context in self.contexts.values()
        ]

    def dashboard(self, supervisor_id: str | None = None, employee_id: str | None = None) -> dict:
        self.refresh_discovery()
        if supervisor_id is not None:
            return self.contexts[supervisor_id].dashboard(employee_id, self.runtime_snapshot())
        rows = [context.dashboard(runtime_snapshot=self.runtime_snapshot()) for context in self.contexts.values()]
        return {
            "schema_version": 1,
            "aggregate": True,
            "supervisors": rows,
            "alerts": [alert for row in rows for alert in row.get("alerts", [])],
            "employees": [employee for row in rows for employee in row.get("employees", [])],
        }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


class ConsoleHandler(BaseHTTPRequestHandler):
    server: "ConsoleServer"

    def log_message(self, format: str, *args: object) -> None:
        # Keep worker output and tokens out of the console's normal stdout.
        return

    @property
    def state(self) -> ConsoleState:
        return self.server.state

    def _query_token(self) -> str | None:
        values = parse_qs(urlparse(self.path).query).get("token", [])
        return values[0] if values else None

    def _auth(self) -> tuple[str, str | None] | None:
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
        return self.state.auth(token or self._query_token())

    def _require(self, action: str | None = None, supervisor_id: str | None = None) -> tuple[str, str | None]:
        auth = self._auth()
        if auth is None:
            self._respond(401, {"error": "authentication required"})
            raise PermissionError
        role, token_scope = auth
        if action == "owner" and role != "owner":
            self._respond(403, {"error": "owner approval required"})
            raise PermissionError
        if supervisor_id is not None and role == "supervisor" and token_scope != supervisor_id:
            self._respond(403, {"error": "supervisor token is scoped to another supervisor"})
            raise PermissionError
        return auth

    def _context(self, auth: tuple[str, str | None], supervisor_id: str | None, required: bool = True):
        return self.state.context(supervisor_id, auth, required=required)

    def _respond(self, status: int, value: object) -> None:
        data = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY:
            raise ValueError("request body is missing or too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _inside_campaign(self, value: str | None, label: str, context: SupervisorContext | None = None) -> Path | None:
        if value is None:
            return None
        path = Path(value).resolve(strict=True)
        if context is None or not path.is_file() or not path.is_relative_to(context.root):
            raise ValueError(f"{label} must be a file inside the campaign")
        return path

    def _query_supervisor_id(self) -> str | None:
        return parse_qs(urlparse(self.path).query).get("supervisor_id", [None])[0]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/me":
                auth = self._require()
                supervisors = self.state.supervisors()
                if auth[0] == "supervisor":
                    supervisors = [item for item in supervisors if item["supervisor_id"] == auth[1]]
                self._respond(200, {"role": auth[0], "supervisor_id": auth[1], "supervisors": supervisors})
                return
            if parsed.path == "/api/supervisors":
                auth = self._require()
                supervisors = self.state.supervisors()
                if auth[0] == "supervisor":
                    supervisors = [item for item in supervisors if item["supervisor_id"] == auth[1]]
                self._respond(200, {"supervisors": supervisors})
                return
            if parsed.path == "/api/dashboard":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                employee_id = parse_qs(parsed.query).get("employee_id", [None])[0]
                if supervisor_id is None and auth[0] == "supervisor":
                    supervisor_id = auth[1]
                self._respond(200, self.state.dashboard(supervisor_id, employee_id))
                return
            if parsed.path == "/api/events":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                context = self._context(auth, supervisor_id)
                self._respond(200, {"supervisor_id": context.supervisor_id, "events": self._events(context)})
                return
            if parsed.path == "/api/messages":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                context = self._context(auth, supervisor_id)
                query = parse_qs(parsed.query)
                messages, _ = topify_supervisor.list_messages(
                    context.root, query.get("target_type", ["supervisor"])[0],
                    query.get("target_id", ["supervisor"])[0],
                    int(query.get("limit", [100])[0]),
                )
                self._respond(200, {"supervisor_id": context.supervisor_id, "messages": messages})
                return
            if parsed.path == "/api/opencode-session":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                context = self._context(auth, supervisor_id)
                session_id = parse_qs(parsed.query).get("session_id", [""])[0]
                result = topify_opencode.messages(context.session_directory, session_id)
                self._respond(200, result)
                return
            if parsed.path == "/api/opencode-forms":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                context = self._context(auth, supervisor_id)
                session_id = parse_qs(parsed.query).get("session_id", [None])[0]
                employee_id = parse_qs(parsed.query).get("employee_id", [None])[0]
                result = context.questions(session_id, employee_id)
                self._respond(200, result)
                return
            if parsed.path == "/api/employee-session":
                supervisor_id = self._query_supervisor_id()
                auth = self._require(supervisor_id=supervisor_id)
                context = self._context(auth, supervisor_id)
                employee_id = parse_qs(parsed.query).get("employee_id", [""])[0]
                employee = topify_supervisor._employee(context.root, employee_id)
                runtime = topify_opencode.employee_runtime(employee)
                if runtime["status"] not in {"running", "idle"}:
                    self._respond(409, {"error": runtime.get("reason"), "runtime": runtime})
                    return
                result = topify_opencode.messages(employee["job_root"], employee["session_id"])
                self._respond(200, {**result, "employee_id": employee_id, "runtime": runtime})
                return
            if parsed.path == "/api/stream":
                self._stream()
                return
            self._static(parsed.path)
        except PermissionError:
            return
        except (common.PipelineError, OSError, ValueError, TypeError) as exc:
            self._respond(400, {"error": str(exc)})

    def _events(self, context: SupervisorContext) -> list[dict]:
        path = context.root / "supervision/events.jsonl"
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _stream(self) -> None:
        supervisor_id = self._query_supervisor_id()
        try:
            auth = self._require(supervisor_id=supervisor_id)
            if supervisor_id is None and auth[0] == "supervisor":
                supervisor_id = auth[1]
        except PermissionError:
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        previous = None
        deadline = time.monotonic() + 300
        try:
            while time.monotonic() < deadline:
                dashboard = self.state.dashboard(supervisor_id)
                events = self._events(self._context(self._auth(), supervisor_id))
                stable_dashboard = {key: value for key, value in dashboard.items() if key != "generated_at"}
                fingerprint = common.canonical_sha256({
                    "dashboard": stable_dashboard,
                    "last_event": events[-1] if events else None,
                })
                if fingerprint != previous:
                    payload = _json_bytes(dashboard).decode("utf-8").rstrip("\n")
                    self.wfile.write(f"event: dashboard\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    previous = fingerprint
                else:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                time.sleep(2)
        except (common.PipelineError, ValueError, TypeError) as exc:
            try:
                payload = _json_bytes({"status": "discovery-error", "error": str(exc)}).decode("utf-8").rstrip("\n")
                self.wfile.write(f"event: error\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if "/" in relative or relative not in {"index.html", "app.js", "decisions.js", "style.css"}:
            self._respond(404, {"error": "not found"})
            return
        source = WEB_ROOT / relative
        if not source.is_file() or source.is_symlink():
            self._respond(404, {"error": "not found"})
            return
        content = source.read_bytes()
        content_type = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "text/javascript; charset=utf-8",
            "decisions.js": "text/javascript; charset=utf-8",
            "style.css": "text/css; charset=utf-8",
        }[relative]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/dispatch":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result, code = topify_supervisor.dispatch(context.root, body["employee_id"])
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/fire":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                evidence = self._inside_campaign(body.get("evidence"), "evidence", context)
                result, code = topify_supervisor.fire(
                    context.root, body["employee_id"], body["reason"], evidence
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/replacement":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                prompt = self._inside_campaign(body.get("prompt_file"), "prompt_file", context)
                fingerprint = self._inside_campaign(body.get("fingerprint_file"), "fingerprint_file", context)
                result, code = topify_supervisor.replace_seed(
                    context.root, body["former_employee_id"], body["new_employee_id"],
                    body["new_seed_id"], body["transport"], prompt, fingerprint
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/qc-resolve":
                body = self._body()
                auth = self._require("owner" if body.get("decision") == "accept_exception" else None,
                                     body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                evidence = self._inside_campaign(body.get("evidence"), "evidence", context)
                result, code = topify_supervisor.qc_resolve(
                    context.root, body["producer_id"], body["decision"],
                    body["note"], evidence,
                )
                self._respond(200 if code == 0 else 409, {"role": auth[0], **result})
                return
            if parsed.path == "/api/owner-decision":
                body = self._body()
                auth = self._require("owner", body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                evidence = self._inside_campaign(body.get("evidence"), "evidence", context)
                result, code = topify_supervisor.owner_decision(
                    context.root, body["employee_id"], body["decision"],
                    body["note"], evidence,
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/owner-form-create":
                body = self._body()
                auth = self._require("owner", body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result, code = topify_supervisor.create_owner_form(
                    context.root, body["employee_id"], body.get("note", "")
                )
                self._respond(201 if code == 0 else 409, result)
                return
            if parsed.path == "/api/owner-form-sync":
                body = self._body()
                auth = self._require("owner", body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result, code = topify_supervisor.sync_owner_form_by_form(
                    context.root, body["form_id"]
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/command":
                self._require("owner")
                body = self._body()
                context = self._context(("owner", None), body.get("supervisor_id"))
                result = self._write_command(context, body)
                self._respond(201, result)
                return
            if parsed.path == "/api/message":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result, code = topify_supervisor.send_message(
                    context.root, body["target_type"], body["target_id"], body["text"],
                    sender_type=auth[0], sender_id=auth[1] or "owner",
                    reply_to=body.get("reply_to"),
                )
                self._respond(201 if code == 0 else 409, result)
                return
            if parsed.path == "/api/opencode-message":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result = topify_opencode.send_message(
                    context.session_directory, body["session_id"], body["text"]
                )
                self._respond(201, result)
                return
            if parsed.path == "/api/opencode-form-reply":
                body = self._body()
                auth = self._require("owner", body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                result = topify_opencode.reply_form(
                    context.session_directory, body["session_id"],
                    body["form_id"], body["answer"],
                )
                # A native reply is only the interaction receipt. If this is
                # an owner-decision form, immediately consume that receipt in
                # the production ledger so the Workforce state cannot remain
                # falsely pending after the owner has answered.
                if topify_supervisor._owner_form_employee_id(context.root, body["form_id"]):
                    synced, sync_code = topify_supervisor.sync_owner_form_by_form(
                        context.root, body["form_id"]
                    )
                    result = {**result, "ledger_sync": synced, "ledger_sync_code": sync_code}
                context.questions(force=True)
                self._respond(200, result)
                return
            if parsed.path == "/api/employee-session-message":
                body = self._body()
                auth = self._require(supervisor_id=body.get("supervisor_id"))
                context = self._context(auth, body.get("supervisor_id"))
                employee = topify_supervisor._employee(context.root, body["employee_id"])
                runtime = topify_opencode.employee_runtime(employee)
                if not runtime["can_chat"]:
                    self._respond(409, {"error": runtime.get("reason", "Employee is not available for chat"), "runtime": runtime})
                    return
                result = topify_opencode.send_message(employee["job_root"], employee["session_id"], body["text"])
                topify_supervisor._append(context.root, "employee_session_message_sent",
                                         employee_id=employee["employee_id"], session_id=employee["session_id"])
                self._respond(201, result)
                return
            self._respond(404, {"error": "not found"})
        except PermissionError:
            return
        except topify_opencode.FormSettledError as exc:
            self._respond(409, {"error": str(exc)})
        except (KeyError, common.PipelineError, OSError, ValueError, TypeError) as exc:
            self._respond(400, {"error": str(exc)})

    def _write_command(self, context: SupervisorContext, body: dict) -> dict:
        text = body.get("text") or body.get("command")
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
            raise ValueError("command text must be nonempty and at most 10000 characters")
        directory = context.root / "supervision/commands"
        directory.mkdir(exist_ok=True)
        number, attempt = common.next_attempt(directory)
        command = {
            "schema_version": 1,
            "target": "supervisor",
            "sender": "owner-web",
            "text": text.strip(),
            "status": "pending",
            "created_at": common.utc_now(),
        }
        command["command_sha256"] = common.canonical_sha256(command)
        common.write_new_json(attempt / "command.json", command)
        topify_supervisor._append(context.root, "owner_command_queued", command_sha256=command["command_sha256"], supervisor_id=context.supervisor_id)
        return {"status": "queued", "supervisor_id": context.supervisor_id, "path": str(attempt / "command.json"), "command_sha256": command["command_sha256"]}


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: ConsoleState):
        self.state = state
        super().__init__(address, ConsoleHandler)


def serve(
    campaign: str | Path | None,
    host: str = "0.0.0.0",
    port: int = 8787,
    owner_token: str | None = None,
    supervisor_token: str | None = None,
    owner_token_file: str | Path | None = None,
    supervisor_token_file: str | Path | None = None,
    registry_file: str | Path | None = None,
    scan_roots: list[str | Path] | None = None,
    adopt_legacy: bool = False,
    dedup_root: str | Path | None = None,
    legacy_benchmark: str = "Unclassified",
    no_auth: bool = False,
) -> tuple[dict, int]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise common.PipelineError("web port must be between 1 and 65535")
    if not no_auth:
        owner_token = owner_token or _read_token_file(owner_token_file, "owner_token_file")
        supervisor_token = supervisor_token or _read_token_file(supervisor_token_file, "supervisor_token_file")
    state = ConsoleState(campaign, owner_token, supervisor_token,
                         owner_token_file, supervisor_token_file, registry_file, scan_roots,
                         adopt_legacy, dedup_root, legacy_benchmark, auth_enabled=not no_auth)
    server = ConsoleServer((host, port), state)
    report = {
        "status": "running",
        "bind": f"{host}:{port}",
        "listen_url": f"http://{host}:{port}",
        "campaigns": [str(context.root) for context in state.contexts.values()],
        "supervisors": state.supervisors(),
        "scan_roots": [str(item) for item in (scan_roots or [])],
        "warning": "Use any reachable server IP with this port; keep tokens private and protect the network path.",
        "authentication": "disabled" if no_auth else "token",
    }
    if not no_auth and registry_file is None and not scan_roots:
        report["owner_token"] = state.owner_token
        report["supervisor_token"] = state.supervisor_token
    if no_auth:
        report["warning"] = "NO AUTH: every client that can reach this port can monitor and control supervisors. Use only on a trusted private network."
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return {**report, "status": "stopped"}, 0
    finally:
        server.server_close()
    return report, 0
