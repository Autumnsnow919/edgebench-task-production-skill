# 运行门：要实跑的检查

默认在 EC2 跑（x86 c7i + ARM c7g，用完 terminate 并报成本）；EC2 不可用再用本机 Docker，跑完关 Docker Desktop。只在 Mac 跑过的结果不作为交付依据。

## 1. Harbor 任务

```bash
# Oracle 与 NOP，每架构各 3 次
harbor run -p <tasks_dir> -t <task> -a oracle --n-trials 3
harbor run -p <tasks_dir> -t <task> -a nop --n-trials 1
# 另一架构：先按 harbor-local-dual-arch 记忆预拉 helper 镜像，再
DOCKER_DEFAULT_PLATFORM=linux/arm64 harbor run -p <tasks_dir> -t <task> -a oracle
```

读 `/logs/verifier/reward.txt` 和 verifier 产物，不看 agent 自报。

## 2. 客户沙箱条件复现（SmolData 实测）

```bash
# 低 ulimit、无 CAP_SYS_RESOURCE：verifier 必须仍能出分
docker run --rm --ulimit nproc=128:128 --ulimit nofile=200:200 --cap-drop SYS_RESOURCE <verifier_image> /tests/test.sh
# 不回收僵尸的 PID1
docker run -d --name z <image> sleep infinity && docker exec z /tests/test.sh
# 非 root 跑 Oracle（task.toml [agent].user 指定的身份）
docker exec -u <agent_user> <container> bash /solution/solve.sh
# base 默认用户
docker image inspect <base> --format '{{.Config.User}}'
```

## 3. 隔离与变体

- 以 agent 身份在 agent 镜像里：`find / -name '*solution*' -o -name '*reference*' -o -name '*golden*' 2>/dev/null`，`cat /tests/private/*` 必须失败。
- 判题镜像里：`find / -path '*private*' -prune -o \( -name '*.tar' -o -name '*calibration*' -o -name '*strong*' -o -name '*reference*' \) -print`，候选 uid 能读到的一律不合格。
- 三种变体各跑一次，都必须 0 分：删核心实现；硬编码预期输出（固定实例按名字 / hash 查表）；改测试文件。
- 符号链接探针：提交物里放指向私有文件的链接，judge 必须拒。

## 4. SForge 任务

```bash
docker buildx build --platform linux/amd64 --load -t <work> contexts/work
docker buildx build --platform linux/amd64 --load -t <judge> contexts/judge
# NOP → 参考 → NOP，三个不同 judge 容器，两架构各一遍；固定参考快照 5 次
python3 <adapter> eval --task <id> --archive nop/submission.tar.gz --json
```

NOP 必须是原样起始提交（前后 SHA 一致）；参考解 5 次同分；判题容器 `network_mode=none`、`cap_drop ALL`、出站探针被阻断。

## 5. SmolData

```bash
cd ~/.smoldata && ./smoldata-vendor-cli update
./smoldata-vendor-cli uploads preflight <tasks_dir>
./smoldata-vendor-cli uploads deliver <tasks_dir> --dataset <id> --delivery YYYY-MM-DD --evaluation-type <type> --dry-run
```

dry run 零 error 后：先传 1 题 pilot，`uploads validation <id> --wait` 过了再传其余。任何 cancelled / verifier failed / infrastructure failed 先定位（原因对照表见 lessons.md 和 checklist.md B1 到 B8）再处理，不 `--yes`。上传由商务侧执行。
