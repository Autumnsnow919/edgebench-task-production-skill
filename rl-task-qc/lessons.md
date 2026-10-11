# 踩坑记录（按时间）

每条：日期，哪批数据，现象，根因，固化成的规则。给清单里的 [L:日期] 标签用。

| 日期 | 批次 | 现象 | 根因 | 规则 |
|---|---|---|---|---|
| 2026-09-14 | TB3 additional 175 题 | 163 题 golden 在 Meta 环境得 0 | verifier SIGKILL 子进程后扫 /proc 等 uid 清空，PID1 是裸 sleep 不回收僵尸 | B4：杀完要 wait，或跳过 State: Z，或 `--init` |
| 2026-09-15 | TB3 350 题 | Oracle 全部 cancelled | Dockerfile 以 USER atw 结尾，沙箱启动卡死 | A7 |
| 2026-09-19 | AutoResearch 1001 题 | 交付树里多了 1001 个 __pycache__ | 核验脚本 import 了交付树代码 | A10；核验脚本设 `sys.dont_write_bytecode`，junk 扫描放最后 |
| 2026-09-24 | TB4 / TBS 小样 | SmolData 拒收 | 文件权限 666 / 744；缺三段网络声明；reward_type 未声明 | A4 A6 A10 |
| 2026-09-24 | EdgeBench PathPulse | 种子文件 agent 可读 | judge 把 seeds_judge.txt 放在普通目录 | B6 |
| 2026-09-25 | TB4 Apéry、HarborLink；TBS battery | 模型 4 次全挂同一点，补上就全过 | 环境权限、题面没写的参数、stage_count 未说明 | D4 补点测试；C3 非 root 跑 Oracle |
| 2026-09-25 | TB4 四题 | 后台进程能改 reward；telemetry 候选能读答案生成器 | 评分目录 777；copytree 跟随符号链接 | B5 |
| 2026-09-25 | TB4 telemetry、IndexedDB | 题面说 offline，配置 public | 没对齐 | A5 |
| 2026-09-25 | 全部 | Mac 上 Oracle 过，Linux 上挂 | Docker Desktop VM 改写权限和用户身份 | C1 只认 Linux |
| 2026-10-01 | TBS crossframe、numerical | 本地过，SmolData verifier failed | setrlimit 抬硬上限，沙箱无 CAP_SYS_RESOURCE；BLAS 线程数 = 宿主机核数撞 NPROC | B1 B3 C5 |
| 2026-10-01 | TB4 5 题 | Oracle 5/5 cancelled | Dockerfile 以 USER agent 结尾 | A7 |
| 2026-10-01 | TB4 一题 | Oracle 卡一夜被 `--yes` 硬发 | 把平台快捷方式当标准动作 | C6 |
| 2026-10-01 | EdgeBench sqlglot | 恶意提交在同进程读隐藏用例伪造满分 | judge 和候选同进程 | B7 |
| 2026-10-01 | EdgeBench itc、sqlglot | NOP 4.83 / 3.12 分 | 起始程序得分非零，改名叫"基线" | C2 |
| 2026-10-02 | TB3 9 题 | Oracle cancelled 第三次 | FROM-only Dockerfile，base 默认用户非 root | A7 查 base 默认用户 |
| 2026-10-02 | TB3 82 题 42 次 run | 模型抓上游 PR 直接过 | agent public 网络 | D3 |
| 2026-10-02 | EdgeBench sqlglot | 参考解 strong.tar 候选可读 | 只保护了 /tests/private，calibration 在外面 | B6 整镜像搜 |
| 2026-10-03 | EdgeBench 五题包 | sperner 缺许可去重；pace 只 1 次运行 | 生产侧把它们排在范围外 | D2 D8 |
| 2026-10-04 | EdgeBench pace | 100 分到不了 | 归一化端点是空集，空集无效 | D6 |
| 2026-10-05 | EdgeBench itc | SmolData 上传"直接失败无记录" | task.toml 里 1609459200000000000 超过解析器整数范围 | A3 |
| 2026-10-05 | EdgeBench itc | 平台构建失败 | FROM edgebench-tools/python 私有镜像名 | A8 |
| 2026-10-05 | EdgeBench itc | 删过头 | 清内部信息时把 SHA 清单和逐次记录一起删了 | A13 D2；清理要有白名单不是黑名单 |
| 2026-10-06 | EdgeBench 五题包 | sperner judge 入口 sforge_eval.py 不在包里；收据哈希对不上 | 出包和跑分是两套源码 | A12；收据哈希必须对当前文件 |
| 2026-10-06 | EdgeBench sperner | 三轮 339 次提交全 8 分 | 只有最简单目标可过 | D5 |
| 2026-10-06 | EdgeBench pace | 三轮到参考 99.8% | 参考值是模型自己上一轮结果 | D5 |
| 2026-10-06 | EdgeBench 三题 | USER root 回退成 USER agent | 生产侧"统一"时没对清单 | A7；出包前跑 qc_static |
| 2026-10-06 | EdgeBench hospital | agent 免密 sudo | 调试遗留 | A11 |
| 2026-10-06 | EdgeBench 整包 | 运行器源码 runtime/、中文 QC 文件进客户包 | 重跑后整目录打包 | E4 |
