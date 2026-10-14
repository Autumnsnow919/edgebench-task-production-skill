# Supervisor Web Console

The dependency-free console is a browser control plane for the same campaign
ledger used by chat dispatch. It does not require Node, npm, Orca desktop or a
GUI on the server.

One Web process can host multiple isolated supervisors/Benchmarks. Each entry
has its own campaign, employee tree and supervisor token; the owner token can
switch between them from the Benchmark selector. The shared dedup root is
validated at startup so two supervisors cannot silently use different dedup
indexes.

## Start

Initialize each campaign and supervisor first. Each `supervisor-init` creates
`supervision/supervisor-stamp.json`. The production console can discover all
stamped campaigns below a scan root; no hand-maintained registry is required:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-web \
  --scan-root /var/lib/edgebench/campaigns \
  --host 0.0.0.0 --port 8787 \
  --no-auth
```

For older campaigns that predate the watermark protocol, add
`--adopt-legacy --dedup-root /var/lib/edgebench/dedup-global`. The scanner
recognizes only valid Topify campaign structures, creates a stamp, and marks it
`legacy-adopted / needs_owner_review`; it does not silently treat arbitrary
task directories as production supervisors.

The service binds to every network interface, so LAN clients use the server's
reachable IP and port 8787 directly. No reverse proxy is required for the
trusted-LAN deployment in this work order; direct port 8787 is plain HTTP. The
token files
the service keeps their values stable across restarts.

`--no-auth` is an explicit trusted-network mode. It grants owner authority to
every client that can reach the port. Do not use it on an untrusted public
network. If the service is later exposed outside the LAN, add a TLS proxy or
restore token authentication by omitting `--no-auth` and supplying
`--owner-token-file` / per-supervisor token files.

If token files are omitted in single-campaign mode, the process generates
separate owner and supervisor tokens and prints them once. Multi-supervisor
registry entries require fixed `0600` supervisor token files. For a resident
service, use fixed `0600` token files so a restart does not invalidate browser
sessions. The owner can switch Benchmark tabs, queue instructions to any
supervisor and approve QC exceptions; a supervisor token can only see and
control its own Benchmark. Keep all tokens private. Tokens are not written into
campaign/customer packages.

The service is foreground-safe and should be kept resident by systemd,
supervisord or another process manager with automatic restart.

For systemd, copy and edit
`references/edgebench-supervisor-web.service`, create the two owner-only token
files, then enable it:

```bash
install -d -m 0750 /etc/edgebench
install -m 0600 /dev/null /etc/edgebench/owner.token
chmod 600 /etc/edgebench/owner.token
# Put long random values in the two files, then:
sudo systemctl daemon-reload
sudo systemctl enable --now edgebench-supervisor-web.service
sudo systemctl status edgebench-supervisor-web.service
```

Open `http://SERVER_IP:8787/` from any permitted server/client IP. Allow TCP
8787 in the host firewall only for the intended network; binding to all
interfaces does not bypass firewall or network ACLs.

## Views

- capacity, active workers and alerts;
- separate tabs/cards for every registered supervisor and Benchmark;
- employee table with parent, role, task/Seed, state and last report;
- detail panel with QC and rollout gate evidence;
- SSE updates every two seconds;
- focused worker selection instead of dumping every transcript.
- employee heartbeat health and watchdog alerts delivered to the actual
  Supervisor; stalled workers are not automatically terminated.

## Controls

The owner has independent “暂停自动沟通并接管上司 / 恢复上下级通道” controls.
Paused campaigns keep reports/evidence but do not deliver worker/watchdog input
or allow directives/dispatch. Owner chat and real native answers remain open.
The board shows the gate reason and pending candidate disposition proposals.
Resume does not start workers, approve a candidate or replay old alerts.
Owner questions automatically hold the channel, and a completed question
answer is still distinct from an executed candidate disposition. Supervisor
tokens cannot operate the owner pause/resume endpoint.
Owner control also installs a native read-only permissions boundary on the
same session; the session/model/history remain unchanged. Original rules are
restored exactly on resume. No global OpenCode/location restart is required.

