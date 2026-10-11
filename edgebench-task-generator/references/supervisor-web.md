# Supervisor Web Console

The dependency-free console is a browser control plane for the same campaign
ledger used by chat dispatch. It does not require Node, npm, Orca desktop or a
GUI on the server.

## Start

Initialize a campaign and supervisor first, then start the console. The chat
supervisor may execute this command for the owner:

```bash
python3 edgebench-task-generator/scripts/topify_pipeline.py \
  supervisor-web CAMPAIGN --host 0.0.0.0 --port 8787 \
  --advertise-host SERVER_IP
```

`0.0.0.0` accepts connections from other IPs. Use it only on a trusted LAN,
Tailscale/WireGuard network, or behind a TLS reverse proxy. Do not expose the
plain HTTP port directly to the public internet.

The process prints separate owner and supervisor tokens. The owner can queue
instructions to the supervisor and approve QC exceptions; the supervisor can
dispatch, terminate and replace employees. Keep both tokens private. Tokens are
not written into campaign/customer packages.

## Views

- capacity, active workers and alerts;
- employee table with parent, role, task/Seed, state and last report;
- detail panel with QC and rollout gate evidence;
- SSE updates every two seconds;
- focused worker selection instead of dumping every transcript.

## Controls

The UI can queue owner text for the supervisor, dispatch an employee and record
termination of an employee tree. The HTTP API also exposes replacement and QC
resolution actions. All actions append to the existing audit ledger.

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

Authenticated requests use `Authorization: Bearer TOKEN`:

```text
GET  /api/me
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
