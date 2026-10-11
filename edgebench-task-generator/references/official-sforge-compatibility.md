# 官方 SForge 文档与本地交付实现核对

参考官方中文文档：

- [简介](https://bytedance-seed.github.io/EdgeBench/zh/guide/introduction.html)
- [Benchmark 与任务接入](https://bytedance-seed.github.io/EdgeBench/zh/tasks/integration-guide.html)
- [测试输出解析器](https://bytedance-seed.github.io/EdgeBench/zh/tasks/parsers.html)
- [迭代评测框架](https://bytedance-seed.github.io/EdgeBench/zh/features/iterative-evaluation.html)
- [网络隔离](https://bytedance-seed.github.io/EdgeBench/zh/features/network-isolation.html)
- [容器后端](https://bytedance-seed.github.io/EdgeBench/zh/configuration/container-backends.html)
- [CLI 参考](https://bytedance-seed.github.io/EdgeBench/zh/reference/cli.html)

本地核对版本为生产环境配置的 **SForge
1.1.0**。文档说明框架能力；客户复现还须用所交版本和真实任务重新执行，
不能把框架支持写成某道题已经过验收。
**业主后续已取消双入口及来源/去重/授权验收要求。以下 SForge 机制仅作
兼容性参考，不能重新变成本批额外门槛。** 两个独立 Dockerfile/构建上下文、
原生双架构构建和可复现输入仍按 EdgeBench DOCX 保留。

| 官方机制 | 现有生产线 | 需要完成的动作 |
|---|---|---|
| Work、Judge 双容器；`sforge serve` 收到归档后新建临时 Judge 容器 | 有独立 `contexts/work` 与 `contexts/judge`，并进行四点 parity | 验证交付镜像中 Work 看不到私有测试，Judge 对多份快照逐次新建且无状态。常驻的 host Judge Server **不是**常驻的判分容器。 |
| `BENCHMARK.yaml` + task JSON，`submit_paths` 可为多文件；可用 `game_mode` | 只支持 `solution.json`，未实现游戏模式及跨语言插件 | 按题族扩展提交接口与快照检查；不能把一个 miniature 覆盖六类任务。 |
| `structured_json` 可返回 `valid`、`score`、`pass_rate`、具名 `details`、`metrics`；`score_first` 适合连续优化 | Wrapper 已输出 score、valid、具名 details、raw/normalized metrics；解析或执行异常为评分失败 | 在真实 SForge 报告中验证评分和反馈可学习，区分无效提交与评分器错误；权重/通过率不得替代连续得分。 |
| Auto-eval 默认每 300 秒增加评分点但不反馈 Agent；Stop hook/auto-resume 帮助完整预算运行 | `rollout-start` 使用 300 秒间隔，`rollout-collect` 记录轨迹和三检查点 | 正式三次完整预算运行必须分别留存；若主动提交受限，auto-eval 不能冒充 Agent 的提交次数。 |
| `internet:false` / `--disable-internet` 在 Docker 上借 iptables 限制 Work 出站，仅放行 Judge Server 与模型 API | 已声明 `internet:false`，但尚无工作/评判网络实测 | 检查 Docker 的免密 `sudo -n iptables`、实际白名单和 IPv6；Judge 容器还需单独证明不能外连。K8s 则须验证 NetworkPolicy 被 CNI 执行。 |
| 同一 task 的 `work.setup_cmds` / `judge.setup_cmds` 可通过 `sforge build` 重建；仅有 `image_tag` 时 `sforge build` **不能**重建。两者同存时标签必须匹配 | 当前 JSON 使用 `image_tag`；owner 不要求第二个 `sforge build --task` 入口 | 验证实际镜像、独立 Work/Judge Dockerfiles/上下文、双架构构建及评分；不能将手动 `docker buildx` 命名为 `sforge build`，也不必做已取消的双入口。 |
| SForge 本地 builder 的 Docker build context 只含自动生成的 `setup_workspace.sh` 或 `setup_judge.sh` 与 Dockerfile | ZIP 随带的 `contexts/work` / `contexts/judge` **不会**自动进入 `sforge build` 的 Docker context | 因本批不要求此第二入口，使用交付的独立上下文构建和校验预构建镜像即可；不得声称随包 Dockerfile 自动参与 `sforge build`。 |
| Docker 后端适合小批量；大批次可用 Kubernetes 并从 registry 拉取镜像 | `rollout-batch` 默认最多 2 并发，作者 scout 可并行 | 保持任务资源协定与并发上限；不同 backend 的可达 Judge URL 和网络策略分别实测。 |

**任务实测边界（2026-09-28）：** 在一个 hospital v2 SForge 1.1.0 Docker
诊断中，实际三次 Judge 容器都是 `user=root`、`network_mode=bridge`；
Work 的 `internet:false` 不等于 Judge 无网。原任务在 x86_64 宿主上通过
QEMU 启动 arm64 Work/Judge 镜像，三份相同快照的 SForge 分数及结构化
指标与 amd64 一致。这只证明该任务的**模拟架构评分一致性**，不满足原生
arm64 验收，也不代表其他任务或 K8s 后端的隔离状态。复核步骤及收据边界
见 `qemu-and-independent-review.md`。

**当前实际阻断项：** 运行中的 Work/Judge 隔离、约定资源、同任务原生
amd64/arm64 评分及可复现上下文仍按对应实测收据判定。旧 `image_tag`
任务不因未带 `setup_cmds` 而自动失败；改变已评分镜像/评分语义仍须
重新评分。

`examples/sforge-build-sources.json` 仅为旧双入口设计模板，不在本批
生产路径中执行。仍需锁定实际使用的系统包依赖与 base image 的多架构
manifest-list digest，重建交付的 Work/Judge 上下文并校验各架构镜像身份；
没有实测时，reproducibility 保持 `incomplete`。
