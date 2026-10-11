# SForge rollout operations (measured failure modes, 2026-09-26/27)

Operational lessons from real `sforge run` launches. Read this before any
rollout; every item below cost a real failed launch.
The container-side Codex proxy notes later in this file apply only if that
integration is selected. A host-side strict controller keeps model/API
credentials on the host and gives Work/Judge no network; do not add a Work
gateway because an older rollout needed one.

## Pre-launch quota and model probe (do this first)

A run that loses provider access mid-budget is truncated evidence and does not
count toward the three full-budget trials. Before launching hours-long runs:

1. Confirm the credential scopes to the intended endpoint (`GET /v1/models`)
   and that the exact model ID is served; endpoint model catalogs differ
   (`gpt-6-sol-cdx` existed on DMX but not on the replacement relay).
2. Make one tiny paid call with the exact model to prove balance > 0.
3. Record which endpoint+model each run used; a mid-campaign provider switch
   makes runs incomparable. Truncated runs stay diagnostic-only and are
   excluded from the score table with the reason recorded.
4. Freeze the **delivered** Work/Judge inputs and collect a main-agent
     Q1–Q7/contract receipt before run `started_at`. `rollout-check` must be
    ready; a late review cannot be dated backward into a qualifying run.
    See `pre-rollout-quality-gates.md` for scope and early kill-tests.

`/models` success and one tiny chat request prove present reachability, not an
hours-long provider window. During the run check both the actual PID and the
latest checkpoint; a stale `running` checkpoint and a monitor PID alone do
not prove progress. A `completion_unknown` request must not be blindly retried
if upstream completion and billing are unknown. Before `--resume`, inspect
the **original owned Work container** and verify it exists with matching
owner/image/policy; `work_usable=true` in a checkpoint is insufficient. If
the container is gone, keep the partial run as diagnostic and use a new
full-budget run ID only if the owner authorizes it. Score-null evaluation
events are neither numeric zeros nor a successful deadline-final grade.

## Judge serve and run must share one log dir

`serve` keeps the judge admin secret at `<log-dir>/.judge-admin-secret`.
Starting `run` with a different `--log-dir` makes register fail with repeated
403 on `/api/v1/register`. Use the same log dir for the task's serve and all
its runs. Never restart a serve that active runs depend on; give each task its
own port.

## Codex agent environment injection

- The container credential comes from `SFORGE_AGENT_API_KEY`, not
  `OPENAI_API_KEY`; the run fails immediately with "Missing environment
  variable: OPENAI_API_KEY" otherwise.
- With `--disable-internet`, a direct proxy config is rejected: start
  `sforge proxy --target <API base URL> --port <port>` and export
  `SFORGE_AGENT_API_BASE_URL=http://host.docker.internal:<port>`, and unset
  HTTP(S)_PROXY in the launching shell.

## Work image tooling

SForge's Codex installer pipes the Node.js tarball through `xz` and calls
`sudo`; a bare slim base fails the install. The work image needs `xz-utils`,
`sudo`, `git`, `curl`, `jq`. The current owner permits network access during
image construction. Pin added build dependencies and record the actual Work
image identity/rebuild receipts before model runs; an altered scored image
cannot inherit results from an earlier identity. Work/Judge execution remains
offline.

## Docker daemon under host load

docker-py's default 60s read timeout aborts container creation when the host
is loaded (observed at load average >20). Retry wastes minutes per attempt.
Launch through a tiny wrapper that raises only the default:

```python
import docker
_orig = docker.from_env
docker.from_env = lambda **kw: _orig(timeout=300, **kw)
```

then call `sforge.cli.main`. Patch only your own process; never edit the
shared venv. Clean up `Created`-state leftover containers before retrying.

## Watching runs

Read per-submission scores from
`<log-dir>/runs/<run-id>/<task>/submissions/*/report.json`. A frontier model
reaching near-reference within the first hour is saturation: stop the run,
record the trajectory, and treat the identity as `revise`; a flat or
slowly-climbing curve over hours is the intended difficulty signal. Auto-eval
submissions repeating the best score only mirror the latest agent snapshot.
Reconcile every `run_history.json` entry against a scored report/archive or
an explicitly diagnosed no-report infrastructure log. Preserve genuine
multi-case positive partial scores with `valid=false`; neither those reports
nor Docker creation timeouts are blanket model zeroes. The run collector
fails closed on unexplained gaps and on a Q1–Q7 review signed after the run
started. See `evidence-truth-and-delivery.md` before computing the mean.
