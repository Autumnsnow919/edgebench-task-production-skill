#!/usr/bin/env python3
"""Write an independent bilingual task/batch inspection report beside the ZIP."""

from __future__ import annotations

from pathlib import Path
import re

import topify_common as common
import topify_completion
import topify_review
import topify_sforge_acceptance
import topify_delivery_contract


QC_FILENAME = re.compile(r"【Morrow Lab】EdgeBench 小样质检报告 \d{4}-\d{2}-\d{2}\.md\Z")
EN_QC_FILENAME = re.compile(r"【Morrow Lab】EdgeBench Sample Quality Inspection Report \d{4}-\d{2}-\d{2}\.md\Z")


WEIGHTS = (15, 15, 15, 15, 15, 10, 5)
QUALITY_ZH = (
    "题目定义清晰度", "题面与验收对齐", "合法可解性与环境", "规则与参考答案正确性",
    "验收对错误的识别能力", "验收公平性", "判分可靠性",
)
CUSTOMER_CHECKS = {
    "sforge.minimum_three_hour_budget": "Declared interaction budget",
    "sforge.separate_contexts": "Separate Work and Judge contexts",
    "sforge.runtime_isolation": "Offline Judge runtime",
    "sforge.score_parity": "Fixed-snapshot scorer agreement",
    "sforge.original_starter_nop": "Unmodified starter and NOP baseline agreement",
    "sforge.five_reference_scores": "Repeated reference score stability",
    "sforge.cold_start_and_score_time": "Scoring startup and end-to-end time",
    "sforge.native_cross_architecture": "Native architecture scoring comparison",
    "sforge.nop_per_task": "NOP lower-bound score",
    "sforge.batch_nop": "Batch NOP scan",
    "sforge.reference_or_open_solution": "Runnable reference or open-solution",
    "sforge.multiarch_build_and_base": "Multi-architecture image build",
    "sforge.reproducible_contexts": "Reproducible Work and Judge contexts",
    "sforge.resource_agreement": "Agreed resource envelope",
    "sforge.frontier_three_runs": "Comparable frontier model trials",
    "sforge.model_snapshot_replay": "Retained model-submission replay on both native architectures",
}


def _text(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _write_customer_report(output: Path, lines: list[str], *, allow_chinese: bool = False) -> None:
    # Keep exactly one customer-data scanner for both direct report generation
    # and reports created as part of a SForge package.
    import topify_customer_safety

    content = "\n".join(lines).encode("utf-8")
    topify_customer_safety.check(output.name, content, allow_chinese=allow_chinese)
    with output.open("xb") as handle:
        handle.write(content)


def _score_interval(review: dict) -> tuple[float, float, int, int, int]:
    quality = review.get("quality") if review.get("status") in {"ready", "revise", "reject"} else None
    if not isinstance(quality, dict):
        quality = {}
    lower = upper = 0.0
    passed = failed = pending = 0
    for index, key in enumerate(topify_review.QUALITY_IDS):
        item = quality.get(key)
        state = item.get("status") if isinstance(item, dict) else None
        if state == "pass":
            passed += 1
            lower += WEIGHTS[index]
            upper += WEIGHTS[index]
        elif state == "fail":
            failed += 1
            # Section 11.5 severity has a minimum ten-point deduction per confirmed defect.
            upper += WEIGHTS[index] * .9
        else:
            pending += 1
            upper += WEIGHTS[index]
    return round(lower, 1), round(upper, 1), passed, failed, pending


def _criterion_interval(state: object, weight: int) -> tuple[str, str]:
    if state == "pass":
        return "100/100", f"{weight:.1f}/{weight}"
    if state == "fail":
        return "0–90/100 (severity ungraded)", f"0.0–{weight * .9:.1f}/{weight}"
    return "0–100/100 (pending)", f"0.0–{weight:.1f}/{weight}"


def _task_record(workspace: str, manifest: dict | None = None) -> dict:
    root, metadata = common.load_workspace(workspace)
    audit = topify_sforge_acceptance.audit_task(root, manifest=manifest)
    task_sha = audit["task_sha256"]
    review = topify_completion._latest(root / "evidence/author-reviews")
    if review.get("task_sha256") != task_sha:
        review = {}
    rollout = topify_completion._latest(root / "evidence/rollout-collections")
    if rollout.get("task_sha256") != task_sha:
        rollout = {}
    design = common.load_json(root / "task_design.json")
    return {
        "task_id": metadata["task_id"],
        "task_sha256": task_sha,
        "request": metadata["request"],
        "design": design,
        "audit": audit,
        "review": review,
        "rollout": rollout,
        "quality": _score_interval(review),
    }


def _run_rows(item: dict) -> list[str]:
    runs = item["rollout"].get("runs", [])
    rows = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict) or run.get("status") != "complete":
            continue
        score = run.get("best_score")
        runtime = run.get("runtime_seconds")
        if not (common.finite_number(score) and common.finite_number(runtime)):
            continue
        step = run.get("agent_steps")
        rows.append(
            f"| {_text(run.get('run_id', 'unknown'))} | {_text(run.get('model', 'not recorded'))} "
            f"| {_text(run.get('effort', 'not recorded'))} | {score} | {runtime:.1f} "
            f"| {step if type(step) is int and step >= 0 else 'not recorded'} "
            f"| {_text(run.get('agent_submissions', 'not recorded'))} |"
        )
    return rows


