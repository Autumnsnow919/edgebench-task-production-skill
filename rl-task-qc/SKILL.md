---
name: rl-task-qc
description: "RL / benchmark 任务交付质检：按用户选择检查 Meta Harbor 或 EdgeBench SForge，覆盖 Vendor V3.0、EdgeBench V1、过程小样、轨迹后打包和运行证据。"
metadata:
  requires: "python3"
  optional: "docker; ~/.smoldata/smoldata-vendor-cli; harbor"
---

# rl-task-qc：RL 任务交付质检

一句话：**好任务 = 题面和验证器互为充要条件，没有捷径，难得有道理，而且这些都有证据。** 质检就是逐门拿证据。

## 0. 先做什么

1. **找最新口径**（顺序：用户需求文档 > Benchmark 专属规范 > Vendor Spec > 阶段质检文档）。标准变过很多次，先绑定一个 profile；EdgeBench 使用 `edgebench-v1`，兼容 Benchmark 使用用户需求文档生成的 profile。
2. 确认用户选择的格式：`harbor`（有 task.toml、独立 verifier）或 `sforge-only`（task JSON、BENCHMARK.yaml、contexts/work、contexts/judge）。选择写入任务工作区并绑定冻结任务哈希；不能用另一格式的静态报告替代。
3. 解包到工作目录，**不要动原包**。中文 zip 名用 `ditto -x -k`。

## 1. 六道门

| 门 | 查什么 | 怎么查 | 不过怎么办 |
|---|---|---|---|
| 1 静态 | 结构、声明、权限、泄露、Dockerfile、verifier 源码模式 | `python3 scripts/qc_static.py <包目录> --format sforge-only` 或 `--format harbor` | FAIL 全清零再进下一门 |
| 2 运行 | 按选择执行 Harbor 或 SForge 的 Oracle/NOP、两架构、重评一致、低 ulimit 和隔离检查 | 见 [runtime_gate.md](runtime_gate.md)，EC2 c7i + c7g 优先 | 任何一项失败退回生产 |
| 3 规格 | 隐藏测试每条能回到题面，题面每条有测试；失败轨迹是否全死在同一条 | 人读；非作者做映射表 | 映射缺一侧退回 |
| 4 对抗 | 候选代码能否读到答案、改 reward、上网；三种变体（删核心实现、硬编码输出、改测试）必须 0 | 读 judge 代码 + 实跑变体 | 任何变体得分退回 |
| 5 难度 | 同模型 ≥3 次完整运行，曲线有爬升，上限可达但未饱和，NOP 到参考之间有余量；补点测试 | 读 observations 重算，不信自带报告 | 全平 / 饱和 / 修 1 点全过：退回或换题 |
| 6 集合与对外 | 对外包只留当前版本事实，无内部过程；文档数字由脚本生成；SHA 清单 | 脚本 + 人扫 README | 清完重生成 SHA |

完整条目见 [checklist.md](checklist.md)，每条带来源标签。踩坑原因见 [lessons.md](lessons.md)。

## 2. 跑静态门

```bash
python3 <skill目录>/scripts/qc_static.py <解包后的交付目录> \
  --format auto|sforge-only|harbor --json qc.json
```

- 支持显式检查 `--format sforge-only` 或 `--format harbor`；`auto` 只在目录中只有一种格式时自动选择，混合格式会失败。
- 输出一张 FAIL / WARN / PASS 表，每行带规则编号、来源标签、文件行号。FAIL 任一条即退出码 1。
- 第三方源码目录（site-packages、vendor、workspace 里的上游仓库、轨迹）默认不扫破折号和措辞；用 `--third-party '*/foo/*'` 追加。
- 它只看文件：镜像能否构建、Oracle / NOP、双架构、ulimit 都查不出，必须跑运行门。

只有用户选择 Harbor 且明确要求平台预检时，才跑平台自己的检查：

```bash
~/.smoldata/smoldata-vendor-cli uploads preflight <tasks 父目录>
```

## 3. 写报告

受众分两种，两份报告：给生产团队的（中文，逐条带文件行号和修法）和给客户的（英文，只有小样本身，目前不给质检报告）。

给生产团队的结构：
1. 总结论表：每题「可交 / 小修 / 必须改 / 不能交」加一句原因。
2. 逐题：已对的（带证据）、要改的（文件:行，怎么改）。
3. 整包层面。
4. 修复清单按 P0 / P1 / P2，P2 标明谁决定。
5. 依据和时间（哪条客户决定、哪版 spec、哪次过程质检文档）。

措辞：结论留余地（"基于现有文件"），不改写客户原文门槛，不用破折号。报告发到项目群，@ 出题人和负责人。

## 4. 不要做的

- 不信随包质检报告的结论，一律从文件重算。
- 不把 dry run 通过当成验收通过。
- 不在 public 网络下跑的 pass@k 不采信。
- 不删原包，在副本上改；修完重跑静态门，零 FAIL 再出包。
- 上传平台前先 dry run 和 1 题 pilot，不用 `--yes` 跳过失败的验证。
- 对外包里只删不编：不能伪造没做过的结果。
