# 交付质检清单（合并版）

来源标签：[V3] Meta Vendor Data Spec V3.0；[V1] EdgeBench Vendor Dataset Requirements V1（2026-09-14）；[PQC] 01 过程小样质检 v0.2；[PACK] 02 轨迹后打包 prompt v0.1；[L:日期] 我们踩过的坑，见 lessons.md。历史 V2.4/SmolData 条目只在用户选择 Harbor 且仍适用时引用。

## A. 结构与声明（静态门，脚本 P/H/S/D 系列）

先记录用户选择的 `sforge-only` 或 `harbor`。以下格式专属条目只对所选格式适用，另一格式的字段不能作为替代证据。

| # | 条目 | 来源 |
|---|---|---|
| A1 | 每题一个目录，目录名 = 任务名；Meta V3.0 允许小写 snake_case 或 kebab-case。Harbor 按选择交解压目录，SForge 按选择交 task JSON 与两个 context | V3；V1；PACK |
| A2 | Harbor 模式：task.toml、instruction.md、独立 agent/verifier 构建定义、tests/test.sh 及适用 NOP/reference；SForge 模式：task JSON、BENCHMARK.yaml、contexts/work、contexts/judge | V1 §2；V3；PACK |
| A3 | task.toml 合法 TOML；`[task].name = "<org>/<目录名>"`；**无超过 2^53 的裸整数**（改字符串） | SMOL；L:2026-10-05 |
| A4 | Harbor 模式的 `[environment]`、`[agent]`、`[verifier]` 三段声明 network policy；SForge 模式声明等价 Work/Judge policy 并由运行探针验证 | V3；V1 §2 |
| A5 | agent、verifier 运行时 no-network；构建期网络若被允许必须说明并固定依赖；模型网关不算任务运行依赖 | V1 §2；V3；L:2026-09-25 |
| A6 | `reward_type` 显式，与 dataset 类型一致：EdgeBench bounded_continuous；其他数据集按其合同 | V3；V1 §4 |
| A7 | `[agent].user` 写明；**environment/Dockerfile 最后一个 stage 以 `USER root` 结尾**；只有 FROM 的 Dockerfile 要 `docker image inspect --format '{{.Config.User}}'` 查 base 默认用户 | SMOL；L:2026-10-02（三次沙箱卡死） |
| A8 | 基础镜像用公开可拉的 manifest-list digest；不写死 `--platform`；EdgeBench 任务支持 amd64 与 arm64 | V3；V1 §6；L:2026-10-05 |
| A9 | tests/test.sh 有 shebang、0755，**所有分支都写 `/logs/verifier/reward.txt`** | SMOL；L:2026-10-01 |
| A10 | 文件权限只有 0644 和 .sh 0755；无符号链接；无 .DS_Store / __MACOSX / __pycache__ / .pyc | SMOL；L:2026-09-19（pycache 混入） |
| A11 | 镜像里不给 agent 免密 sudo；apt 锁版本或 snapshot；wheels 带哈希 | L:2026-10-06；V1 §6 |
| A12 | SForge 任务另查：task json `internet: false`；三段声明在 json metadata 或 network-policy.toml；judge `eval_cmd` 指向的入口文件必须在 judge 构建上下文里；contexts/work 里没有 solution / reference / best_submission | V1；L:2026-10-06（sperner 入口缺失） |
| A13 | 每题有 SHA 清单，覆盖除清单自身外所有文件；改完任何文件重生成 | V1 |

## B. 验证器与隔离（静态门 V 系列 + 对抗门）

