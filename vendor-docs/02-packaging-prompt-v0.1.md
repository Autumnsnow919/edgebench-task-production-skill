# Task 与 Trajectory 标准交付打包 Skill

## 1. 目的

将已经完成的 Task、Trajectory 及相关验收材料整理、清理并打包为标准化、可复现、可对外交付的 Benchmark Sample。

打包过程必须同时满足：

1. **可复现**：接收方能够仅依赖交付内容构建、执行和验证 Task。
2. **最小化**：Task 中只保留复现和验证所必需的文件。
3. **轨迹真实性**：Trajectory 保留真实模型执行、评分和提交结果，不为了“美化”而修改事实。
4. **无内部泄露**：删除本机路径、内部人员、内部基础设施、内部状态、历史修改过程等不应对外暴露的信息。
5. **结构标准化**：最终交付目录必须遵守本文规定的 Benchmark Sample 目录规范。
6. **跨架构可复现**：需要构建镜像的 Task 必须验证 `linux/amd64` 和 `linux/arm64`。
7. **交付可读**：README、质检报告、目录导航和相对链接必须能够直接使用。

不得直接将工作目录整体压缩后交付。

必须先构造一个新的、干净的 staging / delivery 目录，再从源目录中选择性复制允许交付的文件。

# 2. 最终交付物

最终交付由以下四部分组成：

1. 一个 Benchmark Sample 主交付文件夹；
2. 一份英文质检报告；
3. 一份中文质检报告；
4. 一份外部中文 README。

注意：readme和质检报告将在之后生成，不在打包过程中生成。

因此本次打包阶段先交付主 Sample 文件夹中的 `tasks/` 与 `trajectories/` 等实际数据。主 `README.md`、两份质检报告及外部中文 README 未生成时，不得声称已完成本节所述的最终正式交付；后续生成时按本节名称和相对链接规则补齐。

四者必须使用相同的：

* Benchmark 名称；
* 交付日期。

日期统一使用：

```
YYYY-MM-DD
```

不得在外层交付名称中加入：

* Task ID；
* 历史版本号；
* 内部批次号；
* candidate / final / fixed 等状态后缀。

# 3. 标准交付目录

最终交付目录必须符合：

```
交付目录/
├── 【Morrow Lab】 {Benchmark} Sample {YYYY-MM-DD}/
│   ├── README.md
│   ├── tasks/
│   │   └── <task-id>/
│   ├── trajectories/
│   │   └── <task-id>/
│   ├── metadata/       # 可选，仅实际存在时保留
│   └── validation/     # 可选，仅实际存在时保留
├── 【Morrow Lab】 {Benchmark} Sample Quality Report {YYYY-MM-DD}.md
├── 【Morrow Lab】 {Benchmark} 小样质检报告 {YYYY-MM-DD}.md
└── 【Morrow Lab】 {Benchmark} README.zh-CN {YYYY-MM-DD}.md
```

其中：

```
{Benchmark}
```

必须替换为实际 Benchmark 名称。

```
{YYYY-MM-DD}
```

必须替换为本次正式交付日期。

```
<task-id>
```

必须替换为 Task 的稳定 ID。

# 4. 完整命名示例

以 `TB-science`、交付日期 `2026-09-23` 为例：

```
交付目录/
├── 【Morrow Lab】 TB-science Sample 2026-09-23/
│   ├── README.md
│   ├── tasks/
│   │   └── <task-id>/
│   └── trajectories/
│       └── <task-id>/
├── 【Morrow Lab】 TB-science Sample Quality Report 2026-09-23.md
├── 【Morrow Lab】 TB-science 小样质检报告 2026-09-23.md
└── 【Morrow Lab】 TB-science README.zh-CN 2026-09-23.md
```

如果实际存在可交付的 Metadata 或 Validation Evidence，可以扩展为：

