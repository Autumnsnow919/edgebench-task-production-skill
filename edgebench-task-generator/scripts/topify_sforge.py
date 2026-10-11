#!/usr/bin/env python3
"""Prepare a byte-bound SForge rollout adapter without inline setup payloads."""

from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


SAFE_ID = re.compile(r"[^a-z0-9_-]+")
DEFAULT_APT_LOCK = {
    "schema_version": 1,
    "base_reference": "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9",
    "distribution": "trixie",
    "snapshot": "20260919T000000Z",
    "packages": {
        "git": "1:2.47.3-0+deb13u1",
        "curl": "8.14.1-2+deb13u5",
        "jq": "1.7.1-6+deb13u3",
        "sudo": "1.9.16p2-3+deb13u2",
        "xz-utils": "5.8.1-1+deb13u1",
    },
    "package_index_sha256": {
        "linux/amd64": "7778d3e3f303b7ddb8ce0fe7c8d57473a076c6bf2e8f241f75421d2396352498",
        "linux/arm64": "be16b37b2f740f066ea93d35d56f04f94d63693e5a3eb421b052bf86fae75bcd",
    },
}


def _apt_lock(task: Path, base_reference: str) -> dict:
    lock_file = task / "apt-lock.json"
    if lock_file.is_symlink():
        raise ValueError("apt lock cannot be a symlink")
    if lock_file.exists():
        lock = json.loads(lock_file.read_text(encoding="utf-8"))
    elif base_reference == DEFAULT_APT_LOCK["base_reference"]:
        lock = DEFAULT_APT_LOCK
    else:
        raise ValueError("apt lock is required for this pinned Debian base image")
    keys = {"schema_version", "base_reference", "distribution", "snapshot", "packages", "package_index_sha256"}
    if (not isinstance(lock, dict) or set(lock) != keys
            or lock["schema_version"] != 1 or lock["base_reference"] != base_reference
            or not isinstance(lock["distribution"], str)
            or not re.fullmatch(r"[a-z0-9-]+", lock["distribution"])
            or not isinstance(lock["snapshot"], str)
            or not re.fullmatch(r"\d{8}T\d{6}Z", lock["snapshot"])
            or not isinstance(lock["packages"], dict)
            or set(lock["packages"]) != set(DEFAULT_APT_LOCK["packages"])
            or not all(isinstance(version, str) and re.fullmatch(r"[a-zA-Z0-9:~.+-]+", version)
                       for version in lock["packages"].values())
            or not isinstance(lock["package_index_sha256"], dict)
            or set(lock["package_index_sha256"]) != {"linux/amd64", "linux/arm64"}
            or not all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                       for digest in lock["package_index_sha256"].values())):
        raise ValueError("apt lock is incomplete or does not match the pinned base image")
    return lock


def adapter_task_id(task_id: str, task_sha256: str) -> str:
    stem = SAFE_ID.sub("-", task_id.lower()).strip("-")
    stem = re.sub(r"-+", "-", stem)
    return f"{stem[:72]}-{task_sha256[:12]}"


def snapshot_relative(value: str) -> str:
    """Resolve one declared file under /workspace, not an inferred JSON name."""
    if not isinstance(value, str) or not value.startswith("/workspace/"):
        raise ValueError("snapshot must be an absolute file under /workspace")
    path = PurePosixPath(value)
    if str(path) != value or ".." in path.parts:
        raise ValueError("snapshot contains an unsafe path component")
    relative = path.relative_to("/workspace").as_posix()
    if not relative or relative == ".":
        raise ValueError("snapshot must name a file")
    return relative