def quality_report(manifest_path: str | Path, output_path: str | Path) -> tuple[dict, int]:
    manifest = common.load_json(manifest_path)
    profile = manifest.get("delivery_profile") == "owner-sample-2026-09-29"
    if (set(manifest) not in ({"schema_version", "batch_id", "workspaces"},
                             {"schema_version", "batch_id", "workspaces", "delivery_language"},
                             {"schema_version", "batch_id", "workspaces", "delivery_language", "delivery_profile"},
                             {"schema_version", "batch_id", "workspaces", "delivery_format"},
                             {"schema_version", "batch_id", "workspaces", "delivery_format", "delivery_language"})
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or not isinstance(manifest.get("workspaces"), list) or not manifest["workspaces"]):
        raise common.PipelineError("quality report needs a valid batch delivery manifest")
    topify_delivery_contract.manifest_format(manifest, "sforge-only")
    language = manifest.get("delivery_language", "en")
    if language not in {"en-zh", "en"} or ("delivery_profile" in manifest and not profile):
        raise common.PipelineError("delivery_language must be en-zh or en")
    if profile and (language != "en" or len(manifest["workspaces"]) != 1):
        raise common.PipelineError("owner sample quality report requires one English task")
    output = Path(output_path).absolute()
    filename = QC_FILENAME if language == "en-zh" else EN_QC_FILENAME
    if ((output.name != "EdgeBench Sample Quality Inspection Report.md" if profile
         else not filename.fullmatch(output.name))
            or output.exists() or output.is_symlink() or not output.parent.is_dir()):
        raise common.PipelineError("quality report must be new and match delivery_language")
    tasks = [_task_record(workspace, manifest if profile else None) for workspace in manifest["workspaces"]]
    ids = [item["task_id"] for item in tasks]
    if len(ids) != len(set(ids)):
        raise common.PipelineError("quality report has duplicate task IDs")
    tasks.sort(key=lambda item: item["task_id"])
    count = len(tasks)
    pass_count = sum(item["quality"][2] for item in tasks)
    fail_count = sum(item["quality"][3] for item in tasks)
    pending_count = sum(item["quality"][4] for item in tasks)
    low = round(sum(item["quality"][0] for item in tasks) / count, 1)
    high = round(sum(item["quality"][1] for item in tasks) / count, 1)
    complete = all(item["audit"]["status"] == "complete" for item in tasks)
    model_names = sorted({
        run.get("model")
        for item in tasks for run in item["rollout"].get("runs", [])
        if isinstance(run, dict) and run.get("status") == "complete" and isinstance(run.get("model"), str)
    })
    lines = [
        "# 【Morrow Lab】 EdgeBench Sample Quality Inspection Report", "",
        "## 1. Summary", "",
        "| Metric | Observed scope or outcome |", "|---|---|",
        f"| Task count | {count} |",
        f"| Trajectory model(s) | {', '.join(model_names) if model_names else 'Not verified'} |",
        "| Upper/lower model roles | Not declared; reference and NOP are not model trials |",
        "| Successful trajectory / Agent Step | Not established without a declared success threshold or primary-agent steps |",
        f"| Reviewed task criteria | {pass_count}/{len(WEIGHTS) * count} ({pass_count / (len(WEIGHTS) * count) * 100:.1f}%; {fail_count} failed, {pending_count} pending) |",
        f"| Q1–Q7 checked-weight subtotal | {low:.1f}–{high:.1f}/{sum(WEIGHTS)} (not a 100-point quality score) |",
        f"| Non-format DOCX checks | {sum(sum(row['status'] == 'complete' for row in item['audit']['rows']) for item in tasks)}/{sum(sum(row['status'] != 'not_required_by_owner' for row in item['audit']['rows']) for item in tasks)} required checks confirmed |",
        f"| Inspection status | {'Checks completed; customer acceptance not asserted' if complete else 'INCOMPLETE: missing executed evidence'} |",
        "", "## 2. Task Summary", "",
        "| Task | Capability | Real work | Material consequence | Artifact |",
        "|---|---|---|---|---|",
    ]
    if profile:
        arm_status = next((row["status"] for row in tasks[0]["audit"]["rows"]
                           if row["id"] == "sforge.arm64_owner_comparison"), "incomplete")
        at = lines.index("## 2. Task Summary")
        lines[at:at] = [
            f"| ARM64 comparison | {'PASS (native SForge receipts)' if arm_status == 'complete' else 'INCOMPLETE'} |",
            "| Formal acceptance | NO — review sample only |",
            "| Owner-specific nonrequired checks | source declaration, four-catalog dedup, redistribution confirmation: not_required_by_owner |",
            "",
        ]
    for item in tasks:
        lines.append(
            f"| {_text(item['task_id'])} | {_text(item['request']['category'])} "
            f"| {_text(item['design'].get('real_work', 'Not verified'))} "
            f"| {_text(item['design'].get('real_world_consequence', 'Not verified'))} "
            "| `tasks/<namespace>/` SForge JSON and contexts |"
        )
    lines.extend([
        "", "## 3. Scope and metrics", "",
        "Q1–Q7 here assess the **task definition**, not SForge reward or a model pass rate.",
        "Each confirmed pass contributes its guide weight; pending items contribute 0–full",
        "weight to the uncertainty interval, and an ungraded confirmed failure contributes",
        "0–90% of its weight pending severity assessment. No pass threshold has been invented.",
        "SForge `runtime_seconds` is run wall time, **not** separately measured Agent-execution",
        "wall time. Steps are shown only when recorded on the same run. Scores are continuous",
        "best-submission results, never inferred binary successes. Static checks and model",
        "results are separate from task-quality rubric proportions.", "",
        "## 4. Task-by-task inspection", "",
    ])
    for item in tasks:
        review = item["review"]
        quality = review.get("quality", {}) if isinstance(review.get("quality"), dict) else {}
        lower, upper, p, f, u = item["quality"]
        lines.extend([
            f"### {_text(item['task_id'])}", "",
            f"Frozen task SHA-256: `{item['task_sha256']}`. Q1–Q7: {p} pass, {f} fail, {u} pending. ",
            f"Reviewed-weight subtotal: {lower:.1f}–{upper:.1f}/{sum(WEIGHTS)}; no 100-point quality score is assigned.", "",
            "| Criterion | Weight | Status | Criterion score | Weighted contribution | Finding / evidence |",
            "|---|---:|---|---|---|---|",
        ])
        for key, name, weight in zip(topify_review.QUALITY_IDS, topify_review.QUALITY_NAMES, WEIGHTS):
            entry = quality.get(key, {})
            if not isinstance(entry, dict):
                entry = {}
            evidence = entry.get("evidence")
            evidence_names = ", ".join(_text(value) for value in evidence) if isinstance(evidence, list) else "Not verified"
            criterion, weighted = _criterion_interval(entry.get("status"), weight)
            lines.append(
                f"| {key} {name} | {weight} | {_text(entry.get('status', 'pending'))} "
                f"| {criterion} | {weighted} | {_text(entry.get('finding', 'Not reviewed'))}; {evidence_names} |"
            )
        lines.extend(["", "Task-bound technical checks and evidence scope:", "",
                      "| Check | Observed scope |", "|---|---|"])
        for row in item["audit"]["rows"]:
            label = CUSTOMER_CHECKS.get(row["id"])
            if label is not None:
                scope = "Confirmed by retained task-bound evidence" if row["status"] == "complete" else "Not demonstrated in this handoff"
                lines.append(f"| {label} | {scope} |")
        lines.extend([
            "", "Recorded complete full-budget trials (if any):", "",
            "| Trial | Model | Effort | Best score | SForge run wall time (s) | Agent Step | Agent submissions |",
            "|---|---|---|---:|---:|---:|---:|",
        ])
        lines.extend(_run_rows(item) or ["| Not verified | — | — | — | — | — | — |"])
        lines.append("")
    if count > 1:
        lines.extend([
            "## 5. Batch synthesis", "",
            f"The {count} tasks are reviewed separately. {sum(item['audit']['status'] == 'complete' for item in tasks)} have",
            "all currently mapped non-format checks complete. The batch range above weights",
            "tasks equally; it does not hide a task with a serious or unresolved defect.", "",
        ])
    if language == "en":
        lines.append("")
        _write_customer_report(output, lines)
        return {
            "status": "complete" if complete else "incomplete", "task_count": count,
            "confirmed_rubric_pass_count": pass_count, "rubric_denominator": len(WEIGHTS) * count,
            "quality_score_interval": None, "reviewed_weight_subtotal": [low, high],
            "reviewed_weight_denominator": sum(WEIGHTS), "report_path": str(output),
            "report_sha256": common.sha256_file(output),
        }, 0 if complete else 2
    lines.extend([
        "## 中文说明：审查结论与整改", "",
        f"本次逐题核查 {count} 道 EdgeBench 任务；确认通过的题目审查项为 {pass_count}/{len(WEIGHTS) * count}，",
        f"已确认未通过 {fail_count} 项、待核查 {pending_count} 项。Q1–Q7 已审权重小计为 {low:.1f}–{high:.1f}/{sum(WEIGHTS)}，不构成百分制质量分；",
        "缺少证据或缺陷严重度时不将区间上限写成最终质量分。模型得分、SForge 运行时长",
        "和主模型 Step 使用各自口径，不将部分分数擅自记为成功。", "",
    ])
    for item in tasks:
        lines.append(
            f"- `{item['task_id']}`：题目核查为 {item['audit']['status']}；待补证据："
            + ("、".join(item["audit"]["missing"]) if item["audit"]["missing"] else "无")
        )
        lines.extend([
            f"### {_text(item['task_id'])}：逐项质量审查", "",
            "| 分项 | 权重 | 状态 | 分项得分区间 | 加权贡献区间 | 证据与评语 |",
            "|---|---:|---|---|---|---|",
        ])
        reviewed = item["review"].get("quality", {})
        if not isinstance(reviewed, dict):
            reviewed = {}
        status_zh = {"pass": "通过", "fail": "未通过", "pending": "待核查"}
        for key, name, weight in zip(topify_review.QUALITY_IDS, QUALITY_ZH, WEIGHTS):
            value = reviewed.get(key, {})
            if not isinstance(value, dict):
                value = {}
            state = value.get("status", "pending")
            description = _text(value.get("finding", "尚无可核验审查结论"))
            sources = value.get("evidence", [])
            source_text = ", ".join(_text(path) for path in sources) if isinstance(sources, list) else "未核查"
            criterion, weighted = _criterion_interval(state, weight)
            lines.append(f"| {key} {name} | {weight} | {status_zh.get(state, '待核查')} "
                         f"| {criterion} | {weighted} | {_text(description)}；{_text(source_text)} |")
        lines.extend([
            "", "SForge 模型运行成绩见英文逐题表；缺少主模型 Step 或单独 Agent 执行起止时间时",
            "明确写未记录，不以 SForge 总运行时间或提交次数替代。", "",
        ])
    if count > 1:
        lines.append("- 批次概括：先逐题保留严重问题和未核查项，再按独立任务等权归纳，不以总体均分掩盖单题缺陷。")
    lines.append("")
    _write_customer_report(output, lines, allow_chinese=True)
    return {
        "status": "complete" if complete else "incomplete", "task_count": count,
        "confirmed_rubric_pass_count": pass_count, "rubric_denominator": len(WEIGHTS) * count,
        "quality_score_interval": None, "reviewed_weight_subtotal": [low, high],
        "reviewed_weight_denominator": sum(WEIGHTS), "report_path": str(output),
        "report_sha256": common.sha256_file(output),
    }, 0 if complete else 2
