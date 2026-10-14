#!/usr/bin/env python3
"""Separate owner-only Chinese translations of bounded public EdgeBench facts."""

from __future__ import annotations

import topify_common as common


def _scenario(item: dict) -> tuple[str, str, str]:
    values = tuple(item.get(key) for key in ("scenario_zh", "consequence_zh", "why_long_zh"))
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise common.PipelineError("Chinese owner review needs reviewed scenario, consequence and long-work translation")
    return values


def review_readme(tasks: list[dict]) -> bytes:
    lines = [f"# EdgeBench — {len(tasks)} 题 SForge 小样中文审阅件", "",
             "这些任务模拟真实工作中的多轮改进：智能体保留合法中间产物，读取可解释的评分反馈，再继续优化。",
             "英文题面和评分字节是唯一模型运行依据；本文仅帮助审阅客户交付说明。", "",
             "## 已记录的模型执行", "",
             "| 任务 | 模型 / 档位 | Trial | 最高分（0–100） | SForge 运行墙钟 | Agent Step |",
             "|---|---|---|---:|---|---|"]
    for item in tasks:
        if not item["rows"]:
            lines.append(f"| {item['task_id']} | 无已完成模型执行 | — | — | 未记录 | 未记录 |")
        for row in item["rows"]:
            seconds = round(row["run_wall_seconds"])
            duration = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
            step = row["step"] if row["step"] != "not recorded" else "未记录"
            lines.append(f"| {item['task_id']} | {row['model']} / {row['effort']} | {row['trial_id']} | "
                         f"{row['score']} | {duration} | {step} |")
    lines += ["", "SForge 总运行墙钟包含框架开销，不能替代单独 Agent 执行耗时；Step 不由提交次数推算。",
              "NOP 与静态参考是校准快照，不是强弱模型；连续得分不自动等于二元成功。", "",
              "## 任务和业务后果", ""]
    for item in tasks:
        scenario, consequence, why = _scenario(item)
        lines += [f"### {item['task_id']}", "", f"工作场景：{scenario}",
                  f"错误后果：{consequence}", f"为何需要持续改进：{why}", "",
                  f"评分公式：`{item['formula']}`。归一化得分越高越好。", ""]
    lines += ["## 审阅与复现", "", "客户 ZIP 中的英文 README、冻结任务 JSON、Work/Judge 构建上下文、",
              "真实评分报告与 SHA-256 清单共同界定可复查范围。运行时 `/workspace` 与 ZIP 内目录不同。",
              "如未记录某项测量，不将其描述为已通过。本文不改变英文任务和模型运行结果。", ""]
    return ("\n".join(lines)).encode("utf-8")


def review_qc(tasks: list[dict]) -> bytes:
    lines = ["# 【Morrow Lab】EdgeBench 小样中文质检审阅件", "",
             "## 1. 摘要", "", "以下仅复述英文客户材料所依据的任务和成绩；不增加模型轨迹或评分结论。", "",
             "| 任务 | 已完成模型执行 | 当前技术审计 | 题目审阅范围 |",
             "|---|---:|---|---|"]
    for item in tasks:
        audit = item["audit"]
        quality = item["quality_status"]
        passed = sum(status in {"pass", "accepted"} for status in quality.values())
        technical = "已核验" if audit["status"] == "complete" else "缺少完整技术收据"
        lines.append(f"| {item['task_id']} | {len(item['rows'])} | {technical} | "
                     f"Q1–Q7：{passed}/{len(quality)} 项记录通过 |")
    lines += ["", "## 2. 任务概况", ""]
    for item in tasks:
        scenario, consequence, why = _scenario(item)
        lines += [f"### {item['task_id']}", "", scenario, "", f"业务后果：{consequence}",
                  f"持续改进机制：{why}", ""]
    lines += ["## 3. 已记录结果与适用边界", "", "| 任务 | Trial | 模型 / 档位 | 最高分 | SForge 墙钟（秒） | Agent Step |",
              "|---|---|---|---:|---:|---|"]
    for item in tasks:
        if not item["rows"]:
            lines.append(f"| {item['task_id']} | 无已完成执行 | — | — | — | 未记录 |")
        for row in item["rows"]:
            step = row["step"] if row["step"] != "not recorded" else "未记录"
            lines.append(f"| {item['task_id']} | {row['trial_id']} | {row['model']} / {row['effort']} | "
                         f"{row['score']} | {row['run_wall_seconds']} | {step} |")
    lines += ["", "## 4. 题目审阅", "", "| 任务 | 分项 | 状态 |", "|---|---|---|"]
    for item in tasks:
        for key, status in item["quality_status"].items():
            translated = {"pass": "通过", "fail": "不通过", "pending": "待核查"}.get(status, "未记录")
            lines.append(f"| {item['task_id']} | {key} | {translated} |")
    lines += ["", "仅展示已保留的题目审阅范围；不据此虚构百分制质量总分。",
              "运行环境和资源等技术要求与题目审阅独立；缺少原始收据时保持未完成。", ""]
    return ("\n".join(lines)).encode("utf-8")
