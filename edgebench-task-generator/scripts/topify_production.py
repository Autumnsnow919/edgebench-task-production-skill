#!/usr/bin/env python3
"""Timer-friendly campaign coordinator; agents build tasks, not this module."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess

import topify_common as common
import topify_daily
import topify_domain
import topify_seed


REGISTRY = Path(__file__).resolve().parents[1] / "references/source-registry.json"


def _job_root(root: Path, job_id: str) -> Path:
    if not isinstance(job_id, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", job_id) is None:
        raise common.PipelineError("agent job ID must be a safe slug")
    return root / "agent-workspaces" / job_id


def _reserve_job_root(root: Path, job_id: str) -> Path:
    parent = root / "agent-workspaces"
    if parent.is_symlink() or parent.exists() and not parent.is_dir():
        raise common.PipelineError("agent workspace parent is unsafe")
    parent.mkdir(exist_ok=True)
    destination = _job_root(root, job_id)
    if destination.exists() or destination.is_symlink():
        raise common.PipelineError("agent job workspace already exists; never share or reuse it")
    destination.mkdir(mode=0o700)
    return destination


def reserve_auxiliary_workspace(workspace: str | Path, job_id: str,
                                role: str) -> tuple[dict, int]:
    """Reserve a separate working directory for non-dispatch parallel agents."""
    if role not in {"scout", "feasibility", "reviewer"} or not job_id.startswith(role + "-"):
        raise common.PipelineError("auxiliary agent job ID must be bound to its role")
    root, _ = topify_seed.load_campaign(workspace)
    with (root / ".production-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another production dispatch is running") from exc
        job_root = _reserve_job_root(root, job_id)
        receipt = {"schema_version": 1, "job_id": job_id, "role": role,
                   "job_root": str(job_root), "status": "reserved",
                   "recorded_at": common.utc_now()}
        common.write_new_json(job_root / "assignment.json", receipt)
        return receipt, 0


def _claims(root: Path, directory: str) -> set[str]:
    claims = root / directory
    if claims.is_symlink() or claims.exists() and not claims.is_dir():
        raise common.PipelineError(f"unsafe {directory} directory")
    result = set()
    if claims.is_dir():
        for path in sorted(claims.glob("*.json")):
            claim = common.load_json(path)
            if isinstance(claim.get("seed_id"), str):
                topify_seed._safe_name(claim["seed_id"])
                result.add(claim["seed_id"])
    return result


def _verifier_state(root: Path) -> tuple[set[str], dict[str, str], set[str]]:
    """A failed verifier gets one retry; missing receipts never imply failure."""
    directory = root / "verifier-jobs"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise common.PipelineError("unsafe verifier-jobs directory")
    attempts: dict[str, dict[str, str | None]] = {}
    if not directory.is_dir():
        return set(), {}, set()
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".result.json"):
            continue
        claim = common.load_json(path)
        seed_id = claim.get("seed_id")
        topify_seed._safe_name(seed_id)
        job_id = claim.get("job_id")
        if not isinstance(job_id, str) or path.name != job_id + ".json":
            raise common.PipelineError("verifier job claim has a mismatched file name")
        result_path = directory / (job_id + ".result.json")
        status = None
        if result_path.exists() or result_path.is_symlink():
            if result_path.is_symlink():
                raise common.PipelineError("verifier result cannot be a symlink")
            result = common.load_json(result_path)
            if result.get("seed_id") != seed_id or result.get("job_id") != job_id:
                raise common.PipelineError("verifier result does not match its dispatched job")
            status = result.get("status")
            if status not in {"review-pending", "failed", "proposal-invalid", "proposal-missing"}:
                raise common.PipelineError("unknown verifier result status")
        attempts.setdefault(seed_id, {})[job_id] = status
    for result_path in directory.glob("*.result.json"):
        if not (directory / (result_path.name.removesuffix(".result.json") + ".json")).is_file():
            raise common.PipelineError("orphan verifier result without a job claim")
    blocked = set(attempts)
    retry = {}
    exhausted = set()
    for seed_id, jobs in attempts.items():
        first = "verify-" + hashlib.sha256(seed_id.encode()).hexdigest()[:16]
        second = first + "-retry-2"
        if first not in jobs:
            continue  # Historical/custom dispatched jobs remain blocked.
        if not set(jobs).issubset({first, second}):
            raise common.PipelineError("unexpected verifier retry job identity")
        failed = {"failed", "proposal-invalid", "proposal-missing"}
        if second in jobs:
            if jobs[second] in failed:
                exhausted.add(seed_id)
        elif jobs[first] in failed:
            retry[seed_id] = second
    return blocked, retry, exhausted


def plan(workspace: str | Path, verifier_workers: int = 6, author_workers: int = 4) -> dict:
    """Read-only balanced proposals. A plan never accepts a Seed or creates a task."""
    root, _ = topify_seed.load_campaign(workspace)
    campaign = common.load_json(root / "campaign.json")
    supervision = campaign.get("config", {}).get("supervision", {})
    max_concurrent = supervision.get("max_concurrent", 6)
    if type(max_concurrent) is not int or max_concurrent < 1:
        raise common.PipelineError("campaign supervision max_concurrent is invalid")
    if type(verifier_workers) is not int or not 1 <= verifier_workers <= max_concurrent:
        raise common.PipelineError(
            f"source verification concurrency must be 1..{max_concurrent}"
        )
    if type(author_workers) is not int or not 1 <= author_workers <= max_concurrent:
        raise common.PipelineError(
            f"authoring concurrency must be 1..{max_concurrent}"
        )
    registry = topify_seed.validate_source_registry(common.load_json(REGISTRY))
    sources = {source["id"] for source in registry["sources"]}
    domain = topify_domain.plan(root, limit=author_workers)
    verifier_claims, retries, exhausted = _verifier_state(root)
    committed = verifier_claims | _claims(root, "author-jobs")
    shelved = set(domain["shelved_seed_ids"])
    available = set()
    for folder in ("screened", "provisionals", "promoted"):
        for path in (root / folder).glob("*.json"):
            seed_id = common.load_json(path).get("seed_id")
            if isinstance(seed_id, str):
                available.add(seed_id)
    eligible = {category: [] for category in common.CATEGORIES}
    skipped_dispatched = []
    skipped_shelved = []
    for path in sorted((root / "leads").glob("*.json")):
        lead = topify_seed.validate_seed_lead(common.load_json(path))
        seed_id = lead["seed_id"]
        if path.name != topify_seed._safe_name(seed_id):
            raise common.PipelineError("lead file name differs from its seed_id")
        if seed_id in shelved:
            skipped_shelved.append(seed_id)
        elif seed_id in committed and seed_id not in retries:
            skipped_dispatched.append(seed_id)
        elif seed_id not in available and seed_id.split("/")[1] in sources:
            if seed_id in retries:
                first = "verify-" + hashlib.sha256(seed_id.encode()).hexdigest()[:16]
                original = common.load_json(root / "verifier-jobs" / (first + ".json"))
                if original.get("seed_card_sha256") != common.sha256_file(path):
                    raise common.PipelineError("failed verifier lead bytes changed before retry")
            job_id = retries.get(seed_id, "verify-" + hashlib.sha256(seed_id.encode()).hexdigest()[:16])
            eligible[lead["category"]].append({
                "seed_id": seed_id, "category": lead["category"],
                "source_id": seed_id.split("/")[1],
                "seed_card_sha256": common.sha256_file(path),
                "job_id": job_id, "job_root": str(_job_root(root, job_id)),
            })
    for values in eligible.values():
        values.sort(key=lambda item: item["seed_id"])
    selection = []
    order = sorted(common.CATEGORIES, key=lambda category: (
        len(eligible[category]) == 0,
        -topify_seed.DEFAULT_VECTOR[category], category,
    ))
    while len(selection) < verifier_workers:
        selected = False
        for category in order:
            if eligible[category] and len(selection) < verifier_workers:
                selection.append(eligible[category].pop(0))
                selected = True
        if not selected:
            break
    author_jobs = []
    for item in domain["selected"]:
        seed_id = item["seed_id"]
        revision = item["revision_index"]
        parent = item["parent_task_sha256"]
        job_id = ("author-" + hashlib.sha256(seed_id.encode()).hexdigest()[:16]
                  if revision == 0 else
                  "author-" + hashlib.sha256(f"{seed_id}:r{revision}:{parent}".encode()).hexdigest()[:16])
        if item.get("author_attempt") == 2:
            job_id += "-retry-2"
        author_jobs.append({
            "seed_id": seed_id, "category": item["category"],
            "job_id": job_id, "job_root": str(_job_root(root, job_id)),
            "workspace": str(_job_root(root, job_id) / "task"),
            "revision_index": revision, "parent_task_sha256": parent,
            **({"author_attempt": 2} if item.get("author_attempt") == 2 else {}),
            **({key: item[key] for key in ("parent_task_id", "parent_task_workspace", "parent_status")}
               if revision else {}),
        })
    return {
        "schema_version": 1, "campaign": str(root),
        "status": "authors-available" if author_jobs else "leads-need-verification" if selection else "idle",
        "verify": selection, "author": author_jobs,
        "skipped_dispatched": sorted(skipped_dispatched),
        "skipped_shelved": sorted(skipped_shelved),
        "verification_exhausted": sorted(exhausted),
        "unavailable_domains": domain["unavailable_domains"],
        "note": "Proposals only: raw leads need verified source bytes and main review; no task or approval is fabricated.",
    }


def _verifier_prompt(root: Path, job: dict) -> tuple[Path, Path, str]:
    seed_id = job["seed_id"]
    source = root / "leads" / topify_seed._safe_name(seed_id)
    if common.sha256_file(source) != job["seed_card_sha256"]:
        raise common.PipelineError("lead bytes changed")
    job_root = _job_root(root, job["job_id"])
    proposal = job_root / "proposal.json"
    prompt = (
        "You are edgebench-seed-verifier. Verify only the captured Seed Card "
        f"at {source} and official source ID {job['source_id']}. Pin task-input bytes "
        "and their hashes; source/rights/dedup reviews are not required by this owner. "
        f"Your exclusive writable working directory is {job_root}. Use it for every "
        "tool call and stage pinned source artifacts only under that directory; "
        "do not write to the shared campaign, source registry or another job. "
        "The main reviewer will serialize mirror-source after reviewing your bytes. "
        "Write a conservative proposed Seed Card and evidence to "
        f"{proposal}. Return file paths and hashes. Do not mutate the captured lead, "
        "screen, promote, author a task, or claim a license. Main-agent review is mandatory."
    )
    return source, proposal, prompt


def _finalize_verifier(root: Path, job: dict, returncode: int,
                       stdout_bytes: bytes, text: str) -> dict:
    from topify_daily import _redact_text

    proposal = _job_root(root, job["job_id"]) / "proposal.json"
    output = {"seed_id": job["seed_id"], "job_id": job["job_id"],
              "status": "review-pending" if returncode == 0 else "failed",
              "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
              "feedback": _redact_text(text)[:8000]}
    if returncode == 0 and proposal.is_file() and not proposal.is_symlink():
        try:
            candidate = common.load_json(proposal)
            if candidate.get("seed_id") != job["seed_id"] or candidate.get("status") != "lead":
                raise common.PipelineError("proposal is not a lead for this Seed")
            topify_seed.validate_seed_lead(candidate)
            output["proposal_path"] = str(proposal)
            output["proposal_sha256"] = common.sha256_file(proposal)
        except common.PipelineError:
            output["status"] = "proposal-invalid"
    elif returncode == 0:
        output["status"] = "proposal-missing"
    return output


def verifier_dispatch(workspace: str | Path, verifier_workers: int = 6) -> tuple[dict, int]:
    """Claim verifier jobs from a fresh plan and emit prompts for the main
    agent's native subagent dispatch (subagent_type edgebench-seed-verifier).
    No subprocess is spawned; record each finished subagent with
    verifier_collect.
    """
    proposal = plan(workspace, verifier_workers, 1)
    root, _ = topify_seed.load_campaign(workspace)
    if not proposal["verify"]:
        return {"schema_version": 1, "status": "idle", "jobs": [],
                "note": "No unscreened leads need verification."}, 0
    claims = root / "verifier-jobs"
    claims.mkdir(exist_ok=True)
    jobs = []
    try:
        for item in proposal["verify"]:
            job_root = _reserve_job_root(root, item["job_id"])
            _, proposal_path, prompt = _verifier_prompt(root, item)
            prompt += (
                "\n\nDispatch note: you are running as a native subagent of the main "
                "reviewer, not via `opencode run`. Return file paths and hashes as "
                "final text."
            )
            prompt_path = claims / (item["job_id"] + ".prompt.txt")
            common.write_new_json(claims / (item["job_id"] + ".json"), {
                **item, "proposal_path": str(proposal_path),
                "prompt_path": str(prompt_path),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "status": "awaiting-subagent", "dispatched_at": common.utc_now(),
            })
            prompt_path.write_text(prompt, encoding="utf-8")
            jobs.append({
                **item, "subagent": "edgebench-seed-verifier",
                "job_root": str(job_root), "proposal_path": str(proposal_path),
                "prompt_path": str(prompt_path),
                "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
            })
    except (OSError, common.PipelineError) as exc:
        for item in proposal["verify"]:
            claim = claims / (item["job_id"] + ".json")
            result = claims / (item["job_id"] + ".result.json")
            if claim.is_file() and not result.exists() and not result.is_symlink():
                common.write_new_json(result, {
                    "seed_id": item["seed_id"], "job_id": item["job_id"],
                    "status": "failed", "reason": "native verifier prompt unavailable",
                })
        raise common.PipelineError("native verifier prompt dispatch failed; claims are retryable") from exc
    return {
        "schema_version": 1, "status": "awaiting-subagents",
        "dispatch_mode": "native-subagent", "jobs": jobs,
        "instruction": "Dispatch each prompt as an edgebench-seed-verifier subagent; "
                       "then run verifier-collect for every job_id.",
        "recorded_at": common.utc_now(),
    }, 0


def verifier_collect(workspace: str | Path, job_id: str,
                     feedback_file: str | Path | None = None,
                     failed: bool = False) -> tuple[dict, int]:
    """Record one finished seed-verifier subagent and validate its proposal."""
    root, _ = topify_seed.load_campaign(workspace)
    claim_path = root / "verifier-jobs" / (job_id + ".json")
    if not claim_path.is_file() or claim_path.is_symlink():
        raise common.PipelineError("unknown verifier job claim")
    job = common.load_json(claim_path)
    expected_prompt = root / "verifier-jobs" / (job_id + ".prompt.txt")
    expected_root = _job_root(root, job_id)
    if (job.get("job_root") != str(expected_root)
            or job.get("proposal_path") != str(expected_root / "proposal.json")
            or job.get("prompt_path") != str(expected_prompt)
            or not expected_root.is_dir() or expected_root.is_symlink()
            or not expected_prompt.is_file() or expected_prompt.is_symlink()
            or job.get("prompt_sha256") != common.sha256_file(expected_prompt)):
        raise common.PipelineError("verifier job prompt or isolated workspace changed after dispatch")
    result_path = root / "verifier-jobs" / (job_id + ".result.json")
    if result_path.exists() or result_path.is_symlink():
        raise common.PipelineError("verifier job already collected")
    if failed:
        output = {"seed_id": job["seed_id"], "job_id": job_id, "status": "failed",
                  "reason": "seed verifier subagent failed"}
    else:
        if feedback_file is None:
            raise common.PipelineError("verifier-collect needs --feedback-file or --failed")
        path = Path(feedback_file)
        if not path.is_file() or path.is_symlink():
            raise common.PipelineError("feedback file must be a regular file")
        raw = path.read_bytes()
        output = _finalize_verifier(root, job, 0, raw,
                                    raw.decode("utf-8", errors="replace").strip())
    common.write_new_json(result_path, output)
    return output, 0 if output["status"] == "review-pending" else 2


def dispatch_native(workspace: str | Path, verifier_workers: int = 6,
                    author_workers: int = 4) -> tuple[dict, int]:
    """Claim one bounded production round for native Task subagents, not CLI sessions."""
    root, _ = topify_seed.load_campaign(workspace)
    with (root / ".production-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another production dispatch is running") from exc
        proposal = plan(root, verifier_workers, author_workers)
        if not proposal["verify"] and not proposal["author"]:
            return {"schema_version": 1, "status": "idle", "dispatch_mode": "native-subagent",
                    "jobs": [], "plan": proposal}, 0
        number, attempt = common.next_attempt(root / "production-dispatches")
        if proposal["author"]:
            for item in proposal["author"]:
                _reserve_job_root(root, item["job_id"])
            manifest = {"schema_version": 1, "jobs": [
                {key: item[key] for key in ("job_id", "seed_id", "workspace",
                                             "revision_index", "parent_task_sha256",
                                             "parent_task_id", "parent_task_workspace",
                                             "parent_status", "author_attempt") if key in item}
                for item in proposal["author"]
            ]}
            manifest_path = attempt / "author-manifest.json"
            common.write_new_json(manifest_path, manifest)
            topify_daily._validate_author_jobs(root, topify_domain.load_playbook(), manifest_path)
        jobs = []
        errors = []
        try:
            if proposal["verify"]:
                verifiers, _ = verifier_dispatch(root, verifier_workers)
                jobs.extend(verifiers["jobs"])
            if proposal["author"]:
                authors, _ = topify_daily.author_dispatch(root, manifest_path)
                jobs.extend(authors["jobs"])
        except (common.PipelineError, OSError) as exc:
            errors.append(str(exc))
        report = {"schema_version": 1, "attempt": number,
                  "status": "partial" if errors else "awaiting-subagents",
                  "dispatch_mode": "native-subagent", "plan": proposal, "jobs": jobs,
                  "errors": errors, "recorded_at": common.utc_now(),
                  "instruction": "Invoke each job's named native subagent with its hashed prompt; "
                                 "then call verifier-collect or author-collect by job_id. "
                                 "Main agent independently reviews proposals and registers measured candidates."}
        common.write_new_json(attempt / "summary.json", report)
        return report, 2 if errors else 0


def _verify_one(root: Path, job: dict, opencode: str, timeout: int) -> dict:
    from topify_daily import _final_text

    seed_id = job["seed_id"]
    source = root / "leads" / topify_seed._safe_name(seed_id)
    if common.sha256_file(source) != job["seed_card_sha256"]:
        return {"seed_id": seed_id, "job_id": job["job_id"], "status": "failed", "reason": "lead bytes changed"}
    _, proposal, prompt = _verifier_prompt(root, job)
    try:
        result = subprocess.run(
            [opencode, "run", "--agent", "edgebench-seed-verifier", "--format", "json", prompt],
            cwd=_job_root(root, job["job_id"]), env=os.environ.copy(),
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"seed_id": seed_id, "job_id": job["job_id"], "status": "failed",
                "reason": "seed verifier unavailable or timed out"}
    return _finalize_verifier(root, job, result.returncode, result.stdout,
                              _final_text(result.stdout.decode("utf-8", errors="replace")))


def _tick_once(workspace: str | Path, verifier_workers: int, author_workers: int,
               opencode: str, execute: bool, verifier_timeout: int) -> tuple[dict, int]:
    proposal = plan(workspace, verifier_workers, author_workers)
    if not execute:
        return proposal, 0
    if type(verifier_timeout) is not int or verifier_timeout < 30:
        raise common.PipelineError("seed verification timeout must be at least 30 seconds")
    root, _ = topify_seed.load_campaign(workspace)
    attempt_number, attempt = common.next_attempt(root / "production-ticks")
    results = []
    if proposal["verify"]:
        claims = root / "verifier-jobs"
        claims.mkdir(exist_ok=True)
        for item in proposal["verify"]:
            _reserve_job_root(root, item["job_id"])
            common.write_new_json(claims / (item["job_id"] + ".json"), {
                **item, "proposal_path": str(_job_root(root, item["job_id"]) / "proposal.json"),
                "status": "dispatched", "dispatched_at": common.utc_now(),
            })
        with ThreadPoolExecutor(max_workers=min(verifier_workers, len(proposal["verify"]))) as pool:
            results = [future.result() for future in as_completed(
                [pool.submit(_verify_one, root, item, opencode, verifier_timeout)
                 for item in proposal["verify"]]
            )]
        results.sort(key=lambda item: item["seed_id"])
        for result in results:
            common.write_new_json(root / "verifier-jobs" / (result["job_id"] + ".result.json"), result)
    author_report = None
    if proposal["author"]:
        for item in proposal["author"]:
            _reserve_job_root(root, item["job_id"])
        manifest = {"schema_version": 1, "jobs": [
            {key: item[key] for key in ("job_id", "seed_id", "workspace",
                                         "revision_index", "parent_task_sha256",
                                         "parent_task_id", "parent_task_workspace",
                                         "parent_status", "author_attempt") if key in item}
            for item in proposal["author"]
        ]}
        manifest_path = attempt / "author-manifest.json"
        common.write_new_json(manifest_path, manifest)
        author_report, _ = topify_daily.author_batch(root, manifest_path,
                                                    max_workers=author_workers, opencode=opencode)
        for item in author_report["results"]:
            if item.get("status") not in {"controls-pending", "revise", "review-pending"}:
                continue
            try:
                receipt, _ = topify_seed.register_candidate(root, item["seed_id"], Path(item["workspace"]))
                item["candidate_receipt"] = {"status": receipt["status"],
                                             "task_sha256": receipt["task_sha256"]}
            except (common.PipelineError, OSError, ValueError) as exc:
                item["candidate_receipt"] = {"status": "incomplete", "reason": str(exc)}
    report = {
        "schema_version": 1, "attempt": attempt_number,
        "status": "review-pending" if results or author_report else "idle",
        "plan": proposal, "verifier_results": results,
        "author_report": author_report,
        "recorded_at": common.utc_now(),
        "note": "Only subagent work/receipts were dispatched; the main reviewer must accept or reject Seed and task proposals.",
    }
    common.write_new_json(attempt / "summary.json", report)
    return report, 2 if report["status"] == "review-pending" else 0


def tick(workspace: str | Path, verifier_workers: int = 6, author_workers: int = 4,
         opencode: str = "opencode", execute: bool = False,
         verifier_timeout: int = 900, notify_main: bool = False) -> tuple[dict, int]:
    if not execute:
        return plan(workspace, verifier_workers, author_workers), 0
    root, _ = topify_seed.load_campaign(workspace)
    with (root / ".production-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another production-tick is running") from exc
        report, code = _tick_once(root, verifier_workers, author_workers,
                                  opencode, True, verifier_timeout)
        if notify_main and report["status"] == "review-pending":
            attempt = root / "production-ticks" / f"attempt-{report['attempt']:04d}"
            review_path = attempt / "main-review.json"
            if review_path.exists() or review_path.is_symlink():
                raise common.PipelineError("main review already exists before notification")
            prompt = (
                f"You are edgebench-main. Read the immutable production tick at {attempt / 'summary.json'}. "
                "Review verifier proposals, author receipts and cheap baseline audit outcomes. "
                "Only accept a Seed after pinned source bytes and a testable task loop; "
                "record revision/shelving decisions for shallow task families. "
                "Do not certify task quality or start frontier rollouts from a model's prose."
            )
            try:
                process = subprocess.run(
                    [opencode, "run", "--agent", "edgebench-main", "--format", "json", prompt],
                    cwd=Path(__file__).resolve().parents[2], env=os.environ.copy(),
                    capture_output=True, timeout=1200, check=False,
                )
                response = topify_daily._redact_text(
                    topify_daily._final_text(process.stdout.decode("utf-8", errors="replace"))
                )
                main = {
                    "status": "reported" if process.returncode == 0 and response else "failed",
                    "stdout_sha256": hashlib.sha256(process.stdout).hexdigest(),
                    "feedback": response[:16000] if response else None,
                }
            except (OSError, subprocess.TimeoutExpired):
                main = {"status": "failed", "feedback": None}
            if review_path.exists() or review_path.is_symlink():
                if review_path.is_symlink() or not review_path.is_file():
                    raise common.PipelineError("main-agent review is not a regular file")
                written = common.load_json(review_path)
                if (written.get("campaign") != str(root)
                        or written.get("production_tick") != str((attempt / "summary.json").relative_to(root))
                        or written.get("reviewer_role") != "main-agent"
                        or written.get("review_status") not in {"complete", "incomplete"}):
                    raise common.PipelineError("main-agent review does not match this tick")
                report["main_review_status"] = "reported" if main["status"] == "reported" else "failed"
            else:
                common.write_new_json(review_path, main)
                report["main_review_status"] = main["status"]
            report["main_review_path"] = str(review_path)
        return report, code