def make_task_definition(
    task_id: str,
    task_sha256: str,
    instruction: str,
    verifier_timeout: int,
    agent_cpu: int,
    agent_memory_mb: int,
    verifier_cpu: int,
    verifier_memory_mb: int,
    platform_name: str = "linux/amd64",
    scoring_metadata: dict | None = None,
    reference_mode: str = "reference",
    capability_category: str | None = None,
    resources: dict | None = None,
    snapshot_artifacts: list[str] | None = None,
) -> dict:
    identifier = adapter_task_id(task_id, task_sha256)
    image_tag = task_sha256[:12]
    artifacts = (["/workspace/solution.json"] if snapshot_artifacts is None
                 else snapshot_artifacts)
    if len(artifacts) != 1:
        raise ValueError("current SForge adapter supports one declared snapshot file")
    relative_artifact = snapshot_relative(artifacts[0])
    query = (
        instruction.rstrip()
        + f"\n\nWork only on `{artifacts[0]}`. Run the public local scorer often and "
           "call `sforge-submit` whenever the snapshot improves. Runtime internet is unavailable.\n"
    )
    return {
        "task_id": identifier,
        "name": f"EdgeBench task {task_id}",
        "category": capability_category or "Topify EdgeBench",
        "base_image": "python",
        "platform": platform_name,
        "internet": False,
        "cwd": "/workspace",
        "submit_paths": [relative_artifact],
        "submit_exclude": ["dev", "tools", "README.md", ".git"],
        "work": {
            "specs_dir": "/workspace",
            "agent_query": query,
            "image_tag": image_tag,
            "cpu_limit": agent_cpu,
            "mem_limit": f"{agent_memory_mb}m",
        },
        "judge": {
            "eval_cmd": "exec python3 -I /tests/sforge_eval.py",
            "eval_timeout": verifier_timeout,
            "parser": "structured_json",
            "score_direction": "maximize",
            "selection": "score_first",
            "rescale": {"kind": "linear", "lower": 0, "upper": 100},
            "image_tag": image_tag,
            "cpu_limit": verifier_cpu,
            "mem_limit": f"{verifier_memory_mb}m",
        },
        "metadata": {
            "authoring_task_id": task_id,
            "authoring_task_sha256": task_sha256,
            "adapter_role": "sforge-task",
            "reference_mode": reference_mode,
            "open_solution": reference_mode == "open-solution",
            "capability_category": capability_category,
            "scoring": {
                "reward_type": "bounded_continuous",
                "score_direction": "maximize",
                **(scoring_metadata or {}),
            },
            "resources": resources,
        },
    }


WRAPPER = r'''#!/usr/bin/env python3
import json
import math
import subprocess
import sys

environment = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}
process = subprocess.run(
    ["/tests/test.sh"],
    cwd="/workspace",
    env=environment,
    stdin=subprocess.DEVNULL,
    capture_output=True,
    check=False,
)
if process.returncode != 0:
    sys.stderr.write("verifier command failed; scoring was not completed\n")
    sys.exit(2)
try:
    source = json.loads(process.stdout)
except (ValueError, UnicodeDecodeError):
    sys.stderr.write("verifier report is not structured JSON\n")
    sys.exit(2)
if not isinstance(source, dict) or type(source.get("valid")) is not bool:
    sys.stderr.write("verifier report has no validity flag\n")
    sys.exit(2)
checks = source.get("checks")
if not isinstance(checks, list) or not checks or any(
    not isinstance(item, dict) or not isinstance(item.get("name"), str)
    or not item["name"] or type(item.get("pass")) is not bool for item in checks
):
    sys.stderr.write("verifier report has no named Boolean checks\n")
    sys.exit(2)
if any(item["name"] == "report_generation" for item in checks):
    sys.stderr.write("verifier reported an internal scoring exception\n")
    sys.exit(2)
score = source.get("total_score")
if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
    sys.stderr.write("verifier report has no finite bounded score\n")
    sys.exit(2)
if any(
    type(source.get(key)) not in (int, float) or not math.isfinite(source[key])
    for key in ("raw_metric", "normalized_score", "reward")
):
    sys.stderr.write("verifier report has no finite raw and normalized metrics\n")
    sys.exit(2)
valid = source["valid"]
details = []
for check in checks:
    details.append({
        "name": check["name"],
        "status": "PASSED" if check["pass"] else "FAILED",
    })
output = {
    "score": score,
    "valid": valid,
    "summary": "One-shot verifier report mapped without changing score semantics.",
    "details": details,
    "metrics": {
        "raw_metric": source.get("raw_metric"),
        "normalized_score": source.get("normalized_score"),
        "reward": source.get("reward"),
        "source_report_sha256": __import__("hashlib").sha256(process.stdout).hexdigest(),
    },
}
print(json.dumps(output, sort_keys=True, allow_nan=False))
'''


