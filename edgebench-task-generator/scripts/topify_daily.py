#!/usr/bin/env python3
"""Bounded, timer-friendly OpenCode scouts that only enqueue source captures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

import topify_common as common
import topify_domain
import topify_seed


SOURCE_ID = re.compile(r"[a-z0-9-]+\Z")
PROJECT_OWNER_ENV = Path(__file__).resolve().parents[2] / ".env"
_CREDENTIAL_NAMES = frozenset({"COMPANY_API_KEY", "DMX_API_KEY", "BIGMODEL_API_KEY",
                                "LITELLM_API_KEY", "OPENAI_API_KEY",
                                "SFORGE_AGENT_API_KEY", "HOSPITAL_PROVIDER_API_KEY"})


def _capture_from_opencode(stdout: str) -> dict:
    """Accept a strict capture or concatenated text parts from JSON event output."""
    try:
        value = json.loads(stdout.strip())
        if isinstance(value, dict) and value.get("schema_version") == 1 and "records" in value:
            return value
    except ValueError:
        pass
    parts = []
    for line in stdout.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    result = json.loads("".join(parts).strip())
    if not isinstance(result, dict):
        raise ValueError("scout response was not a JSON capture object")
    return result


def _final_text(stdout: str) -> str:
    parts = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "".join(parts).strip()


def _redact_text(value: str) -> str:
    secrets = []
    for name in _CREDENTIAL_NAMES:
        secret = os.environ.get(name)
        if secret:
            secrets.append(secret)
    try:
        source = PROJECT_OWNER_ENV
        if not source.is_symlink():
            info = source.stat()
            if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                for line in source.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        name, secret = line.split("=", 1)
                        if name.strip() in _CREDENTIAL_NAMES and secret.strip():
                            secrets.append(secret.strip().strip('"').strip("'"))
    except (OSError, UnicodeError):
        pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def author_prompt(item: dict, seed_path: Path, playbook: dict) -> str:
    """Give each author its actual domain, work mechanism and cheap controls."""
    category = item["seed_id"].split("/", 1)[0]
    domain = playbook["domains"].get(category)
    if domain is None:
        raise common.PipelineError("author job category is absent from the six-domain playbook")
    mechanisms = "; ".join(domain["task_mechanisms"])
    controls = "; ".join(domain["cheap_controls"])
    base = (
        f"You are the EdgeBench author subagent. Your exclusive writable working "
        f"directory is {Path(item['workspace']).parent}; create the task only at "
        f"{item['workspace']} and set tool working directories under your assigned job root. "
        f"with the screened Seed Card {seed_path}. Job ID {item['job_id']}. "
        f"Your EdgeBench category is {category}. This is a task-family authoring "
        "assignment, not a completed task or permission to recycle an "
        "optimization/scheduling fixture. Choose a source-grounded mechanism "
        f"appropriate to this domain (possibilities, not predetermined answers): {mechanisms}. "
        f"Implement and measure cheap domain controls before rollout: {controls}. "
        "Build a genuine, measured task following edgebench-task-generator/SKILL.md "
        "and references/domain-production.json. Use the Seed's real work, "
        "own snapshot artifact, scored objective, actionable development feedback "
        "and separate judge/work contexts. When a task-family plugin does not "
        "exist, use a source-specific authored_context within your own job directory; "
        "send a proposed plugin file there for main review rather than editing shared generator code. "
        "mini_continuous is test-only. "
        "After a real NOP/starter/interior/reference ladder, write an authoring-only "
        "portable generic-control generator and manifest, run run-controls and "
        "candidate-audit, and report any near-reference cheap saturation; never "
        "approve an easy candidate or replace a frozen task in place. "
        "Source declarations, redistribution rights and catalog dedup are not required "
        "by this owner; never label them verified. Bind every non-discovery Seed origin "
        "revision into the request source (for example in upstream_projects or "
        "public_datasets) so candidate-register can verify lineage; never edit "
        "frozen bytes after measurement. Do not approve your own rubric, run a "
        "frontier rollout, or edit shared tasks. "
        "Return observed commands/results and specific review questions."
    )
    revision = item.get("revision_index", 0)
    if revision == 0:
        text = base + (" This is the initial measured identity (revision 0); "
                       "the same task family is scheduled for two separately "
                       "measured causal hardening identities before final review.")
        return text + (" The previous worker failed without a measured task; this is one "
                       "fresh isolated retry, not a completed first identity."
                       if item.get("author_attempt") == 2 else "")
    text = base + (
        f" This is revision {revision} of the same task family, not a new Seed "
        f"or a different deliverable. Parent task ID: {item['parent_task_id']}; "
        f"parent task SHA-256: {item['parent_task_sha256']}; read its immutable "
        f"workspace at {item['parent_task_workspace']} and diagnose the "
        f"{item['parent_status']} outcome. Preserve the original work objective, "
        "artifact contract and pinned source; introduce one source-grounded "
        "causal constraint, resource interaction or Work-availability bottleneck "
        "that addresses the measured failure. Keep the parent bytes untouched, "
        "use a new task ID and this new isolated workspace, and record "
        "all required fields from edgebench-task-generator/references/"
        "hardening-family-contract.md in authoring/hardening.json, including "
        "parent_task_sha256, revision_index and current measured difficulty evidence. "
        "Rerun NOP, lawful intermediate, strong/reference, generic controls and "
        "ablation probes. If there is no defensible harder mechanism, report "
        "blocked rather than merely adding records, changing metadata, moving "
        "the score upper or switching Seed. The main reviewer decides acceptance."
    )
    return text + (" The previous author failed without a measured candidate; "
                   "this is the sole isolated retry of the same revision, not "
                   "the next hardening round." if item.get("author_attempt") == 2 else "")


def _scout_prompt(source: dict) -> str:
    return (
        "You are the EdgeBench scout subagent. Research ONLY this official source: "
        f"{source['official_url']} (source_id={source['id']}, "
        f"category={source['category']}). Return ONLY one JSON source capture using "
        "edgebench-task-generator/examples/source-capture.json as the exact schema. "
        "Use unique lowercase-slug record IDs, observed official HTTPS URLs, and the "
        "current UTC timestamp. Set revision, artifact_sha256, offline_mirror, license "
        "to null unless verified; never invent them. Do not assert approval or rights. "
        "Find concrete task-bearing records, not generic navigation links. Do not "
        "write repository files, run evaluation, or access credentials."
    )


def _store_capture(root: Path, source: dict, capture: dict) -> dict:
    source_id = source["id"]
    topify_seed.validate_source_capture(capture, source)
    inbox = root / "seed-inbox"
    inbox.mkdir(exist_ok=True)
    digest = common.canonical_sha256(capture)
    path = inbox / f"{source_id}-{digest}.json"
    if path.exists() or path.is_symlink():
        return {"source_id": source_id, "status": "duplicate", "capture_sha256": digest}
    try:
        common.write_new_json(path, capture)
    except common.PipelineError:
        if path.is_file() and not path.is_symlink() and common.load_json(path) == capture:
            return {"source_id": source_id, "status": "duplicate", "capture_sha256": digest}
        raise
    return {
        "source_id": source_id,
        "status": "review-pending",
        "capture_path": str(path),
        "capture_sha256": digest,
        "record_count": len(capture["records"]),
    }


def scout_dispatch(workspace: str | Path, registry_path: str | Path,
                   source_ids: list[str]) -> tuple[dict, int]:
    """Emit scout prompts for native subagent dispatch (Task tool,
    subagent_type edgebench-scout); no subprocess is spawned. Record each
    returned capture with scout_collect.
    """
    root, _ = topify_seed.load_campaign(workspace)
    registry = topify_seed.validate_source_registry(common.load_json(registry_path))
    sources = {source["id"]: source for source in registry["sources"]}
    if (not source_ids or len(source_ids) != len(set(source_ids))
            or any(not SOURCE_ID.fullmatch(item) or item not in sources for item in source_ids)):
        raise common.PipelineError("select distinct registered source IDs")
    number, attempt = common.next_attempt(root / "daily-scout" / "dispatches")
    jobs = []
    for source_id in sorted(source_ids):
        from topify_production import _reserve_job_root

        job_id = f"scout-{source_id}-{number:04d}"
        job_root = _reserve_job_root(root, job_id)
        prompt_path = attempt / f"{source_id}.prompt.txt"
        prompt_path.write_text(_scout_prompt(sources[source_id]) + (
            "\n\nDispatch note: you are running as a native subagent of the main "
            f"reviewer, not via `opencode run`. Your exclusive job root is {job_root}; "
            "do not write shared campaign files. Return only the JSON source capture "
            "as your final text."
        ), encoding="utf-8")
        jobs.append({
            "source_id": source_id, "job_id": job_id, "job_root": str(job_root),
            "subagent": "edgebench-scout",
            "prompt_path": str(prompt_path),
            "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        })
    report = {
        "schema_version": 1, "attempt": number, "status": "awaiting-subagents",
        "dispatch_mode": "native-subagent", "jobs": jobs,
        "instruction": "Dispatch each prompt as an edgebench-scout subagent; save each "
                       "final JSON capture and run scout-collect with its path.",
        "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    return report, 0


def scout_collect(workspace: str | Path, registry_path: str | Path,
                  source_id: str, capture_file: str | Path) -> tuple[dict, int]:
    """Validate one scout subagent's returned capture and store it in seed-inbox."""
    root, _ = topify_seed.load_campaign(workspace)
    registry = topify_seed.validate_source_registry(common.load_json(registry_path))
    sources = {source["id"]: source for source in registry["sources"]}
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id) or source_id not in sources:
        raise common.PipelineError("unknown registered source ID")
    path = Path(capture_file)
    if not path.is_file() or path.is_symlink():
        raise common.PipelineError("capture file must be a regular file")
    try:
        capture = _capture_from_opencode(path.read_text(encoding="utf-8"))
        result = _store_capture(root, sources[source_id], capture)
    except (ValueError, TypeError, common.PipelineError, UnicodeDecodeError) as exc:
        return {"source_id": source_id, "status": "failed",
                "reason": "scout returned invalid source capture"}, 2
    return result, 0 if result["status"] != "failed" else 2