The console shows the verified bound supervisor ID, agent, runtime status and
real transcript. Owner messages and employee review requests use native prompt
admission into that actual session; sending can resume an interrupted session,
viewing does not. Routine stage alerts say “等待上司处理”, not “请负责人批准”.
The supervisor owns dispatch, repair and termination. The legacy Web generic
`owner-decision` path is disabled; an unrelated child completion cannot be
approved as a rollout.

Repaired QC-2 non-pass findings require owner review, not automatic firing.
The board/inspector shows the report hash, closed repairs, remaining findings,
severity and original pass/fail/incomplete states. Unclassified prose is marked
unclassified instead of inferred as failed. The supervisor asks the real native
question with the current QC subject hash. Its verified owner reply is retained
as scoped evidence; `qc-resolve` requires that evidence for exception, further
repair or termination. Supervisor tokens cannot make these owner decisions.

Only a form backed by a running native `question` tool is answerable. On reply
the server verifies its source assistant message/tool ID. The answer resumes
that waiting tool, not a generic JSON-state mapper. The UI keeps selections
across polling and separates duplicate radio groups in board/inspector.
“已交给 Agent” is not “动作已执行”; inspect the real response/tool/receipt.
Old detached API Form receipts are retained as history and never as approvals.

The compatibility `owner-form-create` action requests supervisor review; it
does not manufacture new forms. Do not automatically re-create a disappeared
form or resume production just because a GET detected an alert. Use the native
prompt API for Web-interactive headless supervisors: non-interactive
`opencode run` can auto-cancel question tools.

The dispatch button creates a signed dispatch receipt; it does not pretend that
an OpenCode Task or Orca worker has started. The supervisor transport must
start the actual worker and send a heartbeat before the card becomes `running`.
The Web also exposes the runner capability separately from the communication
gate. For an existing bound OpenCode employee, “start/resume” uses the native
prompt API and waits for a heartbeat; it is not an MCP resource-list operation.
If no Task/Orca/process runner is available, the UI reports `runner-unavailable`
and leaves the ledger unchanged.

The console never accepts arbitrary shell commands or PIDs. A termination is a
ledger decision; the transport supervisor stops only exact processes/dispatches
owned by the employee. Frozen task edits and raw score edits are not exposed.

## Owner → supervisor commands

Text submitted in “联系上司” is stored under `supervision/messages/` and
submitted into the verified native session prompt inbox. The response includes
its native inbox ID and a durable `supervision/session-deliveries/` receipt.
Replies and tools are read from `/api/supervisor-session`, not fabricated from
message JSONs. The legacy `/api/command` ledger queue remains passive and does
not prove consumption. A review request does not approve any production gate.

## API

With token mode, requests use `Authorization: Bearer TOKEN`. In `--no-auth`
mode, the same endpoints are available without a token:

```text
GET  /api/me
GET  /api/supervisors
GET  /api/dashboard[?employee_id=...]
GET  /api/events
GET  /api/stream?token=TOKEN
POST /api/dispatch
POST /api/fire
POST /api/replacement
POST /api/qc-resolve
POST /api/command
GET  /api/supervisor-session
GET  /api/opencode-forms[?employee_id=...|session_id=...]
POST /api/message
POST /api/supervisor-review
POST /api/opencode-form-reply
```

File arguments to Web actions must resolve inside the campaign. The static
asset route serves only the shipped HTML/CSS/JS files, not arbitrary evidence,
credentials or host paths.

## Regression checks

The Python fixture suite verifies missing bindings, interrupted supervisors,
question consumer identity, stale/detached forms and native prompt admission:

```bash
python3 edgebench-task-generator/scripts/test_scripts.py
```

The optional browser fixture uses a separately installed Playwright. It never
starts a model or production job:

```bash
EDGEBENCH_PLAYWRIGHT_MODULE=/PATH/TO/playwright \
PLAYWRIGHT_BROWSERS_PATH=/PATH/TO/test-browsers \
node edgebench-task-generator/tests/browser_native_supervisor.cjs
```

If the default Playwright download CDN is unavailable, its isolated browser
installation may use a trusted mirror, for example
`PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright`.
Do not restart the OpenCode service or delete unrelated browser caches for a
console test. Fixture success is not a formal production/rollout receipt.