```
交付目录/
├── 【Morrow Lab】 TB-science Sample 2026-09-23/
│   ├── README.md
│   ├── tasks/
│   │   ├── task_a/
│   │   └── task_b/
│   ├── trajectories/
│   │   ├── task_a/
│   │   └── task_b/
│   ├── metadata/
│   └── validation/
├── 【Morrow Lab】 TB-science Sample Quality Report 2026-09-23.md
├── 【Morrow Lab】 TB-science 小样质检报告 2026-09-23.md
└── 【Morrow Lab】 TB-science README.zh-CN 2026-09-23.md
```

不得为了满足模板而创建空的：

```
metadata/
validation/
trajectories/<task-id>/
```

目录。

# 5. 文档命名与标题

## 5.1 英文质检报告

文件名：

```
【Morrow Lab】 {Benchmark} Sample Quality Report {YYYY-MM-DD}.md
```

对于 TB-science：

```
【Morrow Lab】 TB-science Sample Quality Report 2026-09-23.md
```

文档一级标题：

```
Morrow Lab：TB-science Sample Quality Inspection Report
```

对于其他 Benchmark，将 `TB-science` 替换为实际 Benchmark 名称。

## 5.2 中文质检报告

文件名：

```
【Morrow Lab】 {Benchmark} 小样质检报告 {YYYY-MM-DD}.md
```

对于 TB-science：

```
【Morrow Lab】 TB-science 小样质检报告 2026-09-23.md
```

文档一级标题：

```
Morrow Lab：TB-science 小样质检报告
```

## 5.3 外部中文 README

文件名：

```
【Morrow Lab】 {Benchmark} README.zh-CN {YYYY-MM-DD}.md
```

该文件位于主 Sample 文件夹之外，与：

* Sample 主文件夹；
* 英文质检报告；
* 中文质检报告；

处于同一级目录。

## 5.4 用户指定内容优先

如果用户明确指定：

* 品牌；
* Benchmark 名称；
* 报告标题；
* 文件名；
* 日期；
* 外层目录名称；

则用户明确指定的内容优先于默认模板。

不得擅自将用户指定名称恢复成默认的 `Morrow Lab` 或 `TB-science`。

# 6. 文件名字符规则

需要区分**外层交付命名**和**主交付文件夹内部命名**。

## 6.1 外层允许品牌和中文名称

以下名称按模板保留：

```
【Morrow Lab】 ...
小样质检报告
README.zh-CN
```

因此外层交付物不受纯 ASCII 限制。

## 6.2 主交付文件夹内部

主交付文件夹内部的目录和文件名统一使用 ASCII 字符。

例如：

```
README.md
tasks/
trajectories/
metadata/
validation/
instruction.md
task.toml
Dockerfile
tests/
test.sh
```

不得在内部创建：

```
任务/
轨迹/
质检结果/
说明.md
最终版本/
```

等中文路径。

# 7. Task ID 命名规则

`tasks/` 下的 Task ID 目录必须满足：

```
^[a-z0-9_]+$
```

允许：

```
matrix_solver
protein_alignment
sql_debugging
```

禁止：

```
matrix-solver
MatrixSolver
matrix_solver_v2
matrix_solver_final
matrix_solver_candidate
matrix_solver_fixed
matrix_solver_2026_09_23
```

Task ID 中不得加入：

* 版本号；
* 日期；
* Review 状态；
* 修改状态；
* Delivery 状态；
* 内部批次。

重新交付同一道题时：

> 保持相同 Task ID，只修改外层交付日期。

不得通过修改 Task ID 表示新交付版本，因为改名可能导致系统将其识别为另一道题。

# 8. 主交付文件夹导航规则

主 Sample 文件夹承担实际数据交付功能。

基本结构：

```
交付文件夹/
├── README.md
├── tasks/
├── trajectories/
├── metadata/       # 可选
└── validation/     # 可选
```

各目录用途如下。

### `README.md`

英文项目说明和使用入口。

应帮助接收方快速理解：