def _scout_one(source: dict, root: Path, opencode: str, timeout: int) -> dict:
    from topify_production import _reserve_job_root

    source_id = source["id"]
    job_id = f"scout-{source_id}-{uuid.uuid4().hex[:16]}"
    job_root = _reserve_job_root(root, job_id)
    prompt = _scout_prompt(source) + f" Your exclusive working directory is {job_root}."
    argv = [opencode, "run", "--agent", "edgebench-scout", "--format", "json", prompt]
    try:
        process = subprocess.run(
            argv,
            cwd=job_root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"source_id": source_id, "status": "failed", "reason": "scout unavailable or timed out"}
    if process.returncode != 0:
        return {"source_id": source_id, "status": "failed", "reason": "scout exited nonzero"}
    try:
        capture = _capture_from_opencode(process.stdout)
        return _store_capture(root, source, capture)
    except (ValueError, TypeError, common.PipelineError):
        return {"source_id": source_id, "status": "failed", "reason": "scout returned invalid source capture"}


def tick(
    workspace: str | Path,
    registry_path: str | Path,
    source_ids: list[str],
    max_workers: int = 6,
    opencode: str = "opencode",
    timeout_seconds: int = 900,
    notify_main: bool = False,
) -> tuple[dict, int]:
    root, _ = topify_seed.load_campaign(workspace)
    registry = topify_seed.validate_source_registry(common.load_json(registry_path))
    if type(max_workers) is not int or not 1 <= max_workers <= 6:
        raise common.PipelineError("scout concurrency must be between 1 and 6")
    if type(timeout_seconds) is not int or timeout_seconds < 30:
        raise common.PipelineError("scout timeout must be at least 30 seconds")
    sources = {source["id"]: source for source in registry["sources"]}
    if (not source_ids or len(source_ids) != len(set(source_ids))
            or any(not SOURCE_ID.fullmatch(item) or item not in sources for item in source_ids)):
        raise common.PipelineError("select distinct registered source IDs")
    with (root / ".seed-tick.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise common.PipelineError("another scheduled seed-tick is still running") from exc
        with ThreadPoolExecutor(max_workers=min(max_workers, len(source_ids))) as pool:
            pending = {
                pool.submit(_scout_one, sources[source_id], root, opencode, timeout_seconds): source_id
                for source_id in source_ids
            }
            results = [job.result() for job in as_completed(pending)]
        results.sort(key=lambda item: item["source_id"])
        attempt_number, attempt = common.next_attempt(root / "daily-scout")
        report = {
            "schema_version": 1,
            "attempt": attempt_number,
            "status": "complete" if all(item["status"] != "failed" for item in results) else "partial",
            "scout_concurrency": max_workers,
            "results": results,
            "recorded_at": common.utc_now(),
        }
        common.write_new_json(attempt / "summary.json", report)
        common.write_latest_json(attempt.parent / "latest.json", report)
        new_leads = [item["capture_path"] for item in results if item["status"] == "review-pending"]
        if notify_main and new_leads:
            prompt = (
                "You are edgebench-main. Review the new untrusted source captures "
                f"listed in {attempt / 'summary.json'}. Inspect URLs, novelty, rights, "
                "causal work potential and likely generic-baseline saturation. For captures "
                "you accept as leads, call scout-source with the matching registered source ID "
                "and absolute capture path. Reject poor leads explicitly; never confirm "
                "license, immutable revision, dedup or task quality based only on a model "
                "suggestion. Return accepted/rejected IDs, observed reasons and next actions."
                " For accepted but unpinned leads, assign @edgebench-seed-verifier "
                "to run mirror-source and draft a source-grounded Seed hypothesis "
                "before provisional screening; do not fabricate artifact bytes."
            )
            try:
                proc = subprocess.run(
                    [opencode, "run", "--agent", "edgebench-main", "--format", "json", prompt],
                    cwd=Path(__file__).resolve().parents[2], env=os.environ.copy(),
                    capture_output=True, timeout=1200, check=False,
                )
                text = _final_text(proc.stdout.decode("utf-8", errors="replace"))
                main_receipt = {
                    "status": "reported" if proc.returncode == 0 and text else "failed",
                    "returncode": proc.returncode,
                    "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
                    "feedback": _redact_text(text)[:16000] if text else None,
                }
            except (OSError, subprocess.TimeoutExpired):
                main_receipt = {"status": "failed", "feedback": None}
            common.write_new_json(attempt / "main-review.json", main_receipt)
            report["main_review_status"] = main_receipt["status"]
            report["main_review_path"] = str(attempt / "main-review.json")
            if main_receipt["status"] != "reported":
                return report, 2
        return report, 0 if report["status"] == "complete" else 2


def _validate_author_jobs(root: Path, playbook: dict, manifest_path: str | Path) -> list[dict]:
    manifest = common.load_json(manifest_path)
    if set(manifest) != {"schema_version", "jobs"} or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise common.PipelineError("author batch requires schema_version and jobs")
    jobs = manifest["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise common.PipelineError("author batch needs at least one job")
    seen_ids, seen_workspaces = set(), set()
    basic = {"seed_id", "workspace", "job_id"}
    initial = basic | {"revision_index", "parent_task_sha256"}
    revised = initial | {"parent_task_id", "parent_task_workspace", "parent_status"}
    for item in jobs:
        if not isinstance(item, dict):
            raise common.PipelineError("each author job needs a bound Seed and workspace")
        fields = set(item) - {"author_attempt"}
        if fields not in (basic, initial, revised):
            raise common.PipelineError("each author job needs a bound Seed, workspace and optional revision parent")
        revision = item.get("revision_index", 0)
        if (type(revision) is not int or revision not in (0, 1, 2)
                or revision == 0 and fields == revised
                or revision and fields != revised):
            raise common.PipelineError("author revision needs a complete parent-bound manifest")
        if "author_attempt" in item and (item["author_attempt"] != 2
                                         or not item["job_id"].endswith("-retry-2")):
            raise common.PipelineError("author retry needs a single second-attempt identity")
        if revision == 0:
            if "parent_task_sha256" in item and item["parent_task_sha256"] is not None:
                raise common.PipelineError("initial author job cannot have a parent task")
        elif (not isinstance(item["parent_task_sha256"], str)
              or not common.HEX64.fullmatch(item["parent_task_sha256"])
              or not isinstance(item["parent_task_id"], str)
              or not isinstance(item["parent_task_workspace"], str)
              or item["parent_status"] not in {"revise", "review-needed"}):
            raise common.PipelineError("author revision parent identity malformed")
        if not isinstance(item["job_id"], str) or not SOURCE_ID.fullmatch(item["job_id"]):
            raise common.PipelineError("author job_id must be a slug")
        seed_id = item["seed_id"]
        safe_name = topify_seed._safe_name(seed_id)
        if (root / "seed-family-decisions" / safe_name).exists():
            raise common.PipelineError("author job refused: main reviewer shelved this Seed")
        screened = root / "screened" / safe_name
        provisional = root / "provisionals" / safe_name
        if not screened.is_file() and not provisional.is_file():
            raise common.PipelineError("author job needs a screened or provisional Seed Card")
        if revision:
            candidates = sorted((root / "candidates" / safe_name.removesuffix(".json")).glob("attempt-*/report.json"))
            if not candidates:
                raise common.PipelineError("author revision has no registered parent candidate")
            parent = common.load_json(candidates[-1])
            for field, expected in (("seed_id", seed_id),
                                    ("task_sha256", item["parent_task_sha256"]),
                                    ("task_id", item["parent_task_id"]),
                                    ("task_workspace", item["parent_task_workspace"]),
                                    ("status", item["parent_status"])):
                if parent.get(field) != expected:
                    raise common.PipelineError(f"author revision parent {field} changed")
            if parent.get("revision_index", 0) != revision - 1:
                raise common.PipelineError("author revision parent sequence changed")
        seed_path = screened if screened.is_file() else provisional
        card = common.load_json(seed_path)
        category = seed_id.split("/", 1)[0]
        if category not in playbook["domains"] or card.get("category", category) != category:
            raise common.PipelineError("author Seed Card does not match its EdgeBench domain")
        workspace = Path(item["workspace"])
        expected = root / "agent-workspaces" / item["job_id"] / "task"
        if (not workspace.is_absolute() or workspace != expected
                or workspace.exists() or workspace.is_symlink()
                or not workspace.parent.is_dir() or workspace.parent.is_symlink()):
            raise common.PipelineError("author job needs its own unused, job-bound task workspace")
        if (item["job_id"] in seen_ids or seed_id in seen_ids
                or str(workspace) in seen_workspaces):
            raise common.PipelineError("author jobs must have distinct IDs, seeds, and workspaces")
        seen_ids.add(item["job_id"])
        seen_ids.add(seed_id)
        seen_workspaces.add(str(workspace))
    claims = root / "author-jobs"
    claims.mkdir(exist_ok=True)
    for item in jobs:
        claim = claims / f"{item['job_id']}.json"
        if claim.exists() or claim.is_symlink():
            raise common.PipelineError("author job ID was already dispatched")
        if item.get("author_attempt") == 2:
            first_id = item["job_id"].removesuffix("-retry-2")
            prior = claims / f"{first_id}.json"
            if not prior.is_file() or prior.is_symlink():
                raise common.PipelineError("author retry needs its original claim")
            observed = common.load_json(prior)
            if (observed.get("seed_id") != item["seed_id"]
                    or observed.get("revision_index", 0) != item.get("revision_index", 0)
                    or observed.get("parent_task_sha256") != item.get("parent_task_sha256")
                    or not (observed.get("status") == "failed"
                            or observed.get("status") == "collected"
                            and observed.get("result_status") in {"failed", "no-candidate"})):
                raise common.PipelineError("author retry is not bound to a failed, unmeasured parent job")
    return jobs


def _claim_author_jobs(root: Path, jobs: list[dict], status: str,
                       prompt_hashes: dict[str, str] | None = None) -> None:
    for item in jobs:
        common.write_new_json(root / "author-jobs" / f"{item['job_id']}.json", {
            **item, "status": status, "dispatched_at": common.utc_now(),
            **({"prompt_path": str(root / "author-jobs" / f"{item['job_id']}.prompt.txt"),
                "prompt_sha256": prompt_hashes[item["job_id"]],
                "job_root": str(Path(item["workspace"]).parent)} if prompt_hashes is not None else {}),
        })


def _finalize_author(root: Path, item: dict, returncode: int,
                     stdout_bytes: bytes, text: str) -> dict:
    """Record one author's feedback and grade its workspace evidence."""
    response_dir = root / "author-responses"
    response_dir.mkdir(exist_ok=True)
    response_path = response_dir / f"{item['job_id']}.json"
    common.write_new_json(response_path, {
        "schema_version": 1,
        "job_id": item["job_id"], "seed_id": item["seed_id"],
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "feedback": _redact_text(text)[:16000],
        "truncated": len(text) > 16000,
        "returncode": returncode,
        "recorded_at": common.utc_now(),
    })
    candidate = Path(item["workspace"])
    candidate_exists = candidate.is_dir() and (candidate / "workspace.json").is_file()
    measured = candidate_exists and (candidate / "task").is_dir() and any(
        (candidate / "evidence/measurements").glob("attempt-*/summary.json")
    )
    diagnostic = {}
    if measured:
        reports = sorted((candidate / "evidence/candidate-audits").glob("attempt-*/report.json"))
        if reports:
            try:
                diagnostic = common.load_json(reports[-1])
                import topify_validate

                if diagnostic.get("task_sha256") != topify_validate.task_sha256(candidate / "task"):
                    diagnostic = {}
            except (common.PipelineError, OSError):
                diagnostic = {}
    diagnostic_status = diagnostic.get("status")
    completed_status = (
        "review-pending" if diagnostic_status == "review-needed" else
        "revise" if diagnostic_status == "revise" else "controls-pending"
    )
    return {
        "job_id": item["job_id"], "seed_id": item["seed_id"],
        "workspace": item["workspace"],
        "status": completed_status if returncode == 0 and measured
                  else "incomplete-prototype" if returncode == 0 and candidate_exists
                  else "no-candidate" if returncode == 0 else "failed",
        "response_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "feedback_path": str(response_path),
        "reason": "review recorded generic-control diagnostic" if returncode == 0 and diagnostic_status in {"review-needed", "revise"}
                  else "run generic controls and candidate-audit" if returncode == 0 and measured
                  else "author prototype is incomplete" if returncode == 0 and candidate_exists
                  else "author returned no task workspace" if returncode == 0
                  else "author exited nonzero",
    }


def author_dispatch(campaign: str | Path, manifest_path: str | Path) -> tuple[dict, int]:
    """Claim author jobs and emit prompts for the main agent's native subagent
    dispatch (for example the Task tool with subagent_type edgebench-author).
    No subprocess is spawned; record each finished subagent with author_collect.
    """
    root, _ = topify_seed.load_campaign(campaign)
    playbook = topify_domain.load_playbook()
    jobs = _validate_author_jobs(root, playbook, manifest_path)
    dispatched = []
    prompts = {}
    for item in jobs:
        name = topify_seed._safe_name(item["seed_id"])
        seed_path = root / "screened" / name
        if not seed_path.is_file():
            seed_path = root / "provisionals" / name
        prompts[item["job_id"]] = author_prompt(item, seed_path, playbook) + (
            "\n\nDispatch note: you are running as a native subagent of the main "
            "reviewer, not via `opencode run`. Work only in your assigned "
            "workspace and return observed commands/results as final text."
        )
    hashes = {job_id: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
              for job_id, prompt in prompts.items()}
    try:
        _claim_author_jobs(root, jobs, "awaiting-subagent", hashes)
        for item in jobs:
            prompt_path = root / "author-jobs" / f"{item['job_id']}.prompt.txt"
            prompt_path.write_text(prompts[item["job_id"]], encoding="utf-8")
            dispatched.append({
                "job_id": item["job_id"], "seed_id": item["seed_id"],
                "workspace": item["workspace"], "job_root": str(Path(item["workspace"]).parent),
                "subagent": "edgebench-author",
                "prompt_path": str(prompt_path),
                "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
                "revision_index": item.get("revision_index", 0),
                "parent_task_sha256": item.get("parent_task_sha256"),
                "author_attempt": item.get("author_attempt", 1),
            })
    except (OSError, common.PipelineError) as exc:
        for item in jobs:
            claim = root / "author-jobs" / f"{item['job_id']}.json"
            if claim.is_file() and not claim.is_symlink():
                original = common.load_json(claim)
                common.write_latest_json(claim, {**original, "status": "failed",
                    "result_status": "failed", "collected_at": common.utc_now(),
                    "reason": "native author prompt unavailable"})
        raise common.PipelineError("native author prompt dispatch failed; claims are retryable") from exc
    return {
        "schema_version": 1, "status": "awaiting-subagents",
        "dispatch_mode": "native-subagent", "jobs": dispatched,
        "instruction": "Dispatch each prompt as a separate edgebench-author subagent; "
                       "then run author-collect for every job_id with its final feedback.",
        "recorded_at": common.utc_now(),
    }, 0


def author_collect(campaign: str | Path, job_id: str,
                   feedback_file: str | Path | None = None,
                   failed: bool = False) -> tuple[dict, int]:
    """Record one finished author subagent and grade its workspace evidence."""
    root, _ = topify_seed.load_campaign(campaign)
    if not isinstance(job_id, str) or not SOURCE_ID.fullmatch(job_id):
        raise common.PipelineError("job_id must be a slug")
    claim_path = root / "author-jobs" / f"{job_id}.json"
    if not claim_path.is_file() or claim_path.is_symlink():
        raise common.PipelineError("unknown author job claim")
    item = common.load_json(claim_path)
    expected_prompt = root / "author-jobs" / f"{job_id}.prompt.txt"
    expected_workspace = root / "agent-workspaces" / job_id / "task"
    if (item.get("workspace") != str(expected_workspace)
            or item.get("job_root") != str(expected_workspace.parent)
            or item.get("prompt_path") != str(expected_prompt)
            or not expected_workspace.parent.is_dir() or expected_workspace.parent.is_symlink()
            or not expected_prompt.is_file() or expected_prompt.is_symlink()
            or item.get("prompt_sha256") != common.sha256_file(expected_prompt)):
        raise common.PipelineError("author job prompt or isolated workspace changed after dispatch")
    response_path = root / "author-responses" / f"{job_id}.json"
    if response_path.exists() or response_path.is_symlink():
        raise common.PipelineError("author job already collected")
    if failed:
        text, raw, returncode = "", b"", 1
    else:
        if feedback_file is None:
            raise common.PipelineError("author-collect needs --feedback-file or --failed")
        feedback_path = Path(feedback_file)
        if not feedback_path.is_file() or feedback_path.is_symlink():
            raise common.PipelineError("feedback file must be a regular file")
        raw = feedback_path.read_bytes()
        text = raw.decode("utf-8", errors="replace").strip()
        returncode = 0
        if not text:
            returncode = 1
    result = _finalize_author(root, item, returncode, raw, text)
    common.write_latest_json(claim_path, {
        **item, "status": "collected" if returncode == 0 else "failed",
        "collected_at": common.utc_now(), "result_status": result["status"],
    })
    return result, 0 if result["status"] != "failed" else 2


def author_batch(
    campaign: str | Path, manifest_path: str | Path,
    max_workers: int = 4, opencode: str = "opencode", timeout_seconds: int = 14400,
) -> tuple[dict, int]:
    """Legacy CLI-subprocess dispatch. Prefer author_dispatch + author_collect
    with the main agent's native subagent mechanism when available."""
    root, _ = topify_seed.load_campaign(campaign)
    playbook = topify_domain.load_playbook()
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise common.PipelineError("author concurrency must be between 1 and 4")
    if type(timeout_seconds) is not int or timeout_seconds < 3600:
        raise common.PipelineError("author timeout must be at least one hour")
    jobs = _validate_author_jobs(root, playbook, manifest_path)
    _claim_author_jobs(root, jobs, "dispatched")

    def run(item: dict) -> dict:
        name = topify_seed._safe_name(item["seed_id"])
        seed_path = root / "screened" / name
        if not seed_path.is_file():
            seed_path = root / "provisionals" / name
        prompt = author_prompt(item, seed_path, playbook)
        try:
            proc = subprocess.run(
                [opencode, "run", "--agent", "edgebench-author", "--format", "json", prompt],
                cwd=Path(item["workspace"]).parent, env=os.environ.copy(),
                capture_output=True, timeout=timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"job_id": item["job_id"], "seed_id": item["seed_id"], "status": "failed", "reason": "author unavailable or timed out"}
        text = _final_text(proc.stdout.decode("utf-8", errors="replace"))
        if not text and proc.returncode == 0:
            return {"job_id": item["job_id"], "seed_id": item["seed_id"], "status": "failed", "reason": "author returned no final feedback"}
        return _finalize_author(root, item, proc.returncode, proc.stdout, text)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
        results = [future.result() for future in as_completed([pool.submit(run, job) for job in jobs])]
    results.sort(key=lambda item: item["job_id"])
    for result in results:
        claim = root / "author-jobs" / f"{result['job_id']}.json"
        assigned = common.load_json(claim)
        common.write_latest_json(claim, {
            **assigned, "status": "failed" if result["status"] == "failed" else "collected",
            "collected_at": common.utc_now(), "result_status": result["status"],
        })
    number, attempt = common.next_attempt(root / "author-batches")
    report = {
        "schema_version": 1, "attempt": number, "results": results,
        "status": "review-pending" if all(item["status"] == "review-pending" for item in results) else "partial",
        "max_workers": max_workers, "recorded_at": common.utc_now(),
    }
    common.write_new_json(attempt / "summary.json", report)
    return report, 0 if report["status"] == "review-pending" else 2


def rollout_batch(
    manifest_path: str | Path, max_workers: int = 3, sforge: str = "sforge",
) -> tuple[dict, int]:
    """Run independent frozen SForge jobs with a bounded shared resource limit."""
    import topify_rollout

    if type(max_workers) is not int or max_workers < 1:
        raise common.PipelineError("rollout concurrency must be a positive integer")
    manifest = common.load_json(manifest_path)
    if (set(manifest) != {"schema_version", "log_dir", "judge_url", "jobs"}
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1):
        raise common.PipelineError("rollout batch requires schema_version, log_dir, judge_url, jobs")
    jobs = manifest["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise common.PipelineError("rollout batch requires at least one job")
    seen = set()
    for item in jobs:
        if not isinstance(item, dict) or set(item) != {"workspace", "run_id"}:
            raise common.PipelineError("rollout job needs workspace and run_id")
        run_id = item["run_id"]
        if not isinstance(run_id, str) or not topify_rollout.RUN_ID.fullmatch(run_id) or run_id in seen:
            raise common.PipelineError("rollout run IDs must be distinct safe slugs")
        seen.add(run_id)
        readiness, code = topify_rollout.preflight(item["workspace"])
        if code != 0:
            raise common.PipelineError(f"rollout preflight is incomplete for run_id {run_id}")
        if (Path(manifest["log_dir"]) / "runs" / run_id).exists():
            raise common.PipelineError(f"rollout run ID already exists: {run_id}")

    def launch(item: dict) -> dict:
        try:
            observed, code = topify_rollout.start(
                item["workspace"], item["run_id"], manifest["log_dir"],
                manifest["judge_url"], sforge,
            )
            return {
                "workspace": item["workspace"], "run_id": item["run_id"],
                "status": "process-complete" if code == 0 else "failed",
                "task_sha256": observed["task_sha256"],
                "observation_path": str(Path(item["workspace"]) / "evidence/rollout-launches" / item["run_id"] / "process.json"),
            }
        except (common.PipelineError, OSError, ValueError) as exc:
            return {"workspace": item["workspace"], "run_id": item["run_id"], "status": "failed", "reason": str(exc)}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
        results = [future.result() for future in as_completed([pool.submit(launch, item) for item in jobs])]
    results.sort(key=lambda item: item["run_id"])
    return {
        "status": "process-complete" if all(item["status"] == "process-complete" for item in results) else "partial",
        "max_workers": max_workers, "results": results,
        "note": "Use rollout-collect to verify full-budget runs; launch completion does not imply acceptance.",
    }, 0 if all(item["status"] == "process-complete" for item in results) else 2
