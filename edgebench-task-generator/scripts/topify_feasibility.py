#!/usr/bin/env python3
"""Bounded, review-only pre-screen feasibility probes for verified Seed leads.

No receipt here confers Seed eligibility, SForge runtime parity, or native
cross-architecture scoring. Authors still need the normal Seed screen.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess

import topify_common as common
import topify_daily
import topify_seed


def _regular_below(root: Path, relative: str) -> Path:
    if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
        raise common.PipelineError("unsafe feasibility work directory")
    if not isinstance(relative, str):
        raise common.PipelineError("probe receipt needs a relative path")
    parsed = PurePosixPath(relative)
    if (parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts
            or str(parsed) != relative):
        raise common.PipelineError("probe receipt must remain inside its work directory")
    current = root
    for part in parsed.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise common.PipelineError("probe receipt file missing") from exc
        if stat.S_ISLNK(mode):
            raise common.PipelineError("probe receipt cannot use symlinks")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise common.PipelineError("probe receipt is not a regular file")
    return current


def _source(root: Path, seed_id: str) -> dict:
    lead_path = root / "leads" / topify_seed._safe_name(seed_id)
    lead = topify_seed.validate_seed_lead(common.load_json(lead_path))
    if lead["seed_id"] != seed_id:
        raise common.PipelineError("feasibility lead identity mismatch")
    jobs = root / "verifier-jobs"
    if jobs.is_symlink():
        raise common.PipelineError("unsafe verifier jobs directory")
    candidates = []
    for receipt_path in sorted(jobs.glob("*.result.json")):
        receipt = common.load_json(receipt_path)
        if receipt.get("seed_id") != seed_id or receipt.get("status") != "review-pending":
            continue
        job_id = receipt.get("job_id")
        if not isinstance(job_id, str) or receipt_path.name != job_id + ".result.json":
            raise common.PipelineError("invalid verifier receipt identity")
        claim = common.load_json(jobs / (job_id + ".json"))
        if (claim.get("job_id") != job_id or claim.get("seed_id") != seed_id
                or claim.get("seed_card_sha256") != common.sha256_file(lead_path)):
            raise common.PipelineError("verifier claim is not bound to current lead bytes")
        native_root = root / "agent-workspaces" / job_id
        native_prompt = jobs / (job_id + ".prompt.txt")
        native_proposal = native_root / "proposal.json"
        if any(key in claim for key in ("job_root", "prompt_path", "prompt_sha256", "proposal_path")):
            if (claim.get("job_root") != str(native_root)
                    or claim.get("proposal_path") != str(native_proposal)
                    or claim.get("prompt_path") != str(native_prompt)
                    or native_root.is_symlink() or not native_root.is_dir()
                    or native_prompt.is_symlink() or not native_prompt.is_file()
                    or claim.get("prompt_sha256") != common.sha256_file(native_prompt)):
                raise common.PipelineError("native verifier claim or signed prompt mismatch")
            expected = native_proposal
        else:
            expected = root / "verifier-proposals" / job_id / "proposal.json"
        if (receipt.get("proposal_path") != str(expected)
                or expected.is_symlink() or expected.parent.is_symlink()
                or common.sha256_file(expected) != receipt.get("proposal_sha256")):
            raise common.PipelineError("verified proposal path or SHA-256 mismatch")
        proposal = topify_seed.validate_seed_lead(common.load_json(expected))
        if (proposal["seed_id"] != seed_id or proposal["category"] != lead["category"]
                or len(proposal["origins"]) <= len(lead["origins"])):
            raise common.PipelineError("proposal lacks a pinned artifact for this lead")
        matched = []
        captures = root / "mirrors/captures"
        if captures.is_symlink():
            raise common.PipelineError("unsafe source capture directory")
        for origin in proposal["origins"][len(lead["origins"]):]:
            digest = origin.get("artifact_sha256")
            blob = root / "mirrors" / f"{digest}.blob"
            if (not isinstance(digest, str) or not common.HEX64.fullmatch(digest)
                    or origin.get("offline_mirror") != str(blob)
                    or blob.is_symlink() or common.sha256_file(blob) != digest):
                continue
            for path in captures.glob("*.json"):
                capture = common.load_json(path)
                if (capture.get("source_id") == seed_id.split("/")[1]
                        and capture.get("sha256") == digest
                        and capture.get("mirror_path") == str(blob)
                        and capture.get("source_url") == origin.get("url")
                        and capture.get("revision") == origin.get("revision")
                        and capture.get("status") == "lead-only"):
                    matched.append({"sha256": digest, "mirror_path": str(blob),
                                    "capture_path": str(path),
                                    "capture_sha256": common.sha256_file(path)})
                    break
        if not matched:
            raise common.PipelineError("no current capture receipt binds the proposed mirror")
        candidates.append({"seed_id": seed_id, "category": lead["category"],
                           "source_proposal": str(expected),
                           "proposal_sha256": receipt["proposal_sha256"],
                           "source_sha256": matched[0]["sha256"],
                           "source_path": matched[0]["mirror_path"],
                           "capture_path": matched[0]["capture_path"],
                           "capture_sha256": matched[0]["capture_sha256"]})
    if not candidates:
        raise common.PipelineError("feasibility needs a current verified source proposal")
    return candidates[-1]


def plan(workspace: str | Path, seed_ids: list[str], workers: int = 2) -> dict:
    root, _ = topify_seed.load_campaign(workspace)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise common.PipelineError("feasibility worker count must be 1..4")
    if not seed_ids or len(seed_ids) != len(set(seed_ids)) or len(seed_ids) > 4:
        raise common.PipelineError("select 1 to 4 distinct verified Seeds for feasibility")
    jobs = []
    skipped = []
    for seed_id in seed_ids:
        topify_seed._safe_name(seed_id)
        if (root / "seed-family-decisions" / topify_seed._safe_name(seed_id)).exists():
            raise common.PipelineError("shelved Seed cannot enter feasibility queue")
        source = _source(root, seed_id)
        job_id = "feasibility-" + hashlib.sha256(
            (seed_id + ":" + source["proposal_sha256"]).encode()).hexdigest()[:16]
        job = {**source, "job_id": job_id,
               "workspace": str(root / "agent-workspaces" / job_id)}
        job_root = root / "feasibility-jobs"
        claim = job_root / (job_id + ".json")
        if claim.exists() or claim.is_symlink():
            if claim.is_symlink():
                raise common.PipelineError("feasibility job claim cannot be a symlink")
            original = common.load_json(claim)
            if (original.get("job_id") != job_id or original.get("seed_id") != seed_id
                    or original.get("proposal_sha256") != source["proposal_sha256"]):
                raise common.PipelineError("feasibility job claim changed identity")
            outcome = job_root / (job_id + ".result.json")
            retry_id = job_id + "-retry-2"
            retry_claim = job_root / (retry_id + ".json")
            if (outcome.is_file() and not outcome.is_symlink()
                    and not retry_claim.exists() and not retry_claim.is_symlink()):
                result = common.load_json(outcome)
                if (result.get("job_id") != job_id or result.get("seed_id") != seed_id):
                    raise common.PipelineError("feasibility result changed identity")
                if result.get("status") == "failed":
                    jobs.append({**job, "job_id": retry_id,
                                  "workspace": str(root / "agent-workspaces" / retry_id)})
                    continue
            skipped.append(seed_id)
        else:
            jobs.append(job)
    return {"schema_version": 1, "campaign": str(root), "jobs": jobs,
            "skipped_dispatched": skipped, "workers": workers,
            "status": "ready" if jobs else "idle",
            "note": "Read-only plan; pre-screen probes cannot approve Seeds or replace arm64 SForge scores."}


def _native_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "linux/amd64"
    if machine in {"aarch64", "arm64"}:
        return "linux/arm64"
    raise common.PipelineError("unknown native feasibility-probe architecture")


def _validate(root: Path, job: dict, proposal_relative: str = "proposal.json") -> dict:
    work = Path(job["workspace"])
    proposal_path = _regular_below(work, proposal_relative)
    proposal = common.load_json(proposal_path)
    keys = {"schema_version", "seed_id", "source_proposal_sha256", "native_architecture",
            "arm64_status", "score_seconds", "dependency_closure", "results", "unresolved"}
    if (not isinstance(proposal, dict) or set(proposal) != keys
            or type(proposal["schema_version"]) is not int or proposal["schema_version"] != 1
            or proposal["seed_id"] != job["seed_id"]
            or proposal["source_proposal_sha256"] != job["proposal_sha256"]
            or proposal["native_architecture"] != _native_architecture()
            or proposal["arm64_status"] != "pending"
            or proposal["dependency_closure"] not in {"incomplete", "measured"}
            or not isinstance(proposal["unresolved"], list)
            or not all(isinstance(v, str) and v.strip() for v in proposal["unresolved"])):
        raise common.PipelineError("invalid or unbound feasibility proposal")
    results = proposal["results"]
    if not isinstance(results, list) or not 1 <= len(results) <= 10:
        raise common.PipelineError("feasibility needs 1..10 process receipts")
    seen = set()
    receipts = []
    for entry in results:
        if (not isinstance(entry, dict)
                or set(entry) != {"name", "process_path", "process_sha256"}
                or not isinstance(entry["name"], str) or not entry["name"].strip()
                or entry["name"] in seen):
            raise common.PipelineError("invalid or repeated process receipt name")
        seen.add(entry["name"])
        folder = PurePosixPath(entry["process_path"])
        if folder.name != "process.json" or len(folder.parts) < 2:
            raise common.PipelineError("process receipt path must name a probe directory")
        process_path = _regular_below(work, entry["process_path"])
        if (not isinstance(entry["process_sha256"], str)
                or not common.HEX64.fullmatch(entry["process_sha256"])
                or common.sha256_file(process_path) != entry["process_sha256"]):
            raise common.PipelineError("process receipt digest mismatch")
        process = common.load_json(process_path)
        if (type(process.get("pid")) is not int or process["pid"] <= 0
                or type(process.get("exit_code")) is not int
                or not common.finite_number(process.get("elapsed_seconds"))
                or process["elapsed_seconds"] <= 0
                or not isinstance(process.get("argv"), list) or not process["argv"]
                or not all(isinstance(arg, str) for arg in process["argv"])):
            raise common.PipelineError("process metadata malformed")
        for stream in ("stdout", "stderr"):
            payload = _regular_below(work, str(folder.parent / (stream + ".bin")))
            if common.sha256_file(payload) != process.get(stream + "_sha256"):
                raise common.PipelineError("process output hash mismatch")
        receipts.append({"name": entry["name"], "process_sha256": entry["process_sha256"],
                         "exit_code": process["exit_code"],
                         "elapsed_seconds": process["elapsed_seconds"]})
    score = proposal["score_seconds"]
    scorers = [receipt for receipt in receipts if receipt["name"] == "scorer"
               and receipt["exit_code"] == 0]
    if score is not None and (not common.finite_number(score) or score <= 0
                              or len(scorers) != 1 or score != scorers[0]["elapsed_seconds"]):
        raise common.PipelineError("score_seconds lacks a matching successful scorer receipt")
    return {"proposal_path": str(proposal_path), "proposal_sha256": common.sha256_file(proposal_path),
            "processes": receipts, "native_architecture": proposal["native_architecture"],
            "score_seconds": score, "arm64_status": "pending"}


def _run(root: Path, job: dict, opencode: str, timeout: int, *, micro: bool = False) -> dict:
    work = Path(job["workspace"])
    work.mkdir()  # A unique, empty workspace; never reuse another job's files.
    micro_note = (
        " MICRO-SCORER PROTOTYPE MODE: you may author a temporary, task-local "
        "one-shot scorer and cheap baseline inside this isolated directory solely "
        "to test whether a real offline scoring loop is feasible. Run an actual "
        "process named scorer when possible; it must exit 0, use only the pinned "
        "source bytes and local runtime, and its elapsed_seconds must equal "
        "score_seconds. A temporary scorer is not the production judge."
        if micro else ""
    )
    prompt = (
        "You are edgebench-feasibility."
        + micro_note
        + f" Only investigate the verified Seed "
        f"{job['source_proposal']} (SHA-256 {job['proposal_sha256']}). "
        f"Use only pinned source bytes {job['source_path']} (SHA-256 {job['source_sha256']}). "
        f"Work directory: {work}. Stay inside that directory for your own files. "
        "Do not author a task, approve a Seed, call seed-screen, access credentials, "
        "run SForge/frontier rollouts, or change campaign leads and mirrors. "
        "Do not download/install packages or spend more than 120 seconds in "
        "any single probe subprocess. Perform short, actual local process probes for data parsing, dependency "
        "availability, and if possible a real offline one-shot scorer. Record each "
        "subprocess's true argv, pid, exit_code, elapsed_seconds and raw stdout.bin/"
        "stderr.bin under a named probes/ directory. Write each process.json with "
        "stdout_sha256 and stderr_sha256 matching those raw files. "
        f"Write the proposal at the exact absolute path {work / 'proposal.json'} "
        "(not under a nested work/ directory) with EXACT keys: schema_version=1, seed_id, "
        "source_proposal_sha256, native_architecture (linux/amd64 or linux/arm64), "
        "arm64_status='pending', score_seconds=null unless you really ran a "
        "successful process named scorer (equal its elapsed_seconds), "
        "dependency_closure='measured' or 'incomplete', results (1..10 entries, "
        "each with EXACT keys name, process_path, process_sha256; the value of "
        "process_path must be workspace-relative), and unresolved "
        "(list of explicit gaps). If dependencies, a domain scorer or native arm64 "
        "are unavailable, leave those incomplete. Never invent measurements."
    )
    try:
        proc = subprocess.run(
            [opencode, "run", "--agent", "edgebench-feasibility", "--format", "json", prompt],
            cwd=work, env=os.environ.copy(),
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"seed_id": job["seed_id"], "job_id": job["job_id"], "status": "failed",
                "reason": "feasibility probe unavailable or timed out"}
    result = {"seed_id": job["seed_id"], "job_id": job["job_id"],
               "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
               "stderr_sha256": hashlib.sha256(proc.stderr).hexdigest(),
               "exit_code": proc.returncode,
               "feedback": topify_daily._redact_text(topify_daily._final_text(
                   proc.stdout.decode("utf-8", errors="replace")))[:4000]}
    if proc.returncode != 0:
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
                status_code = event.get("error", {}).get("data", {}).get("statusCode")
            except (ValueError, AttributeError, TypeError):
                continue
            if (event.get("type") == "error" and type(status_code) is int
                    and 100 <= status_code <= 599):
                result["error_status_code"] = status_code
                break
        result.update(status="failed", reason="feasibility agent exited nonzero")
    else:
        try:
            result.update(_validate(root, job), status="review-pending")
        except (common.PipelineError, OSError, KeyError, TypeError, ValueError) as exc:
            result.update(status="proposal-invalid", reason=str(exc))
    return result


def tick(workspace: str | Path, seed_ids: list[str], workers: int = 2,
         opencode: str = "opencode", timeout: int = 1800,
         execute: bool = False) -> tuple[dict, int]:
    if not execute:
        return plan(workspace, seed_ids, workers), 0
    if type(timeout) is not int or not 30 <= timeout <= 7200:
        raise common.PipelineError("feasibility timeout must be 30..7200 seconds")
    root, _ = topify_seed.load_campaign(workspace)
    with (root / ".feasibility-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another feasibility tick is running") from exc
        preview = plan(root, seed_ids, workers)
        if not preview["jobs"]:
            return {**preview, "status": "idle", "results": []}, 0
        jobs_root = root / "feasibility-jobs"
        workspace_root = root / "agent-workspaces"
        for parent in (jobs_root, workspace_root):
            if parent.is_symlink() or parent.exists() and not parent.is_dir():
                raise common.PipelineError("unsafe feasibility directory")
            parent.mkdir(exist_ok=True)
        for job in preview["jobs"]:
            if Path(job["workspace"]).exists() or Path(job["workspace"]).is_symlink():
                raise common.PipelineError("feasibility workspace was already allocated")
        for job in preview["jobs"]:
            common.write_new_json(jobs_root / (job["job_id"] + ".json"), {
                **job, "status": "dispatched", "dispatched_at": common.utc_now(),
            })
        with ThreadPoolExecutor(max_workers=min(workers, len(preview["jobs"]))) as pool:
            results = [future.result() for future in as_completed(
                [pool.submit(_run, root, job, opencode, timeout) for job in preview["jobs"]])]
        results.sort(key=lambda item: item["seed_id"])
        for item in results:
            common.write_new_json(jobs_root / (item["job_id"] + ".result.json"), item)
        number, attempt = common.next_attempt(root / "feasibility-ticks")
        report = {"schema_version": 1, "attempt": number, "status": "review-pending",
                  "plan": preview, "results": results, "recorded_at": common.utc_now(),
                  "note": "Host-local author probes only; no Seed, task, or arm64 acceptance."}
        common.write_new_json(attempt / "summary.json", report)
        return report, 2


def review(workspace: str | Path, seed_id: str,
           proposal_relative: str = "proposal.json") -> tuple[dict, int]:
    """Recheck retained agent bytes without editing an immutable failed tick."""
    root, _ = topify_seed.load_campaign(workspace)
    source = _source(root, seed_id)
    job_id = "feasibility-" + hashlib.sha256(
        (seed_id + ":" + source["proposal_sha256"]).encode()).hexdigest()[:16]
    jobs = root / "feasibility-jobs"
    if (jobs / (job_id + "-retry-2.json")).is_file():
        job_id += "-retry-2"
    claim = common.load_json(jobs / (job_id + ".json"))
    if (claim.get("job_id") != job_id or claim.get("seed_id") != seed_id
            or claim.get("proposal_sha256") != source["proposal_sha256"]):
        raise common.PipelineError("feasibility claim does not match the verified source")
    assigned = {**source, "job_id": job_id,
                 "workspace": str(root / "agent-workspaces" / job_id)}
    # Path traversal and symlinks fail without allocating a reviewer attempt.
    _regular_below(Path(assigned["workspace"]), proposal_relative)
    try:
        observation = _validate(root, assigned, proposal_relative)
        status, reason = "review-pending", None
    except (common.PipelineError, OSError, TypeError, KeyError, ValueError) as exc:
        observation, status, reason = {}, "incomplete", str(exc)
    attempt_number, attempt = common.next_attempt(root / "feasibility-reviews" / job_id)
    report = {"schema_version": 1, "attempt": attempt_number,
              "seed_id": seed_id, "job_id": job_id, "status": status,
              "source_proposal_sha256": source["proposal_sha256"],
              "reason": reason, **observation,
              "recorded_at": common.utc_now(),
              "note": "Receipt verification only; task, Seed and arm64 remain unapproved."}
    common.write_new_json(attempt / "summary.json", report)
    return report, 2


def _micro_job_id(seed_id: str, proposal_sha256: str) -> str:
    return "micro-" + hashlib.sha256(
        (seed_id + ":" + proposal_sha256).encode()).hexdigest()[:16]


def _prior_feasibility(root: Path, seed_id: str, proposal_sha256: str) -> str:
    base = "feasibility-" + hashlib.sha256(
        (seed_id + ":" + proposal_sha256).encode()).hexdigest()[:16]
    jobs = root / "feasibility-jobs"
    for job_id in (base + "-retry-2", base):
        claim_path = jobs / (job_id + ".json")
        result_path = jobs / (job_id + ".result.json")
        if not claim_path.is_file() or claim_path.is_symlink():
            continue
        claim = common.load_json(claim_path)
        result = common.load_json(result_path) if result_path.is_file() and not result_path.is_symlink() else {}
        if (claim.get("job_id") == job_id and claim.get("seed_id") == seed_id
                and claim.get("proposal_sha256") == proposal_sha256
                and result.get("job_id") == job_id and result.get("seed_id") == seed_id
                and result.get("status") == "review-pending"):
            return job_id
    raise common.PipelineError("micro-prototype requires a prior feasibility review-pending result")


def micro_plan(workspace: str | Path, seed_ids: list[str], workers: int = 2) -> dict:
    """Plan temporary scorer probes only after source parsing/dependency review."""
    root, _ = topify_seed.load_campaign(workspace)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise common.PipelineError("micro-prototype worker count must be 1..4")
    if not seed_ids or len(seed_ids) != len(set(seed_ids)) or len(seed_ids) > 4:
        raise common.PipelineError("select 1 to 4 distinct verified Seeds for micro-prototype")
    jobs = []
    skipped = []
    for seed_id in seed_ids:
        topify_seed._safe_name(seed_id)
        if (root / "seed-family-decisions" / topify_seed._safe_name(seed_id)).exists():
            raise common.PipelineError("shelved Seed cannot enter micro-prototype queue")
        source = _source(root, seed_id)
        prior = _prior_feasibility(root, seed_id, source["proposal_sha256"])
        job_id = _micro_job_id(seed_id, source["proposal_sha256"])
        job = {**source, "job_id": job_id, "prior_feasibility_job_id": prior,
                "workspace": str(root / "agent-workspaces" / job_id)}
        claim = root / "micro-prototype-jobs" / (job_id + ".json")
        if claim.exists() or claim.is_symlink():
            skipped.append(seed_id)
        else:
            jobs.append(job)
    return {"schema_version": 1, "campaign": str(root), "jobs": jobs,
            "skipped_dispatched": skipped, "workers": workers,
            "status": "ready" if jobs else "idle",
            "note": "Read-only plan; temporary micro-scorers cannot approve Seeds or replace task judges."}


def micro_tick(workspace: str | Path, seed_ids: list[str], workers: int = 2,
               opencode: str = "opencode", timeout: int = 1800,
               execute: bool = False) -> tuple[dict, int]:
    if not execute:
        return micro_plan(workspace, seed_ids, workers), 0
    if type(timeout) is not int or not 30 <= timeout <= 7200:
        raise common.PipelineError("micro-prototype timeout must be 30..7200 seconds")
    root, _ = topify_seed.load_campaign(workspace)
    with (root / ".micro-prototype-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another micro-prototype tick is running") from exc
        preview = micro_plan(root, seed_ids, workers)
        if not preview["jobs"]:
            return {**preview, "status": "idle", "results": []}, 0
        jobs_root = root / "micro-prototype-jobs"
        workspace_root = root / "agent-workspaces"
        for parent in (jobs_root, workspace_root):
            if parent.is_symlink() or parent.exists() and not parent.is_dir():
                raise common.PipelineError("unsafe micro-prototype directory")
            parent.mkdir(exist_ok=True)
        for job in preview["jobs"]:
            if Path(job["workspace"]).exists() or Path(job["workspace"]).is_symlink():
                raise common.PipelineError("micro-prototype workspace was already allocated")
        for job in preview["jobs"]:
            common.write_new_json(jobs_root / (job["job_id"] + ".json"), {
                **job, "status": "dispatched", "dispatched_at": common.utc_now(),
            })
        with ThreadPoolExecutor(max_workers=min(workers, len(preview["jobs"]))) as pool:
            results = [future.result() for future in as_completed(
                [pool.submit(_run, root, job, opencode, timeout, micro=True)
                 for job in preview["jobs"]])]
        results.sort(key=lambda item: item["seed_id"])
        for item in results:
            if item.get("status") == "review-pending":
                item["score_accuracy"] = "unvalidated"
                item["accuracy_limits"] = [
                    "temporary scorer receipt verifies process execution, not score correctness",
                    "not a production judge, baseline, upper anchor, difficulty conclusion, or Seed approval",
                ]
            common.write_new_json(jobs_root / (item["job_id"] + ".result.json"), item)
        number, attempt = common.next_attempt(root / "micro-prototype-ticks")
        report = {"schema_version": 1, "attempt": number, "status": "review-pending",
                  "plan": preview, "results": results, "recorded_at": common.utc_now(),
                  "note": "Temporary scorer evidence only; no Seed, task, judge, or arm64 acceptance."}
        common.write_new_json(attempt / "summary.json", report)
        return report, 2


def micro_review(workspace: str | Path, seed_id: str,
                 proposal_relative: str = "proposal.json") -> tuple[dict, int]:
    root, _ = topify_seed.load_campaign(workspace)
    source = _source(root, seed_id)
    job_id = _micro_job_id(seed_id, source["proposal_sha256"])
    jobs = root / "micro-prototype-jobs"
    claim = common.load_json(jobs / (job_id + ".json"))
    if (claim.get("job_id") != job_id or claim.get("seed_id") != seed_id
            or claim.get("proposal_sha256") != source["proposal_sha256"]):
        raise common.PipelineError("micro-prototype claim does not match the verified source")
    assigned = {**source, "job_id": job_id,
                 "workspace": str(root / "agent-workspaces" / job_id)}
    _regular_below(Path(assigned["workspace"]), proposal_relative)
    try:
        observation = _validate(root, assigned, proposal_relative)
        status, reason = "review-pending", None
    except (common.PipelineError, OSError, TypeError, KeyError, ValueError) as exc:
        observation, status, reason = {}, "incomplete", str(exc)
    attempt_number, attempt = common.next_attempt(root / "micro-prototype-reviews" / job_id)
    report = {"schema_version": 1, "attempt": attempt_number,
              "seed_id": seed_id, "job_id": job_id, "status": status,
              "source_proposal_sha256": source["proposal_sha256"],
              "reason": reason, **observation,
              "recorded_at": common.utc_now(),
              "note": "Temporary scorer receipt verification only; production judge, Seed and arm64 remain unapproved."}
    if status == "review-pending":
        report["score_accuracy"] = "unvalidated"
        report["accuracy_limits"] = [
            "temporary scorer receipt verifies process execution, not score correctness",
            "not a production judge, baseline, upper anchor, difficulty conclusion, or Seed approval",
        ]
    common.write_new_json(attempt / "summary.json", report)
    return report, 2