* 本次交付是什么；
* 包含哪些 Task；
* 如何阅读 Task；
* 如何查看 Trajectory；
* 如何查看评分；
* Metadata / Validation Evidence 在哪里。

### `tasks/`

包含：

* 题面；
* Task 配置；
* 输入；
* Docker / Environment 定义；
* Verifier；
* Tests；
* 复现 Task 所必需的其他文件。

原则：

> `tasks/` 是最小可复现 Task 集合，不是开发工作目录的备份。

### `trajectories/`

按照 Task ID 保存真实：

* Trial；
* Trajectory；
* Model Output；
* Reward；
* Scoring Record；
* Submission；
* 其他确实属于轨迹的交付内容。

### `metadata/`

可选。

只有确实存在可交付 Metadata 时才创建。

可以用于：

* Trajectory 指标；
* Sample 索引；
* 导航；
* 聚合统计。

不得为了套用模板创建空目录。

### `validation/`

可选。

只有存在需要对外交付的检查结果或证据时才创建。

可以包含：

* 可复现性验证结果；
* Cross-architecture 验证结果；
* Package Validation Evidence。

内部 Debug Report 不得因为属于“验证”就自动进入该目录。

# 9. README 导航要求

主文件夹中的：

```
README.md
```

应至少能够引导接收方完成以下操作。

### 理解 Task

阅读：

```
README.md
tasks/<task-id>/instruction.md
```

### 查看模型解题过程

进入：

```
trajectories/<task-id>/
```

并查看实际存在的 Trial 目录。

不得虚构：

```
trial_1/
trial_2/
```

等不存在的轨迹目录。

### 复核模型评分

README 应指向实际存在的：

* Verifier；
* Test；
* Reward；
* Scoring Record；
* 相关验收程序。

不得声称存在实际上没有交付的评分证据。

### 查看 Trajectory 指标

如果存在：

```
metadata/
```

README 可以指向实际 Metadata 文件。

如果不存在，则不要写：

> See metadata for detailed trajectory metrics.

不得为了让 README 完整而虚构文件。

# 10. README 相对链接规则

必须根据 README 所处层级正确计算相对路径。

## 10.1 主文件夹内部英文 README

英文 README 位于：

```
【Morrow Lab】 {Benchmark} Sample {YYYY-MM-DD}/README.md
```

其链接以**主交付文件夹根目录**为基准。

例如：

```
[tasks](tasks/)
[trajectories](trajectories/)
[task instruction](tasks/example_task/instruction.md)
```

不要错误加入主文件夹自身名称。

错误：

```
[task](【Morrow Lab】 TB-science Sample 2026-09-23/tasks/example_task/)
```

正确：

```
[task](tasks/example_task/)
```

## 10.2 外部中文 README

中文 README 位于主 Sample 文件夹之外：

```
交付目录/
├── 【Morrow Lab】 TB-science Sample 2026-09-23/
└── 【Morrow Lab】 TB-science README.zh-CN 2026-09-23.md
```

因此访问 Task / Trajectory 时，相对链接必须增加主文件夹路径。

例如：

```
[Task 文件](./【Morrow%20Lab】%20TB-science%20Sample%202026-09-23/tasks/)
```

或使用目标 Markdown 环境支持的等价合法相对路径。

核心要求：

> 必须按照 README 的真实位置计算链接，而不是简单复制英文 README 中的相对链接。

# 11. 文件分类与最小化

打包前必须先建立文件 Inventory。

每个候选文件必须被归类为：

```
REQUIRED
TRAJECTORY_ONLY
DELIVERY_DOC
OPTIONAL_EVIDENCE
REMOVE
```

## 11.1 REQUIRED

进入：

```
tasks/<task-id>/
```

仅限于：

* Task 定义；
* Instruction；
* 构建环境；
* 执行环境；
* Verifier；
* Test；
* 必需 Fixture；
* 必需 Dataset；
* 必需依赖；
* 必需 Source Material；
* License 要求必须附带的文件。

