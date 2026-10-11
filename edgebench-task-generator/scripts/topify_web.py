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
import time
from urllib.parse import parse_qs, urlparse

import topify_common as common
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


class ConsoleState:
    def __init__(self, campaign: str | Path, owner_token: str | None, supervisor_token: str | None):
        self.root, self.campaign = topify_seed.load_campaign(campaign)
        if not (self.root / "supervision/manifest.json").is_file():
            raise common.PipelineError("run supervisor-init before starting the web console")
        self.tokens = {}
        self.owner_token = owner_token or _token()
        self.supervisor_token = supervisor_token or _token()
        self.tokens[_token_hash(self.owner_token)] = "owner"
        self.tokens[_token_hash(self.supervisor_token)] = "supervisor"

    def role(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.tokens.get(_token_hash(value))

    def dashboard(self, employee_id: str | None = None) -> dict:
        value, _ = topify_supervisor.dashboard(self.root, employee_id)
        return value


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

    def _role(self) -> str | None:
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
        return self.state.role(token or self._query_token())

    def _require(self, action: str | None = None) -> str:
        role = self._role()
        if role is None:
            self._respond(401, {"error": "authentication required"})
            raise PermissionError
        if action == "owner" and role != "owner":
            self._respond(403, {"error": "owner approval required"})
            raise PermissionError
        return role

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

    def _inside_campaign(self, value: str | None, label: str) -> Path | None:
        if value is None:
            return None
        path = Path(value).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(self.state.root):
            raise ValueError(f"{label} must be a file inside the campaign")
        return path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/me":
                role = self._require()
                self._respond(200, {"role": role, "campaign": str(self.state.root)})
                return
            if parsed.path == "/api/dashboard":
                self._require()
                employee_id = parse_qs(parsed.query).get("employee_id", [None])[0]
                self._respond(200, self.state.dashboard(employee_id))
                return
            if parsed.path == "/api/events":
                self._require()
                self._respond(200, {"events": self._events()})
                return
            if parsed.path == "/api/stream":
                self._stream()
                return
            self._static(parsed.path)
        except PermissionError:
            return
        except (common.PipelineError, OSError, ValueError, TypeError) as exc:
            self._respond(400, {"error": str(exc)})

    def _events(self) -> list[dict]:
        path = self.state.root / "supervision/events.jsonl"
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
        try:
            self._require()
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
                dashboard = self.state.dashboard()
                fingerprint = common.canonical_sha256(dashboard)
                if fingerprint != previous:
                    payload = _json_bytes(dashboard).decode("utf-8").rstrip("\n")
                    self.wfile.write(f"event: dashboard\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    previous = fingerprint
                else:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.removeprefix("/")
        if "/" in relative or relative not in {"index.html", "app.js", "style.css"}:
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
                self._require()
                body = self._body()
                result, code = topify_supervisor.dispatch(self.state.root, body["employee_id"])
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/fire":
                self._require()
                body = self._body()
                evidence = self._inside_campaign(body.get("evidence"), "evidence")
                result, code = topify_supervisor.fire(
                    self.state.root, body["employee_id"], body["reason"], evidence
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/replacement":
                self._require()
                body = self._body()
                prompt = self._inside_campaign(body.get("prompt_file"), "prompt_file")
                result, code = topify_supervisor.replace_seed(
                    self.state.root, body["former_employee_id"], body["new_employee_id"],
                    body["new_seed_id"], body["transport"], prompt
                )
                self._respond(200 if code == 0 else 409, result)
                return
            if parsed.path == "/api/qc-resolve":
                body = self._body()
                role = self._require("owner" if body.get("decision") == "accept_exception" else None)
                evidence = self._inside_campaign(body.get("evidence"), "evidence")
                result, code = topify_supervisor.qc_resolve(
                    self.state.root, body["producer_id"], body["decision"],
                    body["note"], evidence,
                )
                self._respond(200 if code == 0 else 409, {"role": role, **result})
                return
            if parsed.path == "/api/command":
                self._require("owner")
                body = self._body()
                result = self._write_command(body)
                self._respond(201, result)
                return
            self._respond(404, {"error": "not found"})
        except PermissionError:
            return
        except (KeyError, common.PipelineError, OSError, ValueError, TypeError) as exc:
            self._respond(400, {"error": str(exc)})

    def _write_command(self, body: dict) -> dict:
        text = body.get("text") or body.get("command")
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
            raise ValueError("command text must be nonempty and at most 10000 characters")
        directory = self.state.root / "supervision/commands"
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
        topify_supervisor._append(self.state.root, "owner_command_queued", command_sha256=command["command_sha256"])
        return {"status": "queued", "path": str(attempt / "command.json"), "command_sha256": command["command_sha256"]}


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: ConsoleState):
        self.state = state
        super().__init__(address, ConsoleHandler)


def serve(
    campaign: str | Path,
    host: str = "0.0.0.0",
    port: int = 8787,
    owner_token: str | None = None,
    supervisor_token: str | None = None,
    owner_token_file: str | Path | None = None,
    supervisor_token_file: str | Path | None = None,
) -> tuple[dict, int]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise common.PipelineError("web port must be between 1 and 65535")
    owner_token = owner_token or _read_token_file(owner_token_file, "owner_token_file")
    supervisor_token = supervisor_token or _read_token_file(supervisor_token_file, "supervisor_token_file")
    state = ConsoleState(campaign, owner_token, supervisor_token)
    server = ConsoleServer((host, port), state)
    report = {
        "status": "running",
        "bind": f"{host}:{port}",
        "listen_url": f"http://{host}:{port}",
        "owner_token": state.owner_token,
        "supervisor_token": state.supervisor_token,
        "campaign": str(state.root),
        "warning": "Use any reachable server IP with this port; keep tokens private and protect the network path.",
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return {**report, "status": "stopped"}, 0
    finally:
        server.server_close()
    return report, 0
