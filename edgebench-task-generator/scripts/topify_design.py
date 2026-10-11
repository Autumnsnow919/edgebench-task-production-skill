#!/usr/bin/env python3
"""Validate that a candidate can produce a meaningful long-horizon score curve."""

from __future__ import annotations

from pathlib import PurePosixPath
import re

import topify_common as common


def _text(value: object, minimum: int = 20) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def _workspace_path(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("/workspace/"):
        return False
    path = PurePosixPath(value)
    return ".." not in path.parts and str(path) == value


def _check(check_id: str, passed: bool, detail: str) -> dict:
    return {"id": check_id, "status": "complete" if passed else "failed", "detail": detail}


def evaluate(design: dict, request: dict, *, profile: dict | None = None) -> dict:
    feedback = design.get("feedback") if isinstance(design.get("feedback"), dict) else {}
    upper = design.get("upper") if isinstance(design.get("upper"), dict) else {}
    artifacts = design.get("snapshot_artifacts")
    levers = design.get("improvement_levers")
    interiors = design.get("interior_candidates")
    signals = feedback.get("actionable_signals")
    ablations = design.get("ablation_plan")
    cheap = design.get("cheap_baseline")
    cheap = cheap if isinstance(cheap, dict) else {}
    scoring = request["scoring"]
    headroom = abs(float(scoring["upper"]) - float(scoring["baseline"]))
    noise = design.get("expected_repeat_noise")
    score_cost = design.get("expected_verifier_end_to_end_seconds")
    planned = design.get("planned_submission_count")
    profile_checks = profile.get("checks", {}) if isinstance(profile, dict) else {}
    continuous_required = profile_checks.get("continuous_reward", True)

    artifacts_ok = (
        isinstance(artifacts, list) and bool(artifacts)
        and len(set(artifacts)) == len(artifacts)
        and all(_workspace_path(item) for item in artifacts)
    )
    levers_ok = (
        isinstance(levers, list) and len(levers) >= 2
        and len(set(levers)) == len(levers)
        and all(_text(item, 10) for item in levers)
    )
    interiors_ok = (
        isinstance(interiors, list) and bool(interiors)
        and all(isinstance(item, dict) and _text(item.get("name"), 2)
                and _text(item.get("expected_relation"), 5) for item in interiors)
    )
    feedback_ok = (
        _workspace_path(feedback.get("development_split"))
        and _workspace_path(feedback.get("local_scorer"))
        and isinstance(signals, list) and bool(signals)
        and all(_text(item, 3) for item in signals)
        and feedback.get("uses_private_answers") is False
    )
    accepted_upper_types = {
        "measured-reference", "human-expert", "published-sota", "ensemble",
        "saturation-estimate",
    }
    upper_ok = (
        upper.get("basis_type") in accepted_upper_types
        and _text(upper.get("description"), 20)
        and _text(scoring.get("upper_basis"), 10)
    )
    ablations_ok = (
        isinstance(ablations, list) and len(ablations) >= 3
        and all(isinstance(item, dict) and _text(item.get("name"), 2)
                and _text(item.get("mechanism_removed"), 10)
                and _text(item.get("expected_effect"), 20) for item in ablations)
        and len({item["name"].strip() for item in ablations if isinstance(item, dict)}) == len(ablations)
    )
    cheap_id = cheap.get("control_id")
    cheap_ok = (_text(cheap.get("method"), 20)
                and _text(cheap.get("domain_fit"), 20)
                and isinstance(cheap_id, str)
                and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", cheap_id) is not None)
    noise_ok = common.finite_number(noise) and 0 <= float(noise) < headroom
    cost_ok = (
        common.finite_number(score_cost) and 0 < float(score_cost) < request["interaction_budget_seconds"]
        and type(planned) is int and planned >= 3
        and float(score_cost) * planned < request["interaction_budget_seconds"]
    )

    checks = [
        _check("design.real_work", _text(design.get("real_work"))
               and _text(design.get("real_world_consequence")),
               "The task represents real work with a concrete consequence."),
        _check("design.long_horizon", _text(design.get("long_horizon_reason")),
               "The design explains why iterative hours of work are useful."),
        _check("design.snapshot", artifacts_ok,
               "The score depends on explicit files under /workspace."),
        _check("design.improvement_levers", levers_ok,
               "The task exposes multiple distinct ways to improve a legal artifact."),
        _check("design.continuous_improvement",
               (design.get("binary_only") is False and interiors_ok)
               if continuous_required else design.get("binary_only") is True,
               "The selected profile requires a lawful continuous score ladder."
               if continuous_required else "The selected profile permits a binary reward."),
        _check("design.no_waiting", design.get("requires_waiting") is False,
               "Progress comes from work rather than elapsed wall-clock time."),
        _check("design.local_feedback", feedback_ok,
               "Public local feedback is actionable and does not disclose private answers."),
        _check("design.achievable_upper", upper_ok,
               "The upper is grounded by an achievable measured or sourced result."),
        _check("design.score_headroom", noise_ok,
               "Expected repeat noise is smaller than the baseline-to-upper improvement space."),
        _check("design.verifier_cost", cost_ok,
               "Repeated scoring fits inside the selected interaction window."),
        _check("design.coupling_mechanism", _text(design.get("coupling_mechanism"), 40),
               "The design names the coupled mechanism that defeats one-shot greedy solutions."),
        _check("design.ablation_plan", ablations_ok,
               "At least three distinct controlled ablations with expected score effects are planned."),
        _check("design.cheap_baseline", cheap_ok,
               "A domain-appropriate cheap baseline, its fit, and a named measured control are required."),
    ]
    complete = all(item["status"] == "complete" for item in checks)
    return {
        "schema_version": 1,
        "verdict": "complete" if complete else "failed",
        "checks": checks,
    }