## 11.2 TRAJECTORY_ONLY

进入：

```
trajectories/<task-id>/
```

包括真实：

* Agent Trajectory；
* Trial；
* History；
* Reward；
* Submission；
* Scoring Record；
* Model Interaction。

## 11.3 DELIVERY_DOC

包括：

```
主 README.md
英文质检报告
中文质检报告
外部中文 README
```

这些属于交付说明材料，不得混入 Task 本体。

## 11.4 OPTIONAL_EVIDENCE

根据实际内容进入：

```
metadata/
```

或：

```
validation/
```

但必须确实具有外部交付价值。

## 11.5 REMOVE

其余文件默认删除。

典型对象：

```
旧 Task 版本
Patch 历史
中间修改文件
内部 Review 报告
开发阶段质检草稿
Debug 日志
临时文件
编辑器文件
本地辅助脚本
内部 Notes
开发截图
未使用 Dataset
未使用 Snapshot
失败中间输出
Backup
```

不要把整个源工作目录直接复制到交付目录再依赖黑名单删除。

优先：

> Allow-list Copy，而不是 Deny-list Cleanup。

# 12. Metadata 清理

Task Metadata 不得包含：

```
allow_internet
顶层 name
authors
author
email
organization
内部审核状态
内部批准状态
内部负责人字段
内部流程字段
```

不得通过其他字段名重新加入等价信息。

## 12.1 `[metadata]` 描述边界

`[metadata]` 描述不得超过 Verifier 实际执行的检查。

Verifier 是最终事实来源。

例如 Verifier 只检查数值正确性，则不得声称还检查：

* 算法复杂度；
* 格式质量；
* 实现策略；
* Robustness；
* 外部数据来源；
* 性能指标。

原则：

> Metadata 描述的能力范围 <= Verifier 实际验证范围。

# 13. 对外副本清理策略

统一按照以下规则处理：


| 内容                   | 处理           |
| ---------------------- | -------------- |
| 模型名                 | 保留           |
| Harness                | 保留           |
| 版本号                 | 保留           |
| Reward                 | 原样保留       |
| 时间戳                 | 原样保留       |
| Submission             | 原样保留       |
| Completion Status      | 原样保留       |
| API 中断管理消息       | 删除该条       |
| 容器恢复消息           | 删除该条       |
| 剩余 Budget 消息       | 删除该条       |
| 本机路径               | 改写或删除     |
| 内部 IP                | 改写或删除     |
| 内部人员               | 改写或删除     |
| 中文内部信息           | 改写或删除     |
| 内部运行 Metadata      | 删除           |
| 无关 Tool / Debug 痕迹 | 删除           |
| 破坏数值的`[REDACTED]` | 删除受影响文件 |
| LICENSE 法定署名       | 保留           |

特别注意：

```
budget_exhausted
```

如果属于真实 Completion Status，则必须保留。

而：

```
provisional
candidate
waived
pending
```

如果属于内部 Review / Workflow 状态，则必须删除。

判断依据是**字段语义，而不是单纯关键词匹配**。

# 14. 全文件泄露扫描

必须扫描所有最终交付文件，包括：

```
TOML
JSON
JSONL
CSV
TXT
Markdown
Shell
Python
Dockerfile
Trajectory
History
Metadata
Validation
配置文件
保留日志
```

不能只检查 README。

## 14.1 中文

至少检测：

```
[\u4e00-\u9fff]
```

主 Sample 文件夹内部的数据文件中出现中文时必须逐项审查。

注意：

外部中文质检报告和中文 README 本身当然允许中文。

因此不能对整个最外层交付目录机械执行“零中文”规则。

应区分：

```
允许中文：
- 【Morrow Lab】 ... 小样质检报告 ...md
- 【Morrow Lab】 ... README.zh-CN ...md
- 用户明确要求保留的公开中文 Task 内容

重点检查中文泄露：
- tasks/
- trajectories/
- metadata/
- validation/
- 英文 README
- 英文 Quality Report
```

