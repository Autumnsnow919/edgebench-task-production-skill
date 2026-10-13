#!/usr/bin/env python3
"""Produce EdgeBench deliveries in the user-selected SForge-only or Harbor format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

import topify_common as common
import topify_control as control
import topify_build
import topify_candidate_audit
import topify_completion
import topify_design
import topify_daily
import topify_delivery_contract
import topify_domain
import topify_feasibility
import topify_freeze
import topify_harbor
import topify_harbor_acceptance
import topify_harbor_delivery
import topify_image_measure
import topify_measure
import topify_monitor
import topify_production
import topify_process_qc
import topify_profile
import topify_rollout
import topify_rl_task_qc
import topify_review
import topify_quality_report
import topify_seed
import topify_source_mirror
import topify_sforge
import topify_sforge_delivery
import topify_sforge_acceptance
import topify_sforge_batch
import topify_validate
import topify_supervisor
import topify_employees
import topify_web


def initialize(workspace: Path, request_path: Path) -> dict:
    request = common.load_json(request_path)
    validated = common.validate_request(request)
    target = workspace.absolute()
    if target.exists() or target.is_symlink():
        raise common.PipelineError("workspace already exists; refusing to overwrite it")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise common.PipelineError("workspace parent must be an existing real directory")
    target.mkdir()
    try:
        (target / "evidence").mkdir()
        (target / "evidence/events.jsonl").touch(exist_ok=False)
        record = {
            "schema_version": 1,
            "docx_sha256": common.DOCX_SHA256,
            "task_id": request["task_id"],
            "request": request,
            "request_sha256": common.canonical_sha256(request),
            "budget_assessment": validated["budget_assessment"],
            "created_at": common.utc_now(),
        }
        common.write_new_json(target / "workspace.json", record)
        common.append_event(target, "initialized", request_sha256=record["request_sha256"])
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"state": "INIT", "workspace": str(target), "budget_assessment": validated["budget_assessment"]}


def screen_design(workspace: Path, design_path: Path) -> tuple[dict, int]:
    root, metadata = common.load_workspace(workspace)
    design = common.load_json(design_path)
    profile_receipt = topify_profile.current(root)
    profile = (topify_profile.load_profile(profile_receipt["profile_id"], profile_receipt["profile_path"])
               if profile_receipt else topify_profile.load_profile("edgebench-v1"))
    topify_profile.assert_supported(profile)
    report = topify_design.evaluate(design, metadata["request"], profile=profile)
    if report["verdict"] != "complete":
        return {**report, "state": "INIT"}, 2
    stored = {
        **design,
        "request_sha256": metadata["request_sha256"],
        "design_sha256": common.canonical_sha256(design),
    }
    common.write_new_json(root / "task_design.json", stored)
    common.append_event(root, "design_screened", design_sha256=stored["design_sha256"])
    return {**report, "state": "DESIGN_SCREENED", "design_sha256": stored["design_sha256"]}, 0


def status(workspace: Path) -> dict:
    root, _ = common.load_workspace(workspace)
    if not (root / "task_design.json").is_file():
        return {
            "state": "INIT",
            "next_command": f"design {root} --input TASK_DESIGN.json",
        }
    if not (root / "task").is_dir():
        return {
            "state": "DESIGN_SCREENED",
            "next_command": f"generate {root}",
        }
    validation_path = root / "evidence/harbor_validation.json"
    if validation_path.is_file() and not validation_path.is_symlink():
        validation = common.load_json(validation_path)
        if (validation.get("verdict") == "complete"
                and validation.get("task_sha256") == topify_validate.task_sha256(root / "task")):
            task_sha256 = validation["task_sha256"]
            rollout = topify_completion._latest(root / "evidence/rollout-collections")
            if rollout.get("status") == "complete" and rollout.get("task_sha256") == task_sha256:
                return {"state": "ROLLOUTS_COLLECTED", "next_command": f"sforge-audit {root}"}
            adapter = topify_completion._latest(root / "evidence/sforge-adapters")
            if adapter.get("status") == "complete" and adapter.get("task_sha256") == task_sha256:
                return {"state": "SFORGE_READY", "next_command": f"rollout-check {root}"}
            native = {}
            for architecture in sorted(common.ARCHITECTURES):
                value = topify_completion._latest(
                    root / "evidence/image-measurements" / architecture.replace("/", "-")
                )
                native[architecture] = (
                    value.get("status") == "complete"
                    and value.get("task_sha256") == task_sha256
                    and value.get("observed_host_architecture") == architecture
                )
            if all(native.values()):
                return {"state": "CROSS_ARCH_MEASURED", "next_command": f"sforge-prepare {root}"}
            measured_architectures = [architecture for architecture, complete in native.items() if complete]
            if measured_architectures:
                missing = next(architecture for architecture, complete in native.items() if not complete)
                return {
                    "state": "IMAGE_MEASURED_PARTIAL",
                    "measured_architectures": measured_architectures,
                    "next_command": f"measure-image {root} --architecture {missing}",
                }
            build = topify_completion._latest(root / "evidence/builds")
            if build.get("status") == "complete" and build.get("task_sha256") == task_sha256:
                return {"state": "BUILT", "next_command": f"measure-image {root}"}
            measurement = topify_completion._latest(root / "evidence/measurements")
            if measurement.get("task_sha256") == task_sha256:
                return {"state": "MEASUREMENT_PARTIAL", "next_command": f"build {root}"}
            return {"state": "HARBOR_VALIDATED", "next_command": f"measure {root}"}
    return {"state": "GENERATED", "next_command": f"validate {root}"}


def production_status(workspace: Path) -> dict:
    root, metadata = common.load_workspace(workspace)
    internal = status(root)
    if not (root / "task").is_dir():
        return {
            "format": "SForge", "rollout_harness": "SForge", "task_id": metadata["task_id"],
            "state": "incomplete", "internal": internal,
            "next_action": internal["next_command"],
        }
    task_hash = topify_validate.task_sha256(root / "task")
    selected_profile = topify_profile.current(root, task_hash)
    if metadata["request"].get("plugin") != "mini_continuous" and not selected_profile:
        return {
            "format": "unselected", "delivery_format": None, "rollout_harness": "SForge",
            "task_id": metadata["task_id"], "task_sha256": task_hash, "state": "incomplete",
            "checks": {"benchmark_profile_selected": False},
            "next_action": f"benchmark-profile {root} --profile edgebench-v1",
            "internal": internal,
        }
    selected = topify_delivery_contract.current(root, task_hash)
    if metadata["request"].get("plugin") != "mini_continuous" and not selected:
        return {
            "format": "unselected", "rollout_harness": "SForge", "task_id": metadata["task_id"],
            "task_sha256": task_hash, "state": "incomplete",
            "checks": {"delivery_format_selected": False},
            "next_action": f"delivery-format {root} --format sforge-only|harbor",
            "internal": internal,
        }
    selected_format = selected.get("format", "sforge-only")
    profile = (topify_profile.load_profile(selected_profile["profile_id"], selected_profile["profile_path"])
               if selected_profile else topify_profile.load_profile("edgebench-v1"))
    required_trials = topify_profile.threshold(profile, "frontier_trials_minimum", 3)
    adapter = topify_completion._latest(root / "evidence/sforge-adapters")
    adapter_ok = adapter.get("status") == "complete" and adapter.get("task_sha256") == task_hash
    review_ok = topify_review.review_is_current(root, task_hash)
    rollout = topify_completion._latest(root / "evidence/rollout-collections")
    rollout_ok = (
        rollout.get("status") == "complete" and rollout.get("task_sha256") == task_hash
        and rollout.get("completed_run_count", 0) >= required_trials
    )
    audit = (topify_sforge_acceptance.audit_task(root)
             if selected_format == "sforge-only" else topify_harbor_acceptance.audit_task(root))
    if not adapter_ok:
        action = f"sforge-prepare {root}"
    elif not review_ok:
        action = f"task-review {root} --input MAIN_AGENT_REVIEW.json"
    elif not rollout_ok:
        action = f"rollout-check {root}; rollout-start or rollout-batch; rollout-collect"
    elif audit["status"] != "complete":
        action = f"sforge-audit {root}"
    else:
        action = (
            'package-selected --manifest BATCH_DELIVERY.json --output '
            '"CUSTOMER_OUTPUT_DIR/DELIVERY" --review-dir PRIVATE_REVIEW_DIR'
        )
    return {
        "format": "SForge" if selected_format == "sforge-only" else "Harbor",
        "delivery_format": selected_format, "rollout_harness": "SForge", "task_id": metadata["task_id"],
        "task_sha256": task_hash,
        "state": "ready-to-package" if rollout_ok and review_ok and adapter_ok and audit["status"] == "complete" else "incomplete",
        "checks": {
            "sforge_adapter_parity": adapter_ok,
            "delivery_format_selected": bool(selected),
            "main_agent_rubric": review_ok,
            "frontier_trials": rollout_ok,
            "three_full_rollouts": rollout_ok,
            "non_format_docx_evidence": audit["status"] == "complete",
        },
        "missing_non_format_evidence": audit["missing"],
        "completed_run_count": rollout.get("completed_run_count", 0),
        "internal": internal, "next_action": action,
    }


def doctor() -> dict:
    root = Path(__file__).resolve().parents[2]
    return {
        "docx_sha256": common.DOCX_SHA256,
        "docker_present": bool(shutil.which("docker")),
        "harbor_present": bool(shutil.which("harbor")),
        "sforge_present": (root / "authoring/.venv/bin/sforge").is_file(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    campaign = commands.add_parser("campaign-init")
    campaign.add_argument("--workspace", required=True, type=Path)
    campaign.add_argument("--config", required=True, type=Path)
    campaign_status = commands.add_parser("campaign-status")
    campaign_status.add_argument("workspace", type=Path)
    supervisor_init = commands.add_parser(
        "supervisor-init", help="initialize the OpenCode/Orca-neutral supervisor ledger"
    )
    supervisor_init.add_argument("workspace", type=Path)
    supervisor_init.add_argument("--mode", choices=("dual", "opencode-task", "orca-cli"), default="dual")
    supervisor_init.add_argument("--max-concurrent", type=int)
    supervisor_init.add_argument("--dedup-root", type=Path)
    supervisor_init.add_argument("--supervisor-id")
    supervisor_init.add_argument("--benchmark")
    supervisor_bind = commands.add_parser("supervisor-session-bind", help="bind the real supervisor OpenCode session")
    supervisor_bind.add_argument("workspace", type=Path)
    supervisor_bind.add_argument("--session-id", required=True)
    employee_hire = commands.add_parser("employee-hire", help="register one one-task production employee")
    employee_hire.add_argument("workspace", type=Path)
    employee_hire.add_argument("--employee-id", required=True)
    employee_hire.add_argument("--role", choices=sorted(topify_supervisor.ROLES), required=True)
    employee_hire.add_argument("--transport", choices=sorted(topify_supervisor.TRANSPORTS), required=True)
    employee_hire.add_argument("--task-id")
    employee_hire.add_argument("--seed-id")
    employee_hire.add_argument("--parent-id")
    employee_hire.add_argument("--prompt-file", type=Path)
    employee_hire.add_argument("--replacement-of")
    employee_hire.add_argument("--fingerprint-file", type=Path)
    employee_bind = commands.add_parser("employee-session-bind", help="bind an employee to its actual persistent OpenCode session")
    employee_bind.add_argument("workspace", type=Path)
    employee_bind.add_argument("--employee-id", required=True)
    employee_bind.add_argument("--session-id", required=True)
    employee_bind.add_argument("--dispatch-id")
    seed_bind = commands.add_parser("producer-seed-bind", help="approve the producer-owned screened Seed and reserve its fingerprint")
    seed_bind.add_argument("workspace", type=Path)
    seed_bind.add_argument("--employee-id", required=True)
    seed_bind.add_argument("--seed-id", required=True)
    seed_bind.add_argument("--screen-receipt", required=True, type=Path)
    employee_status = commands.add_parser("employee-status", help="show supervisor employees and alerts")
    employee_status.add_argument("workspace", type=Path)
    supervisor_dashboard = commands.add_parser("supervisor-dashboard", help="show chat-friendly worker progress and alerts")
    supervisor_dashboard.add_argument("workspace", type=Path)
    supervisor_dashboard.add_argument("--employee-id")
    supervisor_watch = commands.add_parser("supervisor-watch", help="detect stale worker heartbeats and notify the bound supervisor")
    supervisor_watch.add_argument("workspace", type=Path)
    supervisor_watch.add_argument("--stale-after-seconds", type=int, default=topify_supervisor.HEARTBEAT_STALE_AFTER_SECONDS)
    owner_request = commands.add_parser("owner-request", help="pause worker communication and submit a candidate disposition proposal to the owner")
    owner_request.add_argument("workspace", type=Path)
    owner_request.add_argument("--employee-id", required=True)
    owner_request.add_argument("--action", choices=sorted(control.ACTION_LABELS), required=True)
    owner_request.add_argument("--report", type=Path, required=True)
    owner_request.add_argument("--reason", required=True)
    owner_sync = commands.add_parser("owner-request-sync", help="verify a completed native question and record the owner's disposition")
    owner_sync.add_argument("workspace", type=Path)
    owner_sync.add_argument("--request-id", required=True)
    reseed_chat = commands.add_parser("owner-reseed-chat", help="verify an explicit original owner reseed instruction without re-asking it")
    reseed_chat.add_argument("workspace", type=Path)
    reseed_chat.add_argument("--employee-id", required=True)
    reseed_chat.add_argument("--report", type=Path, required=True)
    reseed_chat.add_argument("--session-id", required=True)
    reseed_chat.add_argument("--message-id", required=True)
    disposition = commands.add_parser("candidate-disposition", help="apply an owner-approved candidate closure or standby")
    disposition.add_argument("workspace", type=Path)
    disposition.add_argument("--employee-id", required=True)
    disposition.add_argument("--action", choices=("close_candidate", "standby"), required=True)
    supervisor_web = commands.add_parser("supervisor-web", help="serve the authenticated supervisor web console")
    supervisor_web.add_argument("workspace", type=Path, nargs="?")
    supervisor_web.add_argument("--host", default="0.0.0.0")
    supervisor_web.add_argument("--port", type=int, default=8787)
    supervisor_web.add_argument("--owner-token")
    supervisor_web.add_argument("--supervisor-token")
    supervisor_web.add_argument("--owner-token-file", type=Path)
    supervisor_web.add_argument("--supervisor-token-file", type=Path)
    supervisor_web.add_argument("--registry-file", type=Path)
    supervisor_web.add_argument("--scan-root", action="append", type=Path)
    supervisor_web.add_argument("--adopt-legacy", action="store_true")
    supervisor_web.add_argument("--dedup-root", type=Path)
    supervisor_web.add_argument("--legacy-benchmark", default="Unclassified")
    supervisor_web.add_argument("--no-auth", action="store_true")
    employee_fire = commands.add_parser("employee-fire", help="record a controlled employee termination")
    employee_fire.add_argument("workspace", type=Path)
    employee_fire.add_argument("--employee-id", required=True)
    employee_fire.add_argument("--reason", required=True)
    employee_fire.add_argument("--evidence", type=Path)
    child_request = commands.add_parser("child-request", help="stage a child Task request for supervisor dispatch")
    child_request.add_argument("workspace", type=Path)
    child_request.add_argument("--parent-id", required=True)
    child_request.add_argument("--child-id", required=True)
    child_request.add_argument("--role", choices=sorted(topify_supervisor.ROLES), required=True)
    child_request.add_argument("--transport", choices=sorted(topify_supervisor.TRANSPORTS), required=True)
    child_request.add_argument("--objective", required=True)
    child_request.add_argument("--task-id")
    child_request.add_argument("--seed-id")
    child_request.add_argument("--prompt-file", type=Path)
    supervisor_dispatch = commands.add_parser("supervisor-dispatch", help="emit one signed transport-neutral worker dispatch")
    supervisor_dispatch.add_argument("workspace", type=Path)
    supervisor_dispatch.add_argument("--employee-id", required=True)
    supervisor_dispatch.add_argument("--output", type=Path)
    for name in ("producer-plan", "producer-dispatch"):
        command = commands.add_parser(name, help="plan/prepare producer-first full-task employees")
        command.add_argument("workspace", type=Path)
        command.add_argument("--count", type=int)
        command.add_argument("--transport", choices=sorted(topify_supervisor.TRANSPORTS), default="opencode-task")
    employee_report = commands.add_parser("employee-report", help="record a worker heartbeat or completion report")
    employee_report.add_argument("workspace", type=Path)
    employee_report.add_argument("--employee-id", required=True)
    employee_report.add_argument("--input", required=True, type=Path)
    employee_message = commands.add_parser("employee-message", help="send a worker reply or question through the message ledger")
    employee_message.add_argument("workspace", type=Path)
    employee_message.add_argument("--employee-id", required=True)
    employee_message.add_argument("--target-type", choices=("supervisor", "employee"), required=True)
    employee_message.add_argument("--target-id", required=True)
    employee_message.add_argument("--text", required=True)
    employee_message.add_argument("--reply-to")
    message_list = commands.add_parser("message-list", help="read the conversation for an employee or supervisor")
    message_list.add_argument("workspace", type=Path)
    message_list.add_argument("--target-type", choices=("supervisor", "employee"), required=True)
    message_list.add_argument("--target-id", required=True)
    message_list.add_argument("--limit", type=int, default=100)
    hardening = commands.add_parser("hardening-challenge", help="record the mandatory supervisor difficulty challenge")
    hardening.add_argument("workspace", type=Path)
    hardening.add_argument("--producer-id", required=True)
    hardening.add_argument("--round", required=True, type=int, choices=(1, 2))
    hardening.add_argument("--note", required=True)
    hardening.add_argument("--evidence", type=Path)
    dedup_claim = commands.add_parser("dedup-claim", help="reserve a Seed fingerprint before authoring")
    dedup_claim.add_argument("workspace", type=Path)
    dedup_claim.add_argument("--employee-id", required=True)
    dedup_claim.add_argument("--fingerprint-file", required=True, type=Path)
    dedup_task = commands.add_parser("dedup-register-task", help="register a frozen task hash globally")
    dedup_task.add_argument("workspace", type=Path)
    dedup_task.add_argument("--employee-id", required=True)
    dedup_task.add_argument("--task-sha256", required=True)
    qc_decision = commands.add_parser("qc-decision", help="bind an independent QC decision to a producer")
    qc_decision.add_argument("workspace", type=Path)
    qc_decision.add_argument("--producer-id", required=True)
    qc_decision.add_argument("--qc-id", required=True)
    qc_decision.add_argument("--round", required=True, type=int)
    qc_decision.add_argument("--decision", choices=sorted(topify_supervisor.QC_STATES), required=True)
    qc_decision.add_argument("--severity", choices=sorted(topify_supervisor.QC_SEVERITIES), required=True)
    qc_decision.add_argument("--report", required=True, type=Path)
    qc_decision.add_argument("--note", required=True)
    qc_resolve = commands.add_parser("qc-resolve", help="record the owner's scoped exception, further repair or termination decision")
    qc_resolve.add_argument("workspace", type=Path)
    qc_resolve.add_argument("--producer-id", required=True)
    qc_resolve.add_argument("--decision", choices=sorted(topify_supervisor.QC_DECISIONS), required=True)
    qc_resolve.add_argument("--note", default="")
    qc_resolve.add_argument("--evidence", type=Path)
    qc_answer = commands.add_parser("qc-owner-answer", help="record a verified completed native question answer as QC owner evidence")
    qc_answer.add_argument("workspace", type=Path)
    qc_answer.add_argument("--producer-id", required=True)
    qc_answer.add_argument("--decision", choices=sorted(topify_supervisor.QC_OWNER_CHOICES), required=True)
    owner_decision = commands.add_parser("owner-decision", help="approve or reject a production gate")
    owner_decision.add_argument("workspace", type=Path)
    owner_decision.add_argument("--employee-id", required=True)
    owner_decision.add_argument("--decision", choices=("approve_rollout", "reject_rollout", "accept_exception", "fire"), required=True)
    owner_decision.add_argument("--note", required=True)
    owner_decision.add_argument("--evidence", type=Path)
    owner_form_create = commands.add_parser("owner-form-create", help="request a real supervisor review (does not create a detached form)")
    owner_form_create.add_argument("workspace", type=Path)
    owner_form_create.add_argument("--employee-id", required=True)
    owner_form_create.add_argument("--note", default="")
    owner_form_sync = commands.add_parser("owner-form-sync", help="legacy detached forms are not valid production approvals")
    owner_form_sync.add_argument("workspace", type=Path)
    owner_form_sync.add_argument("--employee-id", required=True)
    rollout_gate = commands.add_parser("supervisor-rollout-gate", help="evaluate profile-bound early rollout policy")
    rollout_gate.add_argument("workspace", type=Path)
    rollout_gate.add_argument("--employee-id", required=True)
    rollout_gate.add_argument("--observations", required=True, type=Path)
    rollout_gate.add_argument("--profile-file", type=Path)
    rollout_gate.add_argument("--task-workspace", type=Path)
    rollout_gate.add_argument("--final", action="store_true")
    replacement = commands.add_parser("replacement-order", help="fire replacement order for a new Seed")
    replacement.add_argument("workspace", type=Path)
    replacement.add_argument("--former-employee-id", required=True)
    replacement.add_argument("--new-employee-id", required=True)
    replacement.add_argument("--new-seed-id", required=True)
    replacement.add_argument("--transport", choices=sorted(topify_supervisor.TRANSPORTS), required=True)
    replacement.add_argument("--prompt-file", type=Path)
    replacement.add_argument("--fingerprint-file", type=Path)
    domain_plan = commands.add_parser("domain-plan")
    domain_plan.add_argument("workspace", type=Path)
    domain_plan.add_argument("--playbook", type=Path, default=topify_domain.PLAYBOOK)
    domain_plan.add_argument("--limit", type=int, default=4)
    production_plan = commands.add_parser("production-plan")
    production_plan.add_argument("workspace", type=Path)
    production_plan.add_argument("--verifiers", type=int, default=6)
    production_plan.add_argument("--authors", type=int, default=4)
    production_plan.add_argument("--producers", type=int)
    production_plan.add_argument("--legacy-workers", action="store_true")
    production_dispatch = commands.add_parser("production-dispatch")
    production_dispatch.add_argument("workspace", type=Path)
    production_dispatch.add_argument("--verifiers", type=int, default=6)
    production_dispatch.add_argument("--authors", type=int, default=4)
    production_dispatch.add_argument("--producers", type=int)
    production_dispatch.add_argument("--legacy-workers", action="store_true")
    auxiliary_workspace = commands.add_parser("agent-workspace-reserve")
    auxiliary_workspace.add_argument("workspace", type=Path)
    auxiliary_workspace.add_argument("--job-id", required=True)
    auxiliary_workspace.add_argument("--role", choices=("scout", "feasibility", "reviewer"), required=True)
    production_tick = commands.add_parser("production-tick")
    production_tick.add_argument("workspace", type=Path)
    production_tick.add_argument("--verifiers", type=int, default=6)
    production_tick.add_argument("--authors", type=int, default=4)
    production_tick.add_argument("--opencode", default="opencode")
    production_tick.add_argument("--timeout", type=int, default=900)
    production_tick.add_argument("--execute", action="store_true")
    production_tick.add_argument("--notify-main", action="store_true")
    production_tick.add_argument("--legacy-workers", action="store_true")
    feasibility_plan = commands.add_parser("feasibility-plan")
    feasibility_plan.add_argument("workspace", type=Path)
    feasibility_plan.add_argument("--seed-id", required=True, action="append")
    feasibility_plan.add_argument("--workers", type=int, default=2)
    feasibility_tick = commands.add_parser("feasibility-tick")
    feasibility_tick.add_argument("workspace", type=Path)
    feasibility_tick.add_argument("--seed-id", required=True, action="append")
    feasibility_tick.add_argument("--workers", type=int, default=2)
    feasibility_tick.add_argument("--timeout", type=int, default=1800)
    feasibility_tick.add_argument("--opencode", default="opencode")
    feasibility_tick.add_argument("--execute", action="store_true")
    feasibility_review = commands.add_parser("feasibility-review")
    feasibility_review.add_argument("workspace", type=Path)
    feasibility_review.add_argument("--seed-id", required=True)
    feasibility_review.add_argument("--proposal", default="proposal.json")
    micro_plan = commands.add_parser("micro-prototype-plan")
    micro_plan.add_argument("workspace", type=Path)
    micro_plan.add_argument("--seed-id", required=True, action="append")
    micro_plan.add_argument("--workers", type=int, default=2)
    micro_tick = commands.add_parser("micro-prototype-tick")
    micro_tick.add_argument("workspace", type=Path)
    micro_tick.add_argument("--seed-id", required=True, action="append")
    micro_tick.add_argument("--workers", type=int, default=2)
    micro_tick.add_argument("--timeout", type=int, default=1800)
    micro_tick.add_argument("--opencode", default="opencode")
    micro_tick.add_argument("--execute", action="store_true")
    micro_review = commands.add_parser("micro-prototype-review")
    micro_review.add_argument("workspace", type=Path)
    micro_review.add_argument("--seed-id", required=True)
    micro_review.add_argument("--proposal", default="proposal.json")
    scout = commands.add_parser("scout")
    scout.add_argument("workspace", type=Path)
    scout.add_argument("--catalog", required=True, type=Path)
    scout_source = commands.add_parser("scout-source")
    scout_source.add_argument("workspace", type=Path)
    scout_source.add_argument("--registry", required=True, type=Path)
    scout_source.add_argument("--source-id", required=True)
    scout_source.add_argument("--capture", required=True, type=Path)
    mirror_source = commands.add_parser("mirror-source")
    mirror_source.add_argument("workspace", type=Path)
    mirror_source.add_argument("--source-id", required=True)
    mirror_source.add_argument("--url", required=True)
    mirror_source.add_argument("--revision", required=True)
    mirror_source.add_argument("--expected-sha256")
    mirror_source.add_argument("--local-file", type=Path)
    mirror_source.add_argument("--max-bytes", type=int, default=50_000_000)
    daily = commands.add_parser("seed-tick")
    daily.add_argument("workspace", type=Path)
    daily.add_argument("--registry", required=True, type=Path)
    daily.add_argument("--source-id", required=True, action="append")
    daily.add_argument("--workers", type=int, default=6)
    daily.add_argument("--opencode", default="opencode")
    daily.add_argument("--timeout", type=int, default=900)
    daily.add_argument("--notify-main", action="store_true")
    authors = commands.add_parser("author-batch")
    authors.add_argument("campaign", type=Path)
    authors.add_argument("--manifest", required=True, type=Path)
    authors.add_argument("--workers", type=int, default=4)
    authors.add_argument("--opencode", default="opencode")
    authors.add_argument("--timeout", type=int, default=14400)
    author_dispatch = commands.add_parser("author-dispatch")
    author_dispatch.add_argument("campaign", type=Path)
    author_dispatch.add_argument("--manifest", required=True, type=Path)
    author_collect = commands.add_parser("author-collect")
    author_collect.add_argument("campaign", type=Path)
    author_collect.add_argument("--job-id", required=True)
    author_collect.add_argument("--feedback-file", type=Path)
    author_collect.add_argument("--failed", action="store_true")
    scout_dispatch = commands.add_parser("scout-dispatch")
    scout_dispatch.add_argument("workspace", type=Path)
    scout_dispatch.add_argument("--registry", required=True, type=Path)
    scout_dispatch.add_argument("--source-id", required=True, action="append")
    scout_collect = commands.add_parser("scout-collect")
    scout_collect.add_argument("workspace", type=Path)
    scout_collect.add_argument("--registry", required=True, type=Path)
    scout_collect.add_argument("--source-id", required=True)
    scout_collect.add_argument("--capture-file", required=True, type=Path)
    verifier_dispatch = commands.add_parser("verifier-dispatch")
    verifier_dispatch.add_argument("workspace", type=Path)
    verifier_dispatch.add_argument("--workers", type=int, default=6)
    verifier_collect = commands.add_parser("verifier-collect")
    verifier_collect.add_argument("workspace", type=Path)
    verifier_collect.add_argument("--job-id", required=True)
    verifier_collect.add_argument("--feedback-file", type=Path)
    verifier_collect.add_argument("--failed", action="store_true")
    seed_refine = commands.add_parser("seed-refine")
    seed_refine.add_argument("workspace", type=Path)
    seed_refine.add_argument("--seed-id", required=True)
    seed_refine.add_argument("--input", required=True, type=Path)
    seed_screen = commands.add_parser("seed-screen")
    seed_screen.add_argument("workspace", type=Path)
    seed_screen.add_argument("--seed-id", required=True)
    seed_screen.add_argument("--for-prototype", action="store_true")
    seed_screen.add_argument("--owner-sample", action="store_true")
    seed_prototype = commands.add_parser("seed-prototype")
    seed_prototype.add_argument("workspace", type=Path)
    seed_prototype.add_argument("--seed-id", required=True)
    seed_prototype.add_argument("--task-workspace", required=True, type=Path)
    candidate_register = commands.add_parser("candidate-register")
    candidate_register.add_argument("workspace", type=Path)
    candidate_register.add_argument("--seed-id", required=True)
    candidate_register.add_argument("--task-workspace", required=True, type=Path)
    seed_shelve = commands.add_parser("seed-shelve")
    seed_shelve.add_argument("workspace", type=Path)
    seed_shelve.add_argument("--seed-id", required=True)
    seed_shelve.add_argument("--reviewer-id", required=True)
    seed_shelve.add_argument("--reason", required=True)
    seed_promote = commands.add_parser("seed-promote")
    seed_promote.add_argument("workspace", type=Path)
    seed_promote.add_argument("--seed-id", required=True)
    batch_nop = commands.add_parser("batch-nop")
    batch_nop.add_argument("workspace", type=Path)
    sforge_batch_nop = commands.add_parser("sforge-batch-nop")
    sforge_batch_nop.add_argument("--manifest", required=True, type=Path)
    sforge_batch_nop.add_argument("--output-dir", required=True, type=Path)
    sforge_batch_nop.add_argument("--sforge", default="sforge")
    init = commands.add_parser("init")
    init.add_argument("--workspace", required=True, type=Path)
    init.add_argument("--request", required=True, type=Path)
    design = commands.add_parser("design")
    design.add_argument("workspace", type=Path)
    design.add_argument("--input", required=True, type=Path)
    generate = commands.add_parser("generate")
    generate.add_argument("workspace", type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("workspace", type=Path)
    measure = commands.add_parser("measure")
    measure.add_argument("workspace", type=Path)
    run_controls = commands.add_parser("run-controls")
    run_controls.add_argument("workspace", type=Path)
    run_controls.add_argument("--manifest", required=True, type=Path)
    candidate_audit = commands.add_parser("candidate-audit")
    candidate_audit.add_argument("workspace", type=Path)
    candidate_audit.add_argument("--controls", type=Path)
    candidate_audit.add_argument("--budget-fraction", type=float, default=.01)
    candidate_audit.add_argument("--near-reference-fraction", type=float, default=.85)
    candidate_audit.add_argument("--min-ablations", type=int, default=3)
    candidate_audit.add_argument("--min-ablation-effect-fraction", type=float, default=.02)
    candidate_audit.add_argument("--min-combined-effect-fraction", type=float, default=.1)
    build = commands.add_parser("build")
    build.add_argument("workspace", type=Path)
    build.add_argument("--docker", default="docker")
    build.add_argument("--timeout", type=int, default=3600)
    image_measure = commands.add_parser("measure-image")
    image_measure.add_argument("workspace", type=Path)
    image_measure.add_argument("--architecture", choices=sorted(common.ARCHITECTURES))
    image_measure.add_argument("--docker", default="docker")
    image_measure.add_argument("--build-timeout", type=int, default=1800)
    sforge_prepare = commands.add_parser("sforge-prepare")
    sforge_prepare.add_argument("workspace", type=Path)
    sforge_prepare.add_argument("--docker", default="docker")
    sforge_prepare.add_argument("--sforge", default="sforge")
    sforge_prepare.add_argument("--build-timeout", type=int, default=1800)
    rollout_collect = commands.add_parser("rollout-collect")
    rollout_collect.add_argument("workspace", type=Path)
    rollout_collect.add_argument("--log-dir", required=True, type=Path)
    rollout_check = commands.add_parser("rollout-check")
    rollout_check.add_argument("workspace", type=Path)
    task_review = commands.add_parser("task-review")
    task_review.add_argument("workspace", type=Path)
    task_review.add_argument("--input", required=True, type=Path)
    difficulty_probe = commands.add_parser("difficulty-probe")
    difficulty_probe.add_argument("workspace", type=Path)
    difficulty_probe.add_argument("--integrated", required=True, type=Path)
    difficulty_probe.add_argument("--ablated", required=True, type=Path)
    difficulty_probe.add_argument("--claim", required=True)
    difficulty_pack = commands.add_parser("difficulty-pack")
    difficulty_pack.add_argument("workspace", type=Path)
    difficulty_pack.add_argument("--input", required=True, type=Path)
    difficulty_pack.add_argument("--required-mechanisms", type=int, default=3)
    difficulty_card = commands.add_parser("difficulty-card")
    difficulty_card.add_argument("workspace", type=Path)
    difficulty_card.add_argument("--input", required=True, type=Path)
    difficulty_summary = commands.add_parser("difficulty-summary")
    difficulty_summary.add_argument("workspace", type=Path)
    difficulty_summary.add_argument("--required-mechanisms", type=int, default=3)
    rollout_start = commands.add_parser("rollout-start")
    rollout_start.add_argument("workspace", type=Path)
    rollout_start.add_argument("--run-id", required=True)
    rollout_start.add_argument("--log-dir", required=True, type=Path)
    rollout_start.add_argument("--judge-url", required=True)
    rollout_start.add_argument("--sforge", default="sforge")
    concurrent_rollout = commands.add_parser("rollout-batch")
    concurrent_rollout.add_argument("--manifest", required=True, type=Path)
    concurrent_rollout.add_argument("--workers", type=int, default=3)
    concurrent_rollout.add_argument("--sforge", default="sforge")
    monitor = commands.add_parser("monitor-tick")
    monitor.add_argument("workspace", type=Path)
    monitor.add_argument("--log-dir", required=True, type=Path)
    monitor.add_argument("--run-id", required=True)
    monitor.add_argument("--notify-agent", action="store_true")
    monitor.add_argument("--opencode", default="opencode")
    monitoring = commands.add_parser("monitor-batch")
    monitoring.add_argument("--manifest", required=True, type=Path)
    monitoring.add_argument("--workers", type=int, default=3)
    monitoring.add_argument("--notify-agent", action="store_true")
    monitoring.add_argument("--opencode", default="opencode")
    delivery = commands.add_parser("package-samples")
    delivery.add_argument("--manifest", required=True, type=Path)
    delivery.add_argument("--output", required=True, type=Path)
    delivery.add_argument("--draft", action="store_true")
    delivery.add_argument("--review-dir", type=Path)
    harbor_export = commands.add_parser("export-harbor", help="Export unpacked Harbor tasks; SForge supplies rollout evidence")
    harbor_export.add_argument("--manifest", required=True, type=Path)
    harbor_export.add_argument("--output", required=True, type=Path)
    harbor_export.add_argument("--draft", action="store_true")
    harbor_audit = commands.add_parser("harbor-audit", help="Read-only audit of Harbor delivery and its SForge scoring bridge")
    harbor_audit.add_argument("workspace", type=Path)
    harbor_receipt = commands.add_parser("record-harbor-evidence", help="Bind independently reviewed raw runtime/rebuild/bridge probe evidence")
    harbor_receipt.add_argument("workspace", type=Path)
    harbor_receipt.add_argument("--kind", choices=("runtime", "reproducibility", "equivalence", "determinism"), required=True)
    harbor_receipt.add_argument("--input", required=True, type=Path)
    sforge_audit = commands.add_parser("sforge-audit")
    sforge_audit.add_argument("workspace", type=Path)
    sforge_audit.add_argument("--manifest", type=Path)
    provenance = commands.add_parser("record-provenance")
    provenance.add_argument("workspace", type=Path)
    provenance.add_argument("--input", required=True, type=Path)
    quality_report = commands.add_parser("quality-report")
    quality_report.add_argument("--manifest", required=True, type=Path)
    quality_report.add_argument("--output", required=True, type=Path)
    completion = commands.add_parser("completion-report")
    completion.add_argument("workspace", type=Path)
    hashes = commands.add_parser("hash-data")
    hashes.add_argument("workspace", type=Path)
    resource_agreement = commands.add_parser("record-resource-agreement")
    resource_agreement.add_argument("workspace", type=Path)
    resource_agreement.add_argument("--input", required=True, type=Path)
    package = commands.add_parser("check-package")
    package.add_argument("workspace", type=Path)
    package.add_argument("--annex", required=True, type=Path)
    package.add_argument("--archive", required=True, type=Path)
    package.add_argument("--checker", required=True, type=Path)
    package.add_argument("--timeout", type=int, default=300)
    current = commands.add_parser("status")
    current.add_argument("workspace", type=Path)
    production = commands.add_parser("production-status")
    production.add_argument("workspace", type=Path)
    delivery_format = commands.add_parser(
        "delivery-format",
        help="record the user's immutable per-task choice of sforge-only or harbor",
    )
    delivery_format.add_argument("workspace", type=Path)
    delivery_format.add_argument("--format", required=True, choices=sorted(topify_delivery_contract.FORMATS))
    delivery_format.add_argument("--reviewer-id", default="main-agent")
    format_audit = commands.add_parser("format-audit", help="audit the selected delivery format")
    format_audit.add_argument("workspace", type=Path)
    selected_package = commands.add_parser(
        "package-selected", help="audit and package using the recorded user-selected format"
    )
    selected_package.add_argument("--manifest", required=True, type=Path)
    selected_package.add_argument("--output", required=True, type=Path)
    selected_package.add_argument("--draft", action="store_true")
    selected_package.add_argument("--review-dir", type=Path)
    final_qc = commands.add_parser(
        "rl-task-qc", help="run the final repository rl-task-qc static gate without mutating the target"
    )
    final_qc.add_argument("target", type=Path)
    final_qc.add_argument("--format", choices=("auto", "sforge-only", "harbor"), default="auto")
    final_qc.add_argument("--json", type=Path)
    final_qc.add_argument("--profile", type=Path)
    pre_qc = commands.add_parser(
        "pre-rollout-qc", help="write the read-only PQC v0.2 process report before trajectories"
    )
    pre_qc.add_argument("workspace", type=Path)
    pre_qc.add_argument("--output", required=True, type=Path)
    freeze = commands.add_parser("freeze", help="evaluate and record the immutable pre-rollout subject")
    freeze.add_argument("workspace", type=Path)
    freeze.add_argument("--reviewer-id", default="main-agent")
    profile = commands.add_parser(
        "benchmark-profile", help="bind a built-in or user-supplied benchmark requirements profile"
    )
    profile.add_argument("workspace", type=Path)
    profile.add_argument("--profile", required=True)
    profile.add_argument("--profile-file", type=Path)
    profile.add_argument("--requirements-doc", type=Path)
    profile.add_argument("--reviewer-id", default="main-agent")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            report, code = doctor(), 0
        elif args.command == "campaign-init":
            report, code = topify_seed.initialize_campaign(args.workspace, args.config), 0
        elif args.command == "campaign-status":
            report, code = topify_seed.campaign_status(args.workspace), 0
        elif args.command == "supervisor-init":
            report, code = topify_supervisor.initialize(
                args.workspace, args.mode, args.max_concurrent, args.dedup_root,
                args.supervisor_id, args.benchmark,
            )
        elif args.command == "supervisor-session-bind":
            report, code = topify_supervisor.bind_supervisor_session(
                args.workspace, args.session_id)
        elif args.command == "employee-hire":
            report, code = topify_supervisor.hire(
                args.workspace, args.employee_id, args.role, args.transport,
                args.task_id, args.seed_id, args.parent_id, args.prompt_file,
                args.replacement_of, args.fingerprint_file,
            )
        elif args.command == "employee-session-bind":
            report, code = topify_supervisor.bind_session(
                args.workspace, args.employee_id, args.session_id, args.dispatch_id)
        elif args.command == "producer-seed-bind":
            report, code = topify_supervisor.bind_seed(
                args.workspace, args.employee_id, args.seed_id, args.screen_receipt)
        elif args.command == "employee-status":
            report, code = topify_supervisor.status(args.workspace)
        elif args.command == "supervisor-dashboard":
            report, code = topify_supervisor.dashboard(args.workspace, args.employee_id)
        elif args.command == "supervisor-watch":
            report, code = topify_supervisor.watch(args.workspace, args.stale_after_seconds)
        elif args.command == "owner-request":
            report, code = control.request(args.workspace, args.employee_id, args.action, args.report, args.reason), 0
        elif args.command == "owner-request-sync":
            report, code = {"resolutions": control.sync_request_answer(args.workspace, args.request_id)}, 0
        elif args.command == "owner-reseed-chat":
            report, code = control.record_reseed_chat(args.workspace, args.employee_id, args.report, args.session_id, args.message_id), 0
        elif args.command == "candidate-disposition":
            report, code = topify_supervisor.candidate_disposition(args.workspace, args.employee_id, args.action)
        elif args.command == "supervisor-web":
            report, code = topify_web.serve(
                args.workspace, args.host, args.port, args.owner_token,
                args.supervisor_token, args.owner_token_file,
                args.supervisor_token_file, args.registry_file, args.scan_root,
                args.adopt_legacy, args.dedup_root, args.legacy_benchmark,
                args.no_auth,
            )
        elif args.command == "employee-fire":
            report, code = topify_supervisor.fire(
                args.workspace, args.employee_id, args.reason, args.evidence
            )
        elif args.command == "child-request":
            report, code = topify_supervisor.child_request(
                args.workspace, args.parent_id, args.child_id, args.role,
                args.objective, args.transport, args.task_id, args.seed_id,
                args.prompt_file,
            )
        elif args.command == "supervisor-dispatch":
            report, code = topify_supervisor.dispatch(
                args.workspace, args.employee_id, args.output
            )
        elif args.command == "producer-plan":
            report, code = topify_employees.plan(args.workspace, args.count, args.transport), 0
        elif args.command == "producer-dispatch":
            report, code = topify_employees.prepare(args.workspace, args.count, args.transport)
        elif args.command == "employee-report":
            report, code = topify_supervisor.report(
                args.workspace, args.employee_id, args.input
            )
        elif args.command == "employee-message":
            report, code = topify_supervisor.send_message(
                args.workspace, args.target_type, args.target_id, args.text,
                sender_type="employee", sender_id=args.employee_id,
                reply_to=args.reply_to,
            )
        elif args.command == "message-list":
            messages, code = topify_supervisor.list_messages(
                args.workspace, args.target_type, args.target_id, args.limit
            )
            report = {"messages": messages}
        elif args.command == "hardening-challenge":
            report, code = topify_supervisor.hardening_challenge(
                args.workspace, args.producer_id, args.round,
                args.note, args.evidence,
            )
        elif args.command == "dedup-claim":
            report, code = topify_supervisor.dedup_claim(
                args.workspace, args.employee_id, args.fingerprint_file
            )
        elif args.command == "dedup-register-task":
            report, code = topify_supervisor.dedup_register_task(
                args.workspace, args.employee_id, args.task_sha256
            )
        elif args.command == "qc-decision":
            report, code = topify_supervisor.qc_decision(
                args.workspace, args.producer_id, args.qc_id, args.round,
                args.decision, args.severity, args.report, args.note,
            )
        elif args.command == "qc-resolve":
            report, code = topify_supervisor.qc_resolve(
                args.workspace, args.producer_id, args.decision,
                args.note, args.evidence,
            )
        elif args.command == "qc-owner-answer":
            report, code = topify_supervisor.record_qc_owner_answer(args.workspace, args.producer_id, args.decision)
        elif args.command == "owner-decision":
            report, code = topify_supervisor.owner_decision(
                args.workspace, args.employee_id, args.decision,
                args.note, args.evidence,
            )
        elif args.command == "owner-form-create":
            report, code = topify_supervisor.create_owner_form(
                args.workspace, args.employee_id, args.note)
        elif args.command == "owner-form-sync":
            report, code = topify_supervisor.sync_owner_form(
                args.workspace, args.employee_id)
        elif args.command == "supervisor-rollout-gate":
            report, code = topify_supervisor.rollout_gate(
                args.workspace, args.employee_id, args.observations,
                args.profile_file, args.task_workspace, args.final,
            )
        elif args.command == "replacement-order":
            report, code = topify_supervisor.replace_seed(
                args.workspace, args.former_employee_id, args.new_employee_id,
                args.new_seed_id, args.transport, args.prompt_file,
                args.fingerprint_file,
            )
        elif args.command == "domain-plan":
            report, code = topify_domain.plan(args.workspace, args.playbook,
                                              limit=args.limit), 0
        elif args.command == "production-plan":
            report, code = (topify_production.plan(args.workspace, args.verifiers, args.authors)
                            if args.legacy_workers else topify_employees.plan(args.workspace, args.producers)), 0
        elif args.command == "production-dispatch":
            report, code = (topify_production.dispatch_native(args.workspace, args.verifiers, args.authors)
                            if args.legacy_workers else topify_employees.prepare(args.workspace, args.producers))
        elif args.command == "agent-workspace-reserve":
            report, code = topify_production.reserve_auxiliary_workspace(
                args.workspace, args.job_id, args.role)
        elif args.command == "production-tick":
            if not args.legacy_workers:
                raise common.PipelineError("producer-first uses producer-dispatch and persistent employee sessions; "
                                           "legacy timer execution requires explicit --legacy-workers")
            report, code = topify_production.tick(
                args.workspace, args.verifiers, args.authors,
                args.opencode, args.execute, args.timeout, args.notify_main,
            )
        elif args.command == "feasibility-plan":
            report, code = topify_feasibility.plan(args.workspace, args.seed_id, args.workers), 0
        elif args.command == "feasibility-tick":
            report, code = topify_feasibility.tick(
                args.workspace, args.seed_id, args.workers, args.opencode,
                args.timeout, args.execute,
            )
        elif args.command == "feasibility-review":
            report, code = topify_feasibility.review(args.workspace, args.seed_id, args.proposal)
        elif args.command == "micro-prototype-plan":
            report, code = topify_feasibility.micro_plan(args.workspace, args.seed_id, args.workers), 0
        elif args.command == "micro-prototype-tick":
            report, code = topify_feasibility.micro_tick(
                args.workspace, args.seed_id, args.workers, args.opencode,
                args.timeout, args.execute,
            )
        elif args.command == "micro-prototype-review":
            report, code = topify_feasibility.micro_review(args.workspace, args.seed_id, args.proposal)
        elif args.command == "scout":
            report, code = topify_seed.scout_catalog(args.workspace, args.catalog), 0
        elif args.command == "scout-source":
            report, code = topify_seed.scout_source(
                args.workspace, args.registry, args.source_id, args.capture
            ), 0
        elif args.command == "mirror-source":
            report, code = topify_source_mirror.capture(
                args.workspace, args.source_id, args.url, args.revision,
                args.expected_sha256, args.local_file, args.max_bytes,
            )
        elif args.command == "seed-tick":
            report, code = topify_daily.tick(
                args.workspace, args.registry, args.source_id, args.workers,
                args.opencode, args.timeout, args.notify_main,
            )
        elif args.command == "author-batch":
            report, code = topify_daily.author_batch(
                args.campaign, args.manifest, args.workers, args.opencode, args.timeout,
            )
        elif args.command == "author-dispatch":
            report, code = topify_daily.author_dispatch(args.campaign, args.manifest)
        elif args.command == "author-collect":
            report, code = topify_daily.author_collect(
                args.campaign, args.job_id, args.feedback_file, args.failed,
            )
        elif args.command == "scout-dispatch":
            report, code = topify_daily.scout_dispatch(
                args.workspace, args.registry, args.source_id,
            )
        elif args.command == "scout-collect":
            report, code = topify_daily.scout_collect(
                args.workspace, args.registry, args.source_id, args.capture_file,
            )
        elif args.command == "verifier-dispatch":
            report, code = topify_production.verifier_dispatch(args.workspace, args.workers)
        elif args.command == "verifier-collect":
            report, code = topify_production.verifier_collect(
                args.workspace, args.job_id, args.feedback_file, args.failed,
            )
        elif args.command == "seed-refine":
            report, code = topify_seed.refine_seed(args.workspace, args.seed_id, args.input)
        elif args.command == "seed-screen":
            report, code = topify_seed.screen_seed(
                args.workspace, args.seed_id, args.for_prototype, owner_sample=args.owner_sample)
        elif args.command == "seed-prototype":
            report, code = topify_seed.register_prototype(
                args.workspace, args.seed_id, args.task_workspace
            )
        elif args.command == "candidate-register":
            report, code = topify_seed.register_candidate(
                args.workspace, args.seed_id, args.task_workspace
            )
        elif args.command == "seed-shelve":
            report, code = topify_seed.shelve_seed(args.workspace, args.seed_id,
                                                  args.reviewer_id, args.reason)
        elif args.command == "seed-promote":
            report, code = topify_seed.promote_seed(args.workspace, args.seed_id)
        elif args.command == "batch-nop":
            report, code = topify_seed.batch_nop(args.workspace)
        elif args.command == "sforge-batch-nop":
            report, code = topify_sforge_batch.batch_nop(args.manifest, args.output_dir, args.sforge)
        elif args.command == "init":
            report, code = initialize(args.workspace, args.request), 0
        elif args.command == "design":
            report, code = screen_design(args.workspace, args.input)
        elif args.command == "generate":
            report, code = topify_harbor.generate(args.workspace), 0
        elif args.command == "validate":
            report, code = topify_validate.validate(args.workspace)
        elif args.command == "measure":
            report, code = topify_measure.measure(args.workspace)
        elif args.command == "run-controls":
            report, code = topify_candidate_audit.run_controls(args.workspace, args.manifest)
        elif args.command == "candidate-audit":
            report, code = topify_candidate_audit.assess(
                args.workspace, args.controls, max_budget_fraction=args.budget_fraction,
                near_reference_fraction=args.near_reference_fraction,
                min_ablations=args.min_ablations,
                min_ablation_effect_fraction=args.min_ablation_effect_fraction,
                min_combined_effect_fraction=args.min_combined_effect_fraction,
            )
        elif args.command == "build":
            report, code = topify_build.build_task(args.workspace, args.docker, args.timeout)
        elif args.command == "measure-image":
            report, code = topify_image_measure.measure_image(
                args.workspace, args.architecture, args.docker, args.build_timeout
            )
        elif args.command == "sforge-prepare":
            report, code = topify_sforge.prepare(
                args.workspace, args.docker, args.sforge, args.build_timeout
            )
        elif args.command == "rollout-collect":
            report, code = topify_rollout.collect(args.workspace, args.log_dir)
        elif args.command == "rollout-check":
            report, code = topify_rollout.preflight(args.workspace)
        elif args.command == "task-review":
            report, code = topify_review.review_task(args.workspace, args.input)
        elif args.command == "difficulty-probe":
            report, code = topify_review.probe_difficulty(
                args.workspace, args.integrated, args.ablated, args.claim,
            )
        elif args.command == "difficulty-pack":
            report, code = topify_review.difficulty_pack(
                args.workspace, args.input, args.required_mechanisms,
            )
        elif args.command == "difficulty-card":
            report, code = topify_review.difficulty_card(args.workspace, args.input)
        elif args.command == "difficulty-summary":
            report, code = topify_review.difficulty_summary(
                args.workspace, args.required_mechanisms,
            )
        elif args.command == "rollout-start":
            report, code = topify_rollout.start(
                args.workspace, args.run_id, args.log_dir, args.judge_url, args.sforge
            )
        elif args.command == "rollout-batch":
            report, code = topify_daily.rollout_batch(args.manifest, args.workers, args.sforge)
        elif args.command == "monitor-tick":
            report, code = topify_monitor.tick(
                args.workspace, args.log_dir, args.run_id, args.notify_agent, args.opencode,
            )
        elif args.command == "monitor-batch":
            report, code = topify_monitor.monitor_batch(
                args.manifest, args.workers, args.notify_agent, args.opencode,
            )
        elif args.command == "package-samples":
            report, code = topify_sforge_delivery.package_batch(
                args.manifest, args.output, draft=args.draft, review_dir=args.review_dir)
        elif args.command == "export-harbor":
            report, code = topify_harbor_delivery.export_batch(args.manifest, args.output, draft=args.draft)
        elif args.command == "harbor-audit":
            report = topify_harbor_delivery.audit_task(args.workspace)
            code = 0 if report["status"] == "complete" else 2
        elif args.command == "record-harbor-evidence":
            report, code = topify_harbor_acceptance.record_evidence(
                args.workspace, args.kind, args.input)
        elif args.command == "sforge-audit":
            report = topify_sforge_acceptance.audit_task(
                args.workspace, manifest=common.load_json(args.manifest) if args.manifest else None)
            code = 0 if report["status"] == "complete" else 2
        elif args.command == "record-provenance":
            report, code = topify_sforge_acceptance.record_provenance(args.workspace, args.input)
        elif args.command == "quality-report":
            report, code = topify_quality_report.quality_report(args.manifest, args.output)
        elif args.command == "completion-report":
            report, code = topify_completion.completion_report(args.workspace)
        elif args.command == "hash-data":
            report, code = topify_completion.hash_data(args.workspace)
        elif args.command == "record-resource-agreement":
            report, code = topify_completion.record_resource_agreement(args.workspace, args.input)
        elif args.command == "check-package":
            report, code = topify_completion.check_package(
                args.workspace, args.annex, args.archive, args.checker, args.timeout
            )
        elif args.command == "production-status":
            report, code = production_status(args.workspace), 0
        elif args.command == "delivery-format":
            report, code = topify_delivery_contract.select(
                args.workspace, args.format, args.reviewer_id)
        elif args.command == "format-audit":
            root, metadata = common.load_workspace(args.workspace)
            task_hash = topify_validate.task_sha256(root / "task")
            selected = topify_delivery_contract.require(root, task_hash)
            if selected["format"] == "sforge-only":
                report = topify_sforge_acceptance.audit_task(root)
            else:
                report = topify_harbor_acceptance.audit_task(root)
            report["delivery_format"] = selected["format"]
            code = 0 if report.get("status") == "complete" else 2
        elif args.command == "package-selected":
            manifest = common.load_json(args.manifest)
            selected_format = manifest.get("delivery_format")
            if selected_format == "sforge-only":
                report, code = topify_sforge_delivery.package_batch(
                    args.manifest, args.output, draft=args.draft, review_dir=args.review_dir)
            elif selected_format == "harbor":
                if args.review_dir is not None:
                    raise common.PipelineError("Harbor packaging does not accept --review-dir")
                report, code = topify_harbor_delivery.export_batch(
                    args.manifest, args.output, draft=args.draft)
            else:
                raise common.PipelineError(
                    "package-selected requires manifest delivery_format=sforge-only or harbor")
        elif args.command == "rl-task-qc":
            report, code = topify_rl_task_qc.run(args.target, args.format, args.json, args.profile)
        elif args.command == "pre-rollout-qc":
            report, code = topify_process_qc.report(args.workspace, args.output)
        elif args.command == "benchmark-profile":
            report, code = topify_profile.select(
                args.workspace, args.profile, args.profile_file,
                args.requirements_doc, args.reviewer_id)
        elif args.command == "freeze":
            report, code = topify_freeze.freeze(args.workspace, args.reviewer_id)
        else:
            report, code = status(args.workspace), 0
        common.print_json(report)
        return code
    except (common.PipelineError, OSError, ValueError, TypeError) as exc:
        print(f"topify_pipeline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
