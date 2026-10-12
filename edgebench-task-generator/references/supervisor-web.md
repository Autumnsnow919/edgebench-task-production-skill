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

## Controls

The UI can queue owner text for the supervisor, dispatch an employee and record
termination of an employee tree. The HTTP API also exposes replacement and QC
resolution actions. All actions append to the existing audit ledger.

The dispatch button creates a signed dispatch receipt; it does not pretend that
an OpenCode Task or Orca worker has started. The supervisor transport must
start the actual worker and send a heartbeat before the card becomes `running`.

The console never accepts arbitrary shell commands or PIDs. A termination is a
ledger decision; the transport supervisor stops only exact processes/dispatches
owned by the employee. Frozen task edits and raw score edits are not exposed.

## Owner → supervisor commands

Text submitted in the control box is stored under
`CAMPAIGN/supervision/commands/attempt-*/command.json` as a pending owner
instruction. The OpenCode/Orca supervisor reads pending commands, executes the
corresponding approved workflow and records a report. This keeps a Web action
from silently becoming an untracked shell command.

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
```

File arguments to Web actions must resolve inside the campaign. The static
asset route serves only the shipped HTML/CSS/JS files, not arbitrary evidence,
credentials or host paths.