重点删除其中的内部中文信息。

## 14.2 本机路径

至少检测：

```
/Users/
/home/
/mnt/
/tmp/
C:\\
D:\\
file://
```

结合上下文区分本机路径与 Task 正式容器路径、公开来源 URL。不得修改轨迹中的真实命令来掩盖路径；无法安全清理的记录应整项移除并在后续交付说明中列明。

## 14.3 内部网络、身份与状态

检查私有 IP、内部主机名、对象存储地址、邮箱、本机用户名、内部人员和组织标识。公开来源材料中的署名及许可证要求的归属信息应保留。

检查 `provisional`、`candidate`、`waived`、`pending`、`quota`、`stopped`、`interrupted`、`unscored` 等词。仅删除无关的内部流程信息；真实任务完成状态、工具结果和 Reward 不得改写。

## 14.4 Unicode 字符

扫描 U+2013 和 U+2014。若需要转换，须保证运行语义和结构化数据解析后的值不变；对于不能等价转换的公开源材料或轨迹内容，记录例外，不得悄悄改变数值、命令或结论。

# 15. Trajectory 网络命令审计

检查轨迹和 history 中的 `curl`、`wget`、`git clone`、`pip install`、`WebFetch` 及等价网络调用，判断是否访问外网、泄露内部地址或违背 Task 的网络约束。不得为了通过扫描而改写真实历史命令。无法安全对外提供的整个文件应移除，并在后续 Trajectory 说明中注明。

# 16. Redaction 与证据真实性

保留原始 Reward、时间戳、模型、Harness、提交物及真实完成状态。不得把数值替换为 `[REDACTED]` 后继续当作有效证据；若原值无法可靠恢复，移除整个受影响文件并说明。历史运行没有生成的证据文件不得事后补造。

# 17. 文本编码、解析与权限

交付文本使用 UTF-8 无 BOM 和 LF。转换后逐文件解析 TOML、JSON、JSONL、CSV，以及实际存在的 YAML、XML 等结构化格式。检查 `__pycache__`、`*.pyc`、临时文件和编辑器备份未进入交付树。验证程序不得向交付树写入临时产物。

最终归档中的目录权限为 0755，Shell 脚本为 0755 且具有有效 shebang，其他普通文件为 0644。重新打包后检查归档内容与交付树一致。

# 18. Verifier Reward 输出协议

`tests/test.sh` 可以直接写 Reward，也可以调用 `tests/verify.py` 等程序写入。必须检查实际运行代码，而非只查看脚本名称或说明文档。

本标准允许同一次验证同时生成：

```text
/logs/verifier/reward.txt    # 单个有限数值，如 0.6
/logs/verifier/reward.json   # 同一数值的 JSON 表示，如 {"reward": 0.6}
```

两份文件必须来自同一次 Judge / verifier 结果，数值相同，且每次运行前清理旧文件；验证失败时两份均不得留下可被误读的旧分数。Harbor 在两份同时存在时优先读取 `reward.json`。若 Task 原本只使用其中一种格式，不能仅为使交付目录看起来齐全而静态补造另一种格式。

改变运行时 Reward 输出协议后，要验证新程序实际写出的文件和 Harbor 读取结果。历史轨迹及其原始 Reward 文件保持不动；如需将旧分数称为最终版 verifier 的评分，应按质检规则对保存的提交物重新评分，不能直接改名或复制历史分数。

# 19. 打包完成检查

核对稳定 Task ID、真实目录与相对链接、Task 必需文件、轨迹与提交物、Reward 和 Judge 证据。对候选文件完成 `REQUIRED`、`TRAJECTORY_ONLY`、`DELIVERY_DOC`、`OPTIONAL_EVIDENCE`、`REMOVE` 分类。记录未完成的跨架构实跑、README 或质检报告，不以静态检查冒充实跑。
