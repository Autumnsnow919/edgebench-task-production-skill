"""Producer-first recruitment. Preparation never claims to launch an Agent."""

from pathlib import Path

import topify_common as common
from topify_ledger_lock import mutation
import topify_profile
import topify_supervisor as supervisor


def plan(workspace, count=None, transport="opencode-task"):
    root, campaign = supervisor._root(workspace)
    manifest = supervisor._load_manifest(root, campaign)
    path = root / "requirements-profile-lock.json"
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("lock the Benchmark requirements before recruiting producers")
    contract = common.load_json(path)
    if contract.get("state") != "REQUIREMENTS_LOCKED":
        raise common.PipelineError("requirements contract is not locked")
    profile_ref = contract.get("profile", {})
    if not isinstance(profile_ref, dict) or not profile_ref.get("path"):
        raise common.PipelineError("locked contract needs a validated profile path")
    profile = topify_profile.load_profile(profile_ref.get("profile_id"), profile_ref["path"])
    topify_profile.assert_supported(profile)
    if (profile_ref.get("sha256") is not None
            and common.sha256_file(profile_ref["path"]) != profile_ref["sha256"]):
        raise common.PipelineError("profile bytes changed after requirements lock")
    owner = contract.get("owner_decisions", {})
    target = owner.get("target_task_count", campaign["config"]["target_task_count"])
    count = count if count is not None else owner.get("initial_producer_concurrency", 1)
    if type(count) is not int or count < 1 or type(target) is not int or target < 1:
        raise common.PipelineError("producer count and target must be positive integers")
    if transport not in manifest["transport_modes"]:
        raise common.PipelineError("requested transport is not enabled")
    employees = supervisor._employees(root)
    owners = [e for e in employees if e["role"] == "producer"
              and e.get("parent_id") == "supervisor" and e.get("employment_status") != "fired"]
    missing = max(0, target - len(owners))
    slots = max(0, manifest["max_concurrent"] - supervisor._active_count(root))
    used = {e["employee_id"] for e in employees}
    jobs = []
    ordinal = 1
    for _ in range(min(count, missing, slots)):
        while f"producer-{ordinal:04d}" in used:
            ordinal += 1
        employee_id = f"producer-{ordinal:04d}"
        jobs.append({"employee_id": employee_id, "role": "producer", "transport": transport,
                     "seed_id": None, "phase": "seed-discovery", "owns_complete_task": True,
                     "job_root": str(root / "supervision/agent-workspaces" / employee_id)})
        used.add(employee_id)
    return {"schema_version": 1, "campaign": str(root), "dispatch_mode": "producer-first",
            "target_task_count": target, "existing_producers": len(owners),
            "unfilled_task_slots": missing, "available_employee_slots": slots,
            "jobs": jobs, "status": "producers-available" if jobs else "capacity-or-target-reached"}


@mutation
def prepare(workspace, count=None, transport="opencode-task"):
    proposal = plan(workspace, count, transport)
    jobs = []
    for job in proposal["jobs"]:
        employee, _ = supervisor.hire(workspace, job["employee_id"], "producer", transport)
        dispatch, _ = supervisor.dispatch(workspace, employee["employee_id"])
        jobs.append(dispatch["dispatch"])
    return {**proposal, "jobs": jobs,
            "status": "awaiting-transport" if jobs else proposal["status"],
            "instruction": "Launch/continue each edgebench-producer in its own persistent session; "
                           "bind the returned session ID with employee-session-bind. "
                           "Do not launch standalone scouts or report prepared jobs as running."}, 0