def _write_text(path: Path, value: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _image_names(definition: dict) -> tuple[str, str, str]:
    task_hash = definition["metadata"]["authoring_task_sha256"]
    benchmark = f"topify-{task_hash[:12]}"
    identifier = definition["task_id"]
    tag = task_hash[:12]
    return (
        benchmark,
        f"{benchmark}.work.{identifier}:{tag}",
        f"{benchmark}.judge.{identifier}:{tag}",
    )


def _create_contexts(adapter: Path, task: Path, base_reference: str,
                      artifact: str = "solution.json") -> tuple[Path, Path]:
    lock = _apt_lock(task, base_reference)
    snapshot = f"https://snapshot.debian.org/archive/debian/{lock['snapshot']}"
    packages = " ".join(f"{name}={version}" for name, version in sorted(lock["packages"].items()))
    source = "/tmp/pinned-apt.list"
    apt_options = (f"-o Dir::Etc::sourcelist={source} -o Dir::Etc::sourceparts=- "
                   "-o Acquire::Check-Valid-Until=false")
    work = adapter / "contexts/work"
    judge = adapter / "contexts/judge"
    work.mkdir(parents=True)
    judge.mkdir(parents=True)
    shutil.copytree(task / "environment/workspace", work / "workspace", symlinks=False)
    shutil.copyfile(task / "instruction.md", work / "README.md")
    shutil.copytree(task / "tests", judge / "tests", symlinks=False)
    (judge / "workspace").mkdir()
    starter = task / "environment/workspace" / artifact
    if not starter.is_file() or starter.is_symlink():
        raise ValueError("declared snapshot is not a regular starter file")
    judge_starter = judge / "workspace" / artifact
    judge_starter.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(starter, judge_starter)
    _write_text(judge / "sforge_eval.py", WRAPPER, executable=True)
    _write_text(work / "apt-lock.json", json.dumps(lock, sort_keys=True, indent=2) + "\n")
    _write_text(work / "verify_apt_index.py",
                Path(__file__).with_name("topify_apt_index.py").read_text(encoding="utf-8"))
    _write_text(
        work / "Dockerfile",
        f"""FROM {base_reference}
ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH
COPY apt-lock.json verify_apt_index.py /tmp/
RUN python3 /tmp/verify_apt_index.py "$TARGETARCH" && printf 'deb [check-valid-until=no] {snapshot} {lock['distribution']} main\\n' > {source} && rm -rf /var/lib/apt/lists/* && apt-get update {apt_options} && apt-get install -y --no-install-recommends {apt_options} {packages} && rm -rf /var/lib/apt/lists/* /tmp/apt-lock.json /tmp/verify_apt_index.py {source} && useradd -m -s /bin/bash agent && echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent
COPY workspace/ /workspace/
COPY README.md /workspace/README.md
RUN chown -R agent:agent /workspace \\
    && cd /workspace \\
    && su agent -c 'git init -q && git config user.email agent@local && git config user.name agent && git add -A && git commit -qm initial'
USER agent
WORKDIR /workspace
""",
    )
    _write_text(
        judge / "Dockerfile",
        f"""FROM {base_reference}
COPY tests/ /tests/
COPY workspace/ /workspace/
COPY sforge_eval.py /tests/sforge_eval.py
RUN chmod -R go-rwx /tests/private \\
    && chmod 755 /tests/test.sh /tests/evaluate.py /tests/sforge_eval.py
WORKDIR /workspace
""",
    )
    return work, judge


def _build_argv(task: Path, docker: str, architecture: str, context: Path, image: str) -> list[str]:
    import topify_build

    return [docker, "buildx", "build", "--platform", architecture,
            "--network", topify_build.declared_build_network(task),
            "--provenance=false", "--sbom=false", "--load", "--tag", image, str(context)]


def _make_archive(path: Path, snapshot: Path,
                  artifact: str = "solution.json") -> None:
    value = snapshot.read_bytes()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(snapshot_relative("/workspace/" + artifact))
        info.size = len(value)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(value))