| # | 条目 | 来源 |
|---|---|---|
| B1 | verifier 不假设满权限：`setrlimit` 先 `getrlimit` 取硬上限只降不抬；`setuid` / `chown` / 锁目录包在 `if os.geteuid() == 0`；构建期不 `chmod 700 /tests` | L:2026-10-01（TBS crossframe 两次 verifier failed） |
| B2 | 降权不用 65534 / 65532（NPROC 按 uid 宿主机全局计数），用唯一高位 uid | L:2026-10-01 |
| B3 | numpy / scipy / torch 时固定 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` | L:2026-10-01（numerical_aware_posterior） |
| B4 | 杀子进程后要 wait 回收，或扫 /proc 时跳过 `State: Z`；别假设被杀进程立刻消失 | L:2026-09-14（TB3 163 题 golden 得 0） |
| B5 | 评分目录候选不可写，reward 最后写、0644；复制不跟随符号链接（copytree symlinks=False、extractall filter、O_NOFOLLOW） | L:2026-09-25（TB4 四题 777、telemetry 符号链接） |
| B6 | **判题镜像里除 root-only 私有目录外，任何位置不放参考解、校准包、隐藏用例**（不止 /tests/private，整个镜像都搜） | L:2026-10-02（sqlglot strong.tar 可读） |
| B7 | 候选代码在独立进程 / 独立容器 / WASI 跑，只收输入不收期望输出；父进程算分；verifier 不 import 候选代码 | L:2026-10-01（sqlglot 同进程读隐藏用例） |
| B8 | 出错路径也要有结构化结果（exit 2 不出分 = 平台 infrastructure failed） | L:2026-10-01 |
| B9 | 评分超时 ≥ 实测端到端耗时的 2 倍；对抗提交（卡住、超大文件）有全局截止 | L:2026-10-02 |
| B10 | 三种变体必须 0 分：删核心实现、硬编码输出、改测试；固定公开实例要防硬编码查表 | 框架维 8；L:2026-10-05（pace h_189 随包答案） |

## C. 运行门（要实跑，见 runtime_gate.md）

| # | 条目 | 来源 |
|---|---|---|
| C1 | Oracle 在 Linux x86 和 ARM 各得满分 ≥3 次，极差 0；只在 Mac 跑过不算（Docker VM 改写权限和身份）；ARM 可 QEMU，EC2 原生优先 | V2.4 S2；L:2026-09-25；G:2026-09-28 |
| C2 | NOP = **原样起始提交**，得最低分（空程序、换程序冒充不算；起始程序得分非零要改公式归零，不能改名叫"基线"） | V1；L:2026-10-01（itc 4.83、sqlglot 3.12） |
| C3 | 以非 root 身份跑一次 Oracle；artifacts 下不能有 agent 不可写的目录 | L:2026-09-25（Apéry、rna） |
| C4 | 在不回收僵尸的 PID1 下跑一次（裸 `sleep infinity` 保活再 docker exec） | L:2026-09-14 |
| C5 | 加 `--ulimit nproc=128:128 --ulimit nofile=200:200 --cap-drop SYS_RESOURCE` 跑一次 verifier | L:2026-10-01 |
| C6 | Harbor 包：SmolData dry run 零 error；传后 pilot 1 题过 preflight 再传其余；Oracle cancelled / verifier failed / infra failed 一律上报并定位，不 `--yes` 硬发 | SMOL；G:2026-10-01 |

## D. 难度与证据（难度门）

| # | 条目 | 来源 |
|---|---|---|
| D1 | 门槛按客户原文（TB4：Muse 4 次最多过 1 次；TBS：pass@3 < 0.7，3 次；EdgeBench：bounded_continuous，≥3h，偏好 12h），以 Slack 最新消息为准 | TB4；TBS；V1；Slack 9/22 Anlu |
| D2 | 同模型 ≥3 次完整运行，在最终版题目上跑，干净结束；最佳取 valid 提交；带逐次提交、提交数、2h/4h/6h 曲线 | V1；L:2026-10-05 |
| D3 | 不在 public 网络下跑；public 下的 pass@k 不采信 | G:2026-10-02（TB3 抓上游 PR） |
| D4 | 补点测试：修 1 个点就全过 = 假难度不交；好题要 ≥2 个独立卡点 | L:2026-09-25（Apéry、HarborLink、battery） |
| D5 | 曲线不能全平（EdgeBench sperner 三轮全 8）；也不能饱和（pace 6h 到参考 99.8%）；参考值不能是模型自己上一轮的结果 | L:2026-10-06 |
| D6 | 上限可达：100 分锚点要有实际能到的解（参考解、公开最优解），不能是数学端点 | V1；L:2026-10-04（pace 空集无效） |
| D7 | 每题有 Task Card（难度来源、卡点、题面↔测试映射、六道门记录） | G:2026-10-02，见 notes/rl-task-quality-framework.md §6 |
| D8 | 来源、再分发许可、对 EdgeBench / MLS-Bench / Terminal-Bench / GDPval 去重 | V1 §5 |

## E. 对外包清洁（集合门，脚本 P6 / P7）

| # | 条目 | 来源 |
|---|---|---|
| E1 | 无中文、全角标点、破折号（— –）、间隔号；无第一人称 | G 多次 |
| E2 | 无内部过程信息：未跑满、中断、重启、waive、excluded、provisional、candidate、COHORT、infrastructure_error、unscored、sanitized、redacted、checkpoint、revision、repairN、stage N、under review、reviewer must | L:2026-09-24 起每版都有 |
| E3 | 无工具名（Codex、Claude Code、Kimi）、供应商名、其他客户名、内部镜像名、本机路径、host.docker.internal、网关 | V3；PACK |
| E4 | 不随包：内部 QA / rubric 自评、pre-rollout / post-rollout 核对、运行器源码（runtime/）、provider fingerprint、旧版本身份文件 | L:2026-10-06 |
| E5 | 按所选格式检查外层命名、内部 ASCII 约定和稳定 Task ID；不把旧 V2.4 命名规则覆盖到 Meta V3.0 | V3；PACK |
| E6 | 报告数字从最终包程序化生成；回复里引用的凭证要么随包要么不写 | V2.4 Q2b |
| E7 | 质检报告、数据分布文档目前不给客户 | G:2026-09-24 |
| E8 | 五题目录结构统一 | L:2026-10-06 |

## F. 交付动作

| # | 条目 | 来源 |
|---|---|---|
| F1 | S3：`sample/` 与 `licensed/` 分开，`dataset=X/delivery=YYYY-MM-DD/task`；条件 PUT 不覆盖；传完远端核 file count + SHA；AccessDenied 先查自己 IAM 白名单 | V2.4 S7 |
| F2 | SmolData：dataset 类型与 reward_type 一致（现有 Edgebench dataset 是 binary，EdgeBench 要新建 bounded_continuous）；RL 环境不能走 unstructured | SMOL 2026-10-04 |
| F3 | Delivery Notification 格式（Meta 9/24）：Data Delivery / Date / S3 Path / Smoldata link / Data Volume / Cost per Task（留空）/ Notes（只写产能）/ CC Anlu、Reace、Zain、Monirul | V2.4 S8 |
| F4 | 上传和对客户通知由商务侧执行；生产侧只出包和报告 | G |