def _extract_json_report(stdout: bytes) -> dict:
    text = stdout.decode("utf-8", errors="strict")
    decoder = json.JSONDecoder()
    reports = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "task_id" in value and "score" in value:
            reports.append(value)
    if not reports:
        raise ValueError("SForge eval did not emit a JSON score report")
    return reports[-1]


def prepare(
    workspace: str | Path,
    docker_executable: str = "docker",
    sforge_executable: str = "sforge",
    build_timeout_seconds: int = 1800,
) -> tuple[dict, int]:
    import topify_build
    import topify_common as common
    import topify_image_measure
    import topify_measure
    import topify_validate

    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    task_sha256 = topify_validate.task_sha256(task)
    validation = common.load_json(root / "evidence/harbor_validation.json")
    if validation.get("verdict") != "complete" or validation.get("task_sha256") != task_sha256:
        raise common.PipelineError("current Harbor validation is not complete")
    build = topify_image_measure._latest_complete_build(root, task_sha256)
    architecture = topify_image_measure.native_architecture()
    if architecture not in common.ARCHITECTURES:
        raise common.PipelineError("SForge preparation requires a native amd64 or arm64 host")
    attempt_number, attempt = common.next_attempt(root / "evidence/sforge-adapters")
    report = {
        "schema_version": 1,
        "attempt": attempt_number,
        "status": "incomplete",
        "state": "BUILT",
        "task_sha256": task_sha256,
        "build_attempt": build["attempt"],
        "architecture": architecture,
        "observed_host_architecture": architecture,
        "processes": [],
        "parity": [],
        "started_at": common.utc_now(),
    }

    def finish(reason: str, code: int = 2) -> tuple[dict, int]:
        report["reason"] = reason
        report["finished_at"] = common.utc_now()
        common.write_new_json(attempt / "summary.json", report)
        common.write_latest_json(attempt.parent / "latest.json", report)
        common.append_event(root, "sforge_adapter", attempt=attempt_number, status=report["status"])
        return report, code

    references = topify_build._base_references(task)
    if len(references) != 1:
        report["status"] = "failed"
        return finish("SForge adapter currently requires one shared pinned base image")
    request = metadata["request"]
    artifact = topify_validate.artifact_relative(task)
    suffix = Path(artifact).suffix
    definition = make_task_definition(
        task_id=request["task_id"],
        task_sha256=task_sha256,
        instruction=(task / "instruction.md").read_text(encoding="utf-8"),
        verifier_timeout=request["verifier_timeout_seconds"],
        agent_cpu=request["resources"]["agent_cpu"],
        agent_memory_mb=request["resources"]["agent_memory_mb"],
        verifier_cpu=request["resources"]["verifier_cpu"],
        verifier_memory_mb=request["resources"]["verifier_memory_mb"],
        platform_name=architecture,
        scoring_metadata=request["scoring"],
        reference_mode=request["reference_mode"],
        capability_category=request["category"],
        resources=request["resources"],
        snapshot_artifacts=[f"/workspace/{artifact}"],
    )
    adapter = attempt / "tasks"
    adapter.mkdir()
    work_context, judge_context = _create_contexts(adapter, task, references[0], artifact)
    benchmark, work_image, judge_image = _image_names(definition)
    benchmark_yaml = (
        f"name: {benchmark}\n"
        "base_images:\n"
        "  python:\n"
        f"    official_image: {references[0]}\n"
        "    extra_packages:\n"
        "      - git\n"
        "      - curl\n"
        "      - jq\n"
    )
    _write_text(adapter / "BENCHMARK.yaml", benchmark_yaml)
    common.write_new_json(adapter / f"{definition['task_id']}.json", definition)
    report["adapter"] = {
        "tasks_dir": str(adapter),
        "task_id": definition["task_id"],
        "task_definition_sha256": common.sha256_file(adapter / f"{definition['task_id']}.json"),
        "benchmark_sha256": common.sha256_file(adapter / "BENCHMARK.yaml"),
        "contexts_sha256": {"work": topify_validate.context_sha256(work_context),
                            "judge": topify_validate.context_sha256(judge_context)},
        "inline_setup_payloads": False,
    }

    for role, context, image in (
        ("work", work_context, work_image),
        ("judge", judge_context, judge_image),
    ):
        argv = _build_argv(task, docker_executable, architecture, context, image)
        process, _, _ = topify_build._run_process(
            argv, root, build_timeout_seconds, common.sanitized_env()
        )
        process["role"] = role
        report["processes"].append(process)
        if process.get("returncode") != 0:
            return finish(f"secure prebuilt SForge {role} image did not complete")

    try:
        nop_snapshot, nop_observation = topify_measure.run_nop_script(
            task,
            attempt / "nop-execution",
            request["verifier_timeout_seconds"],
        )
    except (common.PipelineError, OSError) as exc:
        return finish(str(exc))
    nop_observation["role"] = "nop-script"
    report["processes"].append(nop_observation)
    if request["reference_mode"] == "reference":
        try:
            strong_snapshot, reference_observation = topify_measure.run_reference_script(
                task,
                attempt / "reference-execution",
                request["verifier_timeout_seconds"],
            )
        except (common.PipelineError, OSError) as exc:
            return finish(str(exc))
        reference_observation["role"] = "reference-script"
        report["processes"].append(reference_observation)
    else:
        strong_snapshot = task / "tests/calibration" / f"strong{suffix}"
    snapshots = {
        "nop": nop_snapshot,
        "starter": task / "environment/workspace" / artifact,
        "interior": task / "tests/calibration" / f"interior{suffix}",
        "strong": strong_snapshot,
    }
    archives = attempt / "parity-archives"
    archives.mkdir()
    parity_durations = []
    for name, snapshot in snapshots.items():
        archive = archives / f"{name}.tar.gz"
        _make_archive(archive, snapshot, artifact)
        expected_run = attempt / "expected" / name
        expected, _ = topify_measure._run(
            task,
            snapshot,
            expected_run,
            request["verifier_timeout_seconds"],
        )
        argv = [
            sforge_executable,
            "--tasks-dir",
            str(adapter),
            "--log-dir",
            str(attempt / "sforge-logs"),
            "eval",
            "--task",
            definition["task_id"],
            "--archive",
            str(archive),
            "--run-id",
            f"parity-{name}",
            "--timeout",
            str(request["verifier_timeout_seconds"]),
            "--json",
        ]
        process, stdout, stderr = topify_build._run_process(
            argv,
            root,
            request["verifier_timeout_seconds"] + 60,
            common.sanitized_env(),
        )
        process["role"] = f"parity-{name}"
        raw = attempt / "sforge-evals"
        raw.mkdir(exist_ok=True)
        (raw / f"parity-{name}.stdout.json").write_bytes(stdout)
        (raw / f"parity-{name}.stderr.txt").write_bytes(stderr)
        report["processes"].append(process)
        if process.get("returncode") != 0:
            return finish(f"SForge parity evaluation did not complete for {name}")
        parity_durations.append(process["elapsed_seconds"])
        try:
            actual = _extract_json_report(stdout)
        except (UnicodeDecodeError, ValueError) as exc:
            report["status"] = "failed"
            return finish(str(exc))
        expected_score = float(expected["total_score"])
        actual_score = actual.get("score")
        actual_normalized = actual.get("score_0_100")
        match = actual_score == expected_score and actual_normalized == expected_score
        report["parity"].append({
            "name": name,
            "snapshot_sha256": common.sha256_file(snapshot),
            "archive_sha256": common.sha256_file(archive),
            "harbor_score": expected_score,
            "sforge_score": actual_score,
            "sforge_score_0_100": actual_normalized,
            "match": match,
        })
        if not match:
            report["status"] = "failed"
            return finish(f"SForge/Harbor score parity failed for {name}")

    strong_parity = next(item for item in report["parity"] if item["name"] == "strong")
    repeat_scores = [float(strong_parity["sforge_score_0_100"])]
    for index in range(2, 6):
        argv = [
            sforge_executable,
            "--tasks-dir", str(adapter),
            "--log-dir", str(attempt / "sforge-logs"),
            "eval", "--task", definition["task_id"],
            "--archive", str(archives / "strong.tar.gz"),
            "--run-id", f"parity-strong-repeat-{index}",
            "--timeout", str(request["verifier_timeout_seconds"]), "--json",
        ]
        process, stdout, stderr = topify_build._run_process(
            argv, root, request["verifier_timeout_seconds"] + 60, common.sanitized_env()
        )
        process["role"] = f"strong-repeat-{index}"
        (attempt / "sforge-evals" / f"strong-repeat-{index}.stdout.json").write_bytes(stdout)
        (attempt / "sforge-evals" / f"strong-repeat-{index}.stderr.txt").write_bytes(stderr)
        report["processes"].append(process)
        if process.get("returncode") != 0:
            return finish(f"SForge strong snapshot repeat {index} did not complete")
        parity_durations.append(process["elapsed_seconds"])
        try:
            repeated = _extract_json_report(stdout)
        except (UnicodeDecodeError, ValueError) as exc:
            report["status"] = "failed"
            return finish(str(exc))
        if repeated.get("valid") is not True:
            report["status"] = "failed"
            return finish(f"SForge strong repeat {index} was not a valid score")
        value = repeated.get("score_0_100")
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or not 0 <= value <= 100):
            report["status"] = "failed"
            return finish(f"SForge strong repeat {index} has no normalized score")
        repeat_scores.append(float(value))
    headroom = abs(float(request["scoring"]["upper"]) - float(request["scoring"]["baseline"]))
    report["sforge_reference_repeat"] = {
        "scores": repeat_scores,
        "range": max(repeat_scores) - min(repeat_scores),
        "relative_range": (max(repeat_scores) - min(repeat_scores)) / headroom,
        "snapshot_sha256": strong_parity["snapshot_sha256"],
        "archive_sha256": strong_parity["archive_sha256"],
        "mode": request["reference_mode"],
    }
    report["sforge_timing"] = {
        "cold_start_end_to_end_seconds": parity_durations[0],
        "typical_end_to_end_seconds": statistics.median(parity_durations),
        "maximum_end_to_end_seconds": max(parity_durations),
    }
    if any(topify_validate.context_sha256(context) != report["adapter"]["contexts_sha256"][role]
           for role, context in (("work", work_context), ("judge", judge_context))):
        return finish("SForge build/scoring contexts changed during preparation")
    report["status"] = "complete"
    report["state"] = "SFORGE_READY"
    report["images"] = {"work": work_image, "judge": judge_image}
    return finish(
        "SForge image parity and five same-snapshot scores recorded; main reviewer must assess noise against headroom.",
        code=0,
    )
