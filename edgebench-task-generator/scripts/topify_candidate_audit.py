#!/usr/bin/env python3
"""Receipt-backed, task-family-independent prototype difficulty screen.

The fractions here are authoring heuristics, not DOCX acceptance thresholds.
The local control runner does not execute an agent or certify SForge.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import statistics
import subprocess
import sys
import time

import topify_common as common
import topify_measure
import topify_review
import topify_validate


def _number(value: object, label: str, *, positive: bool = False) -> float:
    if not common.finite_number(value) or (positive and value <= 0):
        raise common.PipelineError(f"{label} must be a finite {'positive ' if positive else ''}number")
    return float(value)


def _file(path: Path, root: Path) -> Path:
    """Reject symlinks anywhere below the canonical workspace, including parents."""
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise common.PipelineError(f"path outside workspace: {path}") from exc
    if ".." in parts:
        raise common.PipelineError(f"unsafe evidence path traversal: {path}")
    candidate = root
    for part in parts:
        candidate /= part
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise common.PipelineError(f"missing evidence: {candidate}") from exc
        if stat.S_ISLNK(mode):
            raise common.PipelineError(f"symlink evidence path: {candidate}")
    if not stat.S_ISREG(candidate.lstat().st_mode):
        raise common.PipelineError(f"not a regular evidence file: {candidate}")
    return candidate


def _relative(root: Path, value: object, prefix: str) -> Path:
    if not isinstance(value, str):
        raise common.PipelineError("receipt path must be a string")
    parsed = PurePosixPath(value)
    if (parsed.is_absolute() or '..' in parsed.parts or '.' in parsed.parts
            or str(parsed) != value or not value.startswith(prefix + "/")):
        raise common.PipelineError(f"unsafe receipt path: {value}")
    return root.joinpath(*parsed.parts)


def _json(root: Path, path: Path) -> dict:
    return common.load_json(_file(path, root))


def _bytes(root: Path, path: Path) -> bytes:
    return _file(path, root).read_bytes()


def _hash(root: Path, path: Path) -> str:
    return common.sha256_file(_file(path, root))


def _latest_summary(directory: Path) -> Path | None:
    attempts = sorted(directory.glob("attempt-*"))
    return attempts[-1] / "summary.json" if attempts else None


def _same(actual: object, expected: object, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise common.PipelineError(f"{label} mismatch: receipt does not match current evidence")


def _process(root: Path, folder: Path, task: Path, task_hash: str,
             *, kind: str, snapshot: Path, solver: Path | None = None,
             allow_invalid: bool = False) -> tuple[dict, dict | None]:
    record = _json(root, folder / "process.json")
    for key, expected in (("snapshot_sha256", _hash(root, snapshot)),
                          ("evaluator_sha256", _hash(root, task / "tests/evaluate.py")),
                          ("task_sha256", task_hash)):
        # Measurement receipts predate task_sha256; their enclosing summary binds the task.
        if kind in {"measurement", "v2-score"} and key == "task_sha256":
            continue
        _same(record.get(key), expected, key)
    private_key = "instances_sha256" if kind in {"measurement", "v2-score"} else "private_sha256"
    _same(record.get(private_key), _hash(root, task / "tests/private/instances.json"), private_key)
    if solver is not None:
        _same(record.get("solver_sha256"), _hash(root, solver), "solver_sha256")
    if type(record.get("pid")) is not int or record["pid"] <= 0:
        raise common.PipelineError("process pid missing or invalid")
    _same(record.get("exit_code"), 0, "exit_code")
    if kind in {"measurement", "v2-score"} and ("timed_out" not in record or "parse_error" not in record):
        raise common.PipelineError("measurement timed_out/parse_error receipt missing")
    if record.get("timed_out", False) is not False or record.get("parse_error") is not None:
        raise common.PipelineError("process timed out or reported parse_error")
    _number(record.get("elapsed_seconds"), "elapsed_seconds", positive=True)
    argv = record.get("argv")
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        raise common.PipelineError("process argv missing")
    if kind == "search":
        if (len(argv) < 5 or argv[1:3] != ["-B", str(solver)]
                or argv[-2:] != ["--output", str(snapshot)]):
            raise common.PipelineError("search argv does not bind solver and snapshot")
    elif (len(argv) < 7 or argv[1:-4] not in (
              ["-I", str(task / "tests/evaluate.py")],
              ["-I", "-B", str(task / "tests/evaluate.py")],
          )
          or argv[-4:] != ["--solution", str(snapshot), "--instances",
                                str(task / "tests/private/instances.json")]):
        raise common.PipelineError("scorer argv does not bind snapshot, evaluator and private instances")
    stdout = _bytes(root, folder / "stdout.bin")
    stderr = _bytes(root, folder / "stderr.bin")
    _same(record.get("stdout_sha256"), hashlib.sha256(stdout).hexdigest(), "stdout_sha256")
    _same(record.get("stderr_sha256"), hashlib.sha256(stderr).hexdigest(), "stderr_sha256")
    if kind == "search":
        return record, None
    try:
        from topify_measure import _report
        parsed = _report(json.loads(stdout.decode("utf-8")))
    except (UnicodeError, ValueError, common.PipelineError) as exc:
        raise common.PipelineError(f"invalid scorer stdout: {exc}") from exc
    if not parsed["valid"] and not allow_invalid:
        raise common.PipelineError("scorer returned invalid snapshot")
    if kind in {"measurement", "v2-score"}:
        _same(_json(root, folder / "report.json"), parsed, "measurement report")
    return record, parsed


def _script(root: Path, folder: Path, task: Path, relative: str, snapshot: Path) -> dict:
    record = _json(root, folder / "process.json")
    _same(record.get("argv"), [str(task / relative)], "script argv")
    _same(record.get("script_sha256"), _hash(root, task / relative), "script_sha256")
    _same(record.get("snapshot_sha256"), _hash(root, snapshot), "script snapshot_sha256")
    for name in ("stdout", "stderr"):
        payload = _bytes(root, folder / f"{name}.bin")
        _same(record.get(f"{name}_sha256"), hashlib.sha256(payload).hexdigest(), name)
        _same(record.get(f"{name}_bytes"), len(payload), f"{name}_bytes")
    if type(record.get("pid")) is not int or record["pid"] <= 0:
        raise common.PipelineError("script pid missing")
    _same(record.get("exit_code"), 0, "script exit_code")
    _same(record.get("timed_out"), False, "script timed_out")
    _number(record.get("elapsed_seconds"), "script elapsed_seconds", positive=True)
    return record


def _measurement(root: Path, task: Path, task_hash: str, mode: str,
                   request: dict) -> tuple[dict, dict]:
    """Return receipt-verified observations; ladder eligibility is assessed separately."""
    path = _latest_summary(root / "evidence/measurements")
    if path is None:
        raise common.PipelineError("missing actual measurement attempt")
    summary = _json(root, path)
    _same(summary.get("task_sha256"), task_hash, "measurement task_sha256")
    _same(summary.get("attempt"), str(path.parent.relative_to(root)), "measurement attempt")
    _same(summary.get("reference_mode"), mode, "measurement reference_mode")
    runs = path.parent / "runs"
    artifact = topify_validate.artifact_relative(task)
    suffix = Path(artifact).suffix
    scores = {}
    snapshots = {
        "nop": runs / "nop-script/workspace" / artifact,
        "starter": task / "environment/workspace" / artifact,
        "interior": task / "tests/calibration" / f"interior{suffix}",
    }
    nop = _script(root, runs / "nop-script", task, "nop/nop.sh", snapshots["nop"])
    _same(summary.get("nop_script"), nop, "nop_script")
    if mode == "reference":
        strong = runs / "reference-script/workspace" / artifact
        script = _script(root, runs / "reference-script", task, "solution/solve.sh", strong)
        _same(summary.get("reference_script"), script, "reference_script")
        _same(summary.get("reference_submission_count"), 1, "reference_submission_count")
    else:
        strong = task / "tests/calibration" / f"strong{suffix}"
        _same(summary.get("reference_script"), None, "reference_script")
        _same(summary.get("reference_submission_count"), "open-solution", "reference_submission_count")
    durations = []
    for name, snapshot in snapshots.items():
        record, parsed = _process(root, runs / name, task, task_hash, kind="measurement",
                                   snapshot=snapshot, allow_invalid=name == "nop")
        scores[name] = float(parsed["total_score"])
        durations.append(record["elapsed_seconds"])
        _same(summary.get("scores", {}).get(name), parsed["total_score"], f"{name} score")
    repeats = []
    for index in range(1, 6):
        record, parsed = _process(root, runs / f"reference-{index}", task, task_hash,
                                  kind="measurement", snapshot=strong)
        repeats.append(float(parsed["total_score"]))
        durations.append(record["elapsed_seconds"])
    _same(summary.get("reference_repeat", {}).get("scores"), repeats, "repeat scores")
    _same(summary.get("reference_repeat", {}).get("snapshot_sha256"), _hash(root, strong), "repeat snapshot")
    score_range = max(repeats) - min(repeats)
    _same(summary.get("reference_repeat", {}).get("range"), score_range, "repeat range")
    declared_headroom = abs(request["scoring"]["upper"] - request["scoring"]["baseline"])
    _same(summary.get("reference_repeat", {}).get("relative_range"),
          score_range / declared_headroom, "relative_range")
    if mode == "reference":
        _same(summary.get("scores", {}).get("reference"), repeats[0], "reference score")
    else:
        _same(summary.get("scores", {}).get("reference"), "open-solution", "open-solution marker")
    _same(summary.get("timing", {}).get("cold_start_seconds"), durations[0], "timing cold start")
    _same(summary.get("timing", {}).get("typical_end_to_end_seconds"),
          statistics.median(durations), "timing median")
    _same(summary.get("timing", {}).get("maximum_end_to_end_seconds"),
          max(durations), "timing maximum")
    _same(summary.get("timing", {}).get("declared_timeout_seconds"),
          request["verifier_timeout_seconds"], "timing declared timeout")
    return {**scores, "reference": repeats[0] if mode == "reference" else None,
            "strong_calibration": repeats[0], "measured_headroom": repeats[0] - scores["nop"]}, {
                "scores": repeats, "range": score_range,
                "relative_range": score_range / declared_headroom,
            }


def _control(root: Path, task: Path, task_hash: str, summary_path: Path, result: object) -> dict:
    metric = {"iterations": result.get("iterations") if isinstance(result, dict) else None,
              "validated": False, "reasons": []}
    try:
        if not isinstance(result, dict):
            raise common.PipelineError("control result must be an object")
        folder = _relative(root, result.get("receipt"), "evidence/generic-controls")
        if folder.parent != summary_path.parent or folder.name != (
                "reference" if result.get("iterations") is None else f"iterations-{result.get('iterations')}"):
            raise common.PipelineError("receipt must be in the same control attempt")
        if result.get("iterations") is not None and (type(result["iterations"]) is not int
                                                        or result["iterations"] <= 0):
            raise common.PipelineError("iterations must be positive")
        snapshot = folder / topify_validate.artifact_relative(task)
        search_argv = _json(root, folder / "search/process.json").get("argv")
        if not isinstance(search_argv, list) or len(search_argv) < 3 or not isinstance(search_argv[2], str):
            raise common.PipelineError("search argv is missing the task-bound solver")
        solver = Path(search_argv[2])
        if not solver.is_absolute() or not solver.is_relative_to(task):
            raise common.PipelineError("solver must be an in-task executable")
        _file(solver, root)
        search, _ = _process(root, folder / "search", task, task_hash,
                             kind="search", snapshot=snapshot, solver=solver)
        argv = search["argv"]
        if result.get("iterations") is None:
            if "--generic-iterations" in argv:
                raise common.PipelineError("reference search contains generic iterations")
        elif argv[-4:-2] != ["--generic-iterations", str(result["iterations"])]:
            raise common.PipelineError("search iterations argv mismatch")
        scored, parsed = _process(root, folder / "heldout-score", task, task_hash,
                                   kind="score", snapshot=snapshot, solver=solver)
        _same(result.get("score"), parsed["total_score"], "score")
        _same(result.get("valid"), parsed["valid"], "valid")
        _same(result.get("search_seconds"), search["elapsed_seconds"], "search_seconds")
        _same(result.get("score_seconds"), scored["elapsed_seconds"], "score_seconds")
        if (folder / "heldout-report.json").exists():
            _same(_json(root, folder / "heldout-report.json"), parsed, "heldout report")
        metric.update(validated=True, score=parsed["total_score"], valid=parsed["valid"],
                      search_seconds=search["elapsed_seconds"], score_seconds=scored["elapsed_seconds"],
                      snapshot_sha256=_hash(root, snapshot), receipt=str(folder.relative_to(root)))
    except (common.PipelineError, OSError, TypeError, KeyError) as exc:
        metric["reasons"].append(str(exc))
    return metric


_CONTROL_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_SHELL_CHARS = frozenset(";&|<>`$\r\n\x00")


def _manifest(root: Path, path: Path, task_hash: str, *, budget_seconds: int) -> tuple[dict, list[dict]]:
    """Validate all controls before any generator starts or attempt is allocated."""
    if type(budget_seconds) is not int or budget_seconds < 3600:
        raise common.PipelineError("generic control requires a validated hours-long task budget")
    maximum_control_seconds = min(1800, max(300, int(budget_seconds * .05)))
    if not path.is_absolute():
        path = root / path
    _file(path, root)
    if path.is_relative_to(root / "task") or path.is_relative_to(root / "evidence/generic-controls"):
        raise common.PipelineError("manifest must live outside task and control attempts")
    manifest = _json(root, path)
    if set(manifest) != {"schema_version", "task_sha256", "controls"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise common.PipelineError("generic-control manifest schema_version must be 1")
    _same(manifest["task_sha256"], task_hash, "manifest task_sha256")
    controls = manifest["controls"]
    if not isinstance(controls, list) or not controls or len(controls) > 32:
        raise common.PipelineError("controls must contain 1 to 32 entries")
    ids = set()
    for entry in controls:
        if not isinstance(entry, dict) or set(entry) != {
                "control_id", "generator", "generator_sha256", "args", "timeout_seconds"}:
            raise common.PipelineError("invalid generic-control entry fields")
        control_id = entry["control_id"]
        if not isinstance(control_id, str) or not _CONTROL_ID.fullmatch(control_id) or control_id in ids:
            raise common.PipelineError("control_id must be a unique lowercase slug")
        ids.add(control_id)
        source = entry["generator"]
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise common.PipelineError("generator must be an absolute canonical script path")
        generator = Path(source)
        _file(generator, root)
        if (str(generator.resolve(strict=True)) != source or generator.is_relative_to(root / "task")
                or generator.is_relative_to(root / "evidence/generic-controls")):
            raise common.PipelineError("generator must be canonical and outside task/controls")
        if not isinstance(entry["generator_sha256"], str) or not common.HEX64.fullmatch(entry["generator_sha256"]):
            raise common.PipelineError("generator_sha256 must be lowercase SHA-256")
        _same(entry["generator_sha256"], _hash(root, generator), "generator_sha256")
        args = entry["args"]
        if (not isinstance(args, list) or not 1 <= len(args) <= 64
                or not all(isinstance(arg, str) and len(arg) <= 4096
                           and not any(char in _SHELL_CHARS for char in arg) for arg in args)
                or args.count("{output}") != 1 or any("{" in arg or "}" in arg for arg in args if arg != "{output}")):
            raise common.PipelineError("args must contain one standalone {output} without shell interpolation")
        timeout = entry["timeout_seconds"]
        if type(timeout) is not int or not 0 < timeout <= maximum_control_seconds:
            raise common.PipelineError(
                f"timeout_seconds must be an integer within 1..{maximum_control_seconds}")
    return manifest, controls


def _execute_generator(root: Path, task_hash: str, entry: dict, folder: Path,
                       artifact: str = "solution.json") -> dict:
    """Run one local author program with sanitized env, retaining failures verbatim."""
    folder.mkdir()
    search = folder / "search"
    search.mkdir()
    snapshot = folder / artifact
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-B", entry["generator"],
            *(str(snapshot) if value == "{output}" else value for value in entry["args"])]
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                   "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0", "WORKSPACE": str(folder)}
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=folder, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                               start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=entry["timeout_seconds"])
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    elapsed = time.monotonic() - started
    (search / "stdout.bin").write_bytes(stdout)
    (search / "stderr.bin").write_bytes(stderr)
    snapshot_safe = snapshot.is_file() and not snapshot.is_symlink()
    record = {"schema_version": 2, "kind": "generic-generator", "argv": argv,
              "cwd": str(folder), "env_keys": sorted(environment), "pid": process.pid,
              "exit_code": process.returncode, "elapsed_seconds": elapsed,
              "timed_out": timed_out, "timeout_seconds": entry["timeout_seconds"],
              "task_sha256": task_hash, "generator_sha256": entry["generator_sha256"],
              "snapshot_sha256": _hash(root, snapshot) if snapshot_safe else None,
              "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
              "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
              "stdout_bytes": len(stdout), "stderr_bytes": len(stderr)}
    common.write_new_json(search / "process.json", record)
    if timed_out or process.returncode != 0 or not snapshot_safe:
        raise common.PipelineError(f"{entry['control_id']} generator failed, timed out or lacks the declared snapshot")
    return record


def _valid_control_score(parsed: dict) -> None:
    """A lawful partial attempt may score zero or fail diagnostic checks."""
    if parsed["valid"] is not True:
        raise common.PipelineError("invalid generic control")
    score = float(parsed["total_score"])
    if not 0 <= score <= 100:
        raise common.PipelineError("generic control score must be bounded from 0 to 100")


def run_controls(workspace: Path, manifest_path: Path) -> tuple[dict, int]:
    """Run bounded, manifest-pinned local controls; return raw-evidence summary and exit code."""
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    if task.is_symlink() or not task.is_dir():
        raise common.PipelineError("unsafe task directory")
    task_hash = topify_validate.task_sha256(task)
    artifact = topify_validate.artifact_relative(task)
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    _, controls = _manifest(root, manifest_path, task_hash,
                            budget_seconds=metadata["request"]["interaction_budget_seconds"])
    validation = _json(root, root / "evidence/harbor_validation.json")
    _same(validation.get("task_sha256"), task_hash, "validation task_sha256")
    _same(validation.get("verdict"), "complete", "validation verdict")
    evidence = root / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        raise common.PipelineError("unsafe evidence directory")
    destination = evidence / "generic-controls"
    if destination.is_symlink() or destination.exists() and not destination.is_dir():
        raise common.PipelineError("unsafe generic-controls directory")
    _, attempt = common.next_attempt(destination)
    report = {"schema_version": 2, "status": "incomplete", "scope": "authoring-local-host",
              "task_sha256": task_hash, "manifest": str(manifest_path.relative_to(root)),
              "manifest_sha256": _hash(root, manifest_path),
              "attempt": str(attempt.relative_to(root)), "results": [], "errors": []}
    for entry in controls:
        folder = attempt / entry["control_id"]
        try:
            search = _execute_generator(root, task_hash, entry, folder, artifact)
            if topify_validate.task_sha256(task) != task_hash or _hash(root, Path(entry["generator"])) != entry["generator_sha256"]:
                raise common.PipelineError("task or generator changed during control execution")
            parsed, scored = topify_measure._run(task, folder / artifact, folder / "score",
                                                   metadata["request"]["verifier_timeout_seconds"])
            _valid_control_score(parsed)
            result = {"control_id": entry["control_id"], "receipt": str(folder.relative_to(root)),
                      "score": parsed["total_score"], "valid": parsed["valid"],
                      "search_seconds": search["elapsed_seconds"],
                      "score_seconds": scored["elapsed_seconds"],
                      "generator_sha256": entry["generator_sha256"],
                      "snapshot_sha256": search["snapshot_sha256"],
                      "search_process_sha256": _hash(root, folder / "search/process.json"),
                      "score_process_sha256": _hash(root, folder / "score/process.json"),
                      "score_report_sha256": _hash(root, folder / "score/report.json")}
            report["results"].append(result)
        except (common.PipelineError, OSError, ValueError, subprocess.SubprocessError) as exc:
            report["errors"].append({"control_id": entry["control_id"], "reason": str(exc),
                                     "receipt": str(folder.relative_to(root))})
            break
    try:
        if topify_validate.task_sha256(task) != task_hash or _hash(root, manifest_path) != report["manifest_sha256"]:
            report["errors"].append({"reason": "task or manifest changed during controls"})
    except (common.PipelineError, OSError) as exc:
        report["errors"].append({"reason": f"task or manifest changed during controls: {exc}"})
    if not report["errors"] and len(report["results"]) == len(controls):
        report["status"] = "measured"
    common.write_new_json(attempt / "summary.json", report)
    return report, 0 if report["status"] == "measured" else 2


def _control_v2(root: Path, task: Path, task_hash: str, summary_path: Path,
                entry: dict, result: object) -> dict:
    metric = {"control_id": entry["control_id"], "validated": False, "reasons": []}
    try:
        if not isinstance(result, dict):
            raise common.PipelineError("control result must be an object")
        folder = _relative(root, result.get("receipt"), "evidence/generic-controls")
        _same(folder, summary_path.parent / entry["control_id"], "control receipt path")
        _same(result.get("control_id"), entry["control_id"], "control_id")
        _same(result.get("generator_sha256"), entry["generator_sha256"], "generator_sha256")
        generator = Path(entry["generator"])
        _same(_hash(root, generator), entry["generator_sha256"], "generator script SHA-256")
        snapshot = folder / topify_validate.artifact_relative(task)
        _same(result.get("snapshot_sha256"), _hash(root, snapshot), "snapshot_sha256")
        for name in ("search", "score"):
            _same(result.get(f"{name}_process_sha256"), _hash(root, folder / name / "process.json"),
                  f"{name}_process_sha256")
        _same(result.get("score_report_sha256"), _hash(root, folder / "score/report.json"),
              "score_report_sha256")
        search = _json(root, folder / "search/process.json")
        _same(search.get("schema_version"), 2, "generator schema_version")
        _same(search.get("kind"), "generic-generator", "generator kind")
        _same(search.get("task_sha256"), task_hash, "generator task_sha256")
        _same(search.get("generator_sha256"), _hash(root, generator), "generator_sha256")
        _same(search.get("snapshot_sha256"), _hash(root, snapshot), "generator snapshot_sha256")
        _same(search.get("argv"), [sys.executable, "-B", str(generator),
              *(str(snapshot) if arg == "{output}" else arg for arg in entry["args"])], "generator argv")
        _same(search.get("cwd"), str(folder), "generator cwd")
        _same(search.get("env_keys"), sorted(["PATH", "LANG", "LC_ALL", "PYTHONHASHSEED", "WORKSPACE"]), "generator environment")
        _same(search.get("timeout_seconds"), entry["timeout_seconds"], "generator timeout_seconds")
        if type(search.get("pid")) is not int or search["pid"] <= 0:
            raise common.PipelineError("generator pid missing")
        _same(search.get("exit_code"), 0, "generator exit_code")
        _same(search.get("timed_out"), False, "generator timed_out")
        _number(search.get("elapsed_seconds"), "generator elapsed_seconds", positive=True)
        for name in ("stdout", "stderr"):
            stream = _bytes(root, folder / "search" / f"{name}.bin")
            _same(search.get(f"{name}_sha256"), hashlib.sha256(stream).hexdigest(), f"generator {name}_sha256")
            _same(search.get(f"{name}_bytes"), len(stream), f"generator {name}_bytes")
        scored, parsed = _process(root, folder / "score", task, task_hash,
                                   kind="v2-score", snapshot=snapshot)
        _same(scored.get("snapshot_path"), str(snapshot), "score snapshot_path")
        _same(scored.get("cwd"), str(task), "score cwd")
        _same(scored.get("argv", [None])[0], sys.executable, "score interpreter")
        _same(_json(root, folder / "score/report.json"), parsed, "scorer report")
        _valid_control_score(parsed)
        _same(result.get("score"), parsed["total_score"], "score")
        _same(result.get("valid"), parsed["valid"], "valid")
        _same(result.get("search_seconds"), search["elapsed_seconds"], "search_seconds")
        _same(result.get("score_seconds"), scored["elapsed_seconds"], "score_seconds")
        metric.update(validated=True, score=parsed["total_score"], valid=parsed["valid"],
                      search_seconds=search["elapsed_seconds"], score_seconds=scored["elapsed_seconds"],
                      snapshot_sha256=_hash(root, snapshot), receipt=str(folder.relative_to(root)))
    except (common.PipelineError, OSError, TypeError, KeyError, ValueError) as exc:
        metric["reasons"].append(str(exc))
    return metric


def _ablation_probes(root: Path, task_hash: str) -> tuple[list[dict], list[str]]:
    """Verify distinct, receipt-bound ablation probes against the current task bytes."""
    directory = root / "evidence/difficulty-probes"
    probes: list[dict] = []
    reasons: list[str] = []
    seen: set[tuple[str, str]] = set()
    if directory.is_symlink() or not directory.is_dir():
        return probes, ["missing difficulty-probe evidence directory"]
    for summary in sorted(directory.glob("attempt-*/summary.json")):
        try:
            probe = _json(root, summary)
            _same(probe.get("task_sha256"), task_hash, "probe task_sha256")
            _same(probe.get("status"), "complete", "probe status")
            if not isinstance(probe.get("causal_claim"), str) or not probe["causal_claim"].strip():
                raise common.PipelineError("probe causal claim missing")
            snapshots = probe.get("snapshots")
            if not isinstance(snapshots, dict) or set(snapshots) != {"integrated", "ablated"}:
                raise common.PipelineError("probe snapshots malformed")
            pair = []
            for name in ("integrated", "ablated"):
                entry = snapshots[name]
                if not isinstance(entry, dict) or entry.get("valid") is not True:
                    raise common.PipelineError(f"probe {name} snapshot invalid")
                _same(entry.get("report_sha256"),
                      _hash(root, summary.parent / name / "report.json"), f"{name} report_sha256")
                _same(entry.get("process_sha256"),
                      _hash(root, summary.parent / name / "process.json"), f"{name} process_sha256")
                if not common.HEX64.fullmatch(str(entry.get("snapshot_sha256"))):
                    raise common.PipelineError(f"probe {name} snapshot hash malformed")
                pair.append(entry["snapshot_sha256"])
            if pair[0] == pair[1]:
                raise common.PipelineError("probe compares identical snapshots")
            effect = probe.get("score_effect")
            if not common.finite_number(effect) or float(effect) <= 0:
                raise common.PipelineError("probe lacks a positive measured score effect")
            key = (pair[0], pair[1])
            if key in seen:
                raise common.PipelineError("duplicate ablation probe snapshots")
            seen.add(key)
            probes.append({"attempt": probe.get("attempt"), "causal_claim": probe["causal_claim"],
                           "score_effect": float(effect),
                           "integrated_sha256": pair[0], "ablated_sha256": pair[1],
                           "receipt": str(summary.relative_to(root)),
                           "receipt_sha256": _hash(root, summary)})
        except (common.PipelineError, OSError, TypeError, KeyError) as exc:
            reasons.append(f"{summary.parent.name}: {exc}")
    return probes, reasons


def _difficulty_pack(root: Path, task_hash: str) -> tuple[list[dict], list[str], str | None]:
    """Verify the latest declared mechanism pack and its immutable probe receipts."""
    path = root / "evidence/difficulty-packs/latest.json"
    reasons: list[str] = []
    probes: list[dict] = []
    if not path.is_file() or path.is_symlink():
        return probes, ["missing difficulty pack evidence"], None
    try:
        pack = _json(root, path)
        _same(pack.get("task_sha256"), task_hash, "difficulty pack task_sha256")
        if pack.get("status") not in {"partial", "complete"}:
            raise common.PipelineError("difficulty pack status malformed")
        attempt = pack.get("attempt")
        if type(attempt) is not int or attempt <= 0:
            raise common.PipelineError("difficulty pack attempt malformed")
        stored_path = root / f"evidence/difficulty-packs/attempt-{attempt:04d}/summary.json"
        _same(_json(root, stored_path), pack, "difficulty pack attempt receipt")
        mechanisms = pack.get("mechanisms")
        if not isinstance(mechanisms, list) or not 1 <= len(mechanisms) <= 10:
            raise common.PipelineError("difficulty pack mechanisms malformed")
        seen_ids = set()
        seen_pairs = set()
        for item in mechanisms:
            if not isinstance(item, dict):
                raise common.PipelineError("difficulty mechanism entry malformed")
            mechanism_id = item.get("mechanism_id")
            if not isinstance(mechanism_id, str) or not mechanism_id.strip() or mechanism_id in seen_ids:
                raise common.PipelineError("difficulty mechanism identity malformed")
            seen_ids.add(mechanism_id)
            if item.get("measured") is not True:
                reasons.append(f"{mechanism_id}: declared mechanism lacks a complete probe")
                continue
            receipt_value = item.get("probe_receipt")
            if not isinstance(receipt_value, str):
                raise common.PipelineError(f"{mechanism_id}: probe receipt missing")
            receipt = _relative(root, receipt_value, "evidence/difficulty-probes")
            if receipt.name != "summary.json" or not receipt.parent.name.startswith("attempt-"):
                raise common.PipelineError(f"{mechanism_id}: probe receipt path malformed")
            receipt_hash = _hash(root, receipt)
            _same(item.get("probe_receipt_sha256"), receipt_hash, f"{mechanism_id} probe_receipt_sha256")
            probe = _json(root, receipt)
            _same(probe.get("task_sha256"), task_hash, f"{mechanism_id} probe task_sha256")
            _same(probe.get("status"), "complete", f"{mechanism_id} probe status")
            _same(probe.get("causal_claim"), item.get("causal_claim"), f"{mechanism_id} causal claim")
            snapshots = probe.get("snapshots")
            if not isinstance(snapshots, dict) or set(snapshots) != {"integrated", "ablated"}:
                raise common.PipelineError(f"{mechanism_id} probe snapshots malformed")
            pair = []
            for name in ("integrated", "ablated"):
                entry = snapshots[name]
                if not isinstance(entry, dict) or entry.get("valid") is not True:
                    raise common.PipelineError(f"{mechanism_id} probe {name} snapshot invalid")
                _same(entry.get("report_sha256"),
                      _hash(root, receipt.parent / name / "report.json"), f"{mechanism_id} {name} report_sha256")
                _same(entry.get("process_sha256"),
                      _hash(root, receipt.parent / name / "process.json"), f"{mechanism_id} {name} process_sha256")
                digest = entry.get("snapshot_sha256")
                if not common.HEX64.fullmatch(str(digest)):
                    raise common.PipelineError(f"{mechanism_id} {name} snapshot hash malformed")
                pair.append(str(digest))
            if pair[0] == pair[1]:
                raise common.PipelineError(f"{mechanism_id} compares identical snapshots")
            if (pair[0] != item.get("integrated_snapshot_sha256")
                    or pair[1] != item.get("ablated_snapshot_sha256")):
                raise common.PipelineError(f"{mechanism_id} probe is not bound to its declared snapshots")
            integrated_path = item.get("integrated_snapshot")
            ablated_path = item.get("ablated_snapshot")
            _, integrated_hash = topify_review._task_file(root, integrated_path, "integrated")
            _, ablated_hash = topify_review._ablation_snapshot(root, ablated_path)
            _same(integrated_hash, pair[0], f"{mechanism_id} integrated snapshot bytes")
            _same(ablated_hash, pair[1], f"{mechanism_id} ablated snapshot bytes")
            if tuple(pair) in seen_pairs:
                raise common.PipelineError("duplicate difficulty mechanism snapshot pair")
            seen_pairs.add(tuple(pair))
            effect = probe.get("score_effect")
            if (not common.finite_number(effect) or float(effect) <= 0
                    or float(effect) != float(item.get("score_effect"))):
                raise common.PipelineError(f"{mechanism_id} probe effect mismatch")
            probes.append({"mechanism_id": mechanism_id,
                           "kind": item.get("kind"),
                           "causal_claim": probe["causal_claim"],
                           "score_effect": float(effect),
                           "integrated_sha256": pair[0], "ablated_sha256": pair[1],
                           "receipt": receipt_value,
                           "receipt_sha256": receipt_hash})
    except (common.PipelineError, OSError, TypeError, KeyError) as exc:
        reasons.append(f"difficulty pack: {exc}")
    return probes, reasons, common.sha256_file(path) if path.is_file() and not path.is_symlink() else None


def assess(workspace: Path, controls_path: Path | None = None, *,
           max_budget_fraction: float = .01, near_reference_fraction: float = .85,
           min_ablations: int = 3, min_ablation_effect_fraction: float = .02,
           min_combined_effect_fraction: float = .1) -> tuple[dict, int]:
    """Audit the current task's local receipts; append a report on safe inputs only."""
    for name, value in (("max_budget_fraction", max_budget_fraction),
                        ("near_reference_fraction", near_reference_fraction),
                        ("min_ablation_effect_fraction", min_ablation_effect_fraction),
                        ("min_combined_effect_fraction", min_combined_effect_fraction)):
        if not 0 < _number(value, name) <= 1:
            raise common.PipelineError(f"{name} must be within (0, 1]")
    if type(min_ablations) is not int or min_ablations < 1:
        raise common.PipelineError("min_ablations must be a positive integer")
    root, metadata = common.load_workspace(workspace)
    task = root / "task"
    if task.is_symlink() or not task.is_dir():
        raise common.PipelineError("unsafe task directory")
    task_hash = topify_validate.task_sha256(task)
    mode = metadata["request"]["reference_mode"]
    control_dir = root / "evidence/generic-controls"
    if controls_path is None:
        candidate = _latest_summary(control_dir)
        controls_path = candidate if candidate is not None and candidate.is_file() else None
    if controls_path is not None:
        controls_path = Path(controls_path)
        if not controls_path.is_absolute():
            controls_path = root / controls_path
        if (controls_path.parent.parent != control_dir or controls_path.name != "summary.json"
                or not controls_path.parent.name.startswith("attempt-")):
            raise common.PipelineError("controls_path must be an in-workspace generic-controls attempt summary")
        if controls_path.exists() or controls_path.is_symlink():
            _file(controls_path, root)
    report = {
        "schema_version": 1, "task_sha256": task_hash, "task_id": metadata["task_id"],
        "calibration_mode": mode, "status": "incomplete", "reasons": [], "controls": [],
        "cheap_saturation_observed": None,
        "cheap_saturation_status": "not_evaluable", "cheap_saturation_reasons": [],
        "cheap_saturation_scope": "authoring-local-host; Work-image reachability needs independent review",
        "heuristics": {"kind": "configurable authoring heuristic; not DOCX acceptance thresholds",
                       "max_budget_fraction": max_budget_fraction,
                       "near_reference_fraction": near_reference_fraction,
                       "min_ablations": min_ablations,
                       "min_ablation_effect_fraction": min_ablation_effect_fraction,
                       "min_combined_effect_fraction": min_combined_effect_fraction},
        "budget_seconds": metadata["request"]["interaction_budget_seconds"],
        "controls_summary_sha256": _hash(root, controls_path) if controls_path else None,
    }
    try:
        validation = _json(root, root / "evidence/harbor_validation.json")
        report["validation_sha256"] = _hash(root, root / "evidence/harbor_validation.json")
        _same(validation.get("task_sha256"), task_hash, "validation task_sha256")
        _same(validation.get("verdict"), "complete", "validation verdict")
    except (common.PipelineError, OSError, TypeError, KeyError) as exc:
        report["reasons"].append(f"validation: {exc}")
    try:
        ladder, repeat = _measurement(root, task, task_hash, mode, metadata["request"])
        report["ladder"], report["reference_repeat"] = ladder, repeat
        report["measurement_summary_sha256"] = _hash(root, _latest_summary(root / "evidence/measurements"))
        # Retain verified scores and repeat evidence even if this eligibility
        # check fails. A lawful weak starter may tie NOP; the interior must
        # still demonstrate strict positive headroom below the strong anchor.
        _same(ladder["nop"], float(metadata["request"]["scoring"]["baseline"]), "nop declared baseline")
        if not ladder["nop"] <= ladder["starter"] < ladder["interior"] < ladder["strong_calibration"]:
            raise common.PipelineError(
                "nop/starter/interior/strong ladder lacks headroom: "
                "require NOP <= starter < interior < strong")
    except (common.PipelineError, OSError, TypeError, KeyError) as exc:
        report["reasons"].append(f"measurement/ladder: {exc}")
        if mode == "open-solution" and "ladder" not in report:
            report["reasons"].append("measured strong calibration anchor unavailable")
    probes, probe_reasons, pack_sha256 = _difficulty_pack(root, task_hash)
    report["difficulty_pack_sha256"] = pack_sha256
    report["ablation_probes"] = probes
    if probe_reasons:
        report["reasons"].extend(f"ablation probe {reason}" for reason in probe_reasons)
    report["required_ablations"] = min_ablations
    report["required_ablation_effect_fraction"] = min_ablation_effect_fraction
    report["required_combined_effect_fraction"] = min_combined_effect_fraction
    headroom = None
    if "ladder" in report and common.finite_number(report["ladder"].get("measured_headroom")):
        headroom = float(report["ladder"]["measured_headroom"])
        if headroom <= 0:
            headroom = None
    effective = []
    if headroom is not None:
        effective = [probe for probe in probes
                     if probe["score_effect"] >= min_ablation_effect_fraction * headroom]
    report["ablation_evaluation_status"] = "evaluated" if headroom is not None else "not_evaluable"
    report["effective_ablations"] = len(effective) if headroom is not None else None
    report["combined_ablation_effect"] = (
        sum(probe["score_effect"] for probe in effective) if headroom is not None else None)
    declared_control = None
    design_path = root / "task_design.json"
    if design_path.is_file() and not design_path.is_symlink():
        design = _json(root, design_path)
        cheap = design.get("cheap_baseline")
        if isinstance(cheap, dict) and isinstance(cheap.get("control_id"), str):
            declared_control = cheap["control_id"]
    report["declared_cheap_baseline_control"] = declared_control
    if controls_path is None:
        report["reasons"].append("missing generic control receipts")
    else:
        try:
            summary = _json(root, controls_path)
            _same(summary.get("task_sha256"), task_hash, "control task_sha256")
            _same(summary.get("status"), "measured", "control status")
            results = summary.get("results")
            if "schema_version" in summary and (type(summary["schema_version"]) is not int or summary["schema_version"] != 2):
                raise common.PipelineError("control schema_version must be integer 2")
            if summary.get("schema_version") == 2:
                _same(summary.get("scope"), "authoring-local-host", "control scope")
                _same(summary.get("attempt"), str(controls_path.parent.relative_to(root)), "control attempt")
                _same(summary.get("errors"), [], "control errors")
                if not isinstance(results, list):
                    raise common.PipelineError("generic-control results missing")
                manifest_value = summary.get("manifest")
                if not isinstance(manifest_value, str):
                    raise common.PipelineError("manifest path missing")
                manifest_path = root / manifest_value
                if (not manifest_path.is_relative_to(root) or ".." in manifest_path.relative_to(root).parts
                        or str(manifest_path.relative_to(root)) != manifest_value):
                    raise common.PipelineError("unsafe manifest path")
                _same(summary.get("manifest_sha256"), _hash(root, manifest_path), "manifest_sha256")
                _, entries = _manifest(root, manifest_path, task_hash,
                                       budget_seconds=report["budget_seconds"])
                if len(results) != len(entries):
                    raise common.PipelineError("generic-control results are incomplete")
                report["control_protocol"] = 2
                report["controls"] = [
                    _control_v2(root, task, task_hash, controls_path, entry, result)
                    for entry, result in zip(entries, results)
                ]
                if any(not item["validated"] for item in report["controls"]):
                    report["reasons"].append("one or more generic-control receipts invalid")
            elif not isinstance(results, list) or not results or (mode == "reference" and not isinstance(summary.get("reference"), dict)):
                raise common.PipelineError("control reference/results missing")
            else:
                reference = (_control(root, task, task_hash, controls_path, summary["reference"])
                             if isinstance(summary.get("reference"), dict) else None)
                if reference is not None:
                    report["control_reference" if mode == "reference" else "control_calibration"] = reference
                report["controls"] = [_control(root, task, task_hash, controls_path, item) for item in results]
                if (reference is not None and not reference["validated"] or
                        any(not item["validated"] for item in report["controls"])):
                    report["reasons"].append("one or more control receipts invalid")
                if "ladder" in report and reference is not None and reference["validated"]:
                    anchor = report["ladder"]["strong_calibration"]
                    if mode == "reference":
                        _same(reference["score"], anchor, "measured reference anchor")
                        if "reference_anchor" in summary:
                            _same(summary["reference_anchor"], anchor, "declared reference anchor")
                    else:
                        # The strong snapshot is a measured calibration anchor, never a reference solution.
                        _same(reference["score"], anchor, "measured strong calibration anchor")
        except (common.PipelineError, OSError, TypeError, KeyError) as exc:
            report["reasons"].append(f"control/anchor: {exc}")
    # Do not let a missing difficulty pack hide an already verified cheap
    # saturation signal. This is an author-host observation, not proof that
    # the same control exists inside the delivered offline Work image.
    validated_controls = [item for item in report["controls"] if item.get("validated") is True]
    if headroom is None:
        report["cheap_saturation_reasons"].append("verified positive measured headroom unavailable")
    elif not validated_controls:
        report["cheap_saturation_reasons"].append("no validated generic control receipts")
    else:
        anchor = report["ladder"]["strong_calibration"]
        nop = report["ladder"]["nop"]
        limit = report["budget_seconds"] * max_budget_fraction
        for item in validated_controls:
            item["gain_fraction"] = (item["score"] - nop) / headroom
            item["total_seconds"] = item["search_seconds"] + item["score_seconds"]
            item["budget_fraction"] = item["total_seconds"] / report["budget_seconds"]
            if item["score"] > anchor:
                item["diagnostic"] = "above measured anchor: revise"
            elif item["total_seconds"] <= limit and item["gain_fraction"] >= near_reference_fraction:
                item["diagnostic"] = "cheap generic search closes near-reference headroom: revise"
            else:
                item["diagnostic"] = "below authoring heuristic: review needed"
        saturated = any(item["diagnostic"].endswith("revise") for item in validated_controls)
        if saturated:
            report["cheap_saturation_observed"] = True
            report["cheap_saturation_status"] = "observed"
        elif len(validated_controls) == len(report["controls"]) and not any(
                reason.startswith("control/anchor:") for reason in report["reasons"]):
            report["cheap_saturation_observed"] = False
            report["cheap_saturation_status"] = "not_observed"
        else:
            report["cheap_saturation_reasons"].append("generic control evidence is incomplete or invalid")
    if not report["reasons"]:
        if report["cheap_saturation_observed"]:
            # Cheap saturation already rejects the task on its own evidence.
            report["status"] = "revise"
        else:
            gaps = []
            if headroom is None:
                gaps.append("measured headroom unavailable for ablation effect floors")
            if len(effective) < min_ablations:
                gaps.append(
                    f"need at least {min_ablations} distinct measured ablation probes each "
                    f"removing at least {min_ablation_effect_fraction:.0%} of measured headroom, "
                    f"found {len(effective)} of {len(probes)}")
            elif (headroom is not None and
                  report["combined_ablation_effect"] < min_combined_effect_fraction * headroom):
                gaps.append(
                    f"combined ablation effect must cover at least "
                    f"{min_combined_effect_fraction:.0%} of measured headroom, covered "
                    f"{report['combined_ablation_effect'] / headroom:.1%}")
            if declared_control is not None and not any(
                item.get("control_id") == declared_control and item["validated"]
                for item in report["controls"]
            ):
                gaps.append(
                    f"declared cheap baseline control '{declared_control}' "
                    "was not measured as a validated generic control")
            if gaps:
                report["status"] = "incomplete"
                report["reasons"].extend(gaps)
            else:
                report["status"] = "review-needed"
        report["reasons"].append("configurable authoring heuristic; human Q1–Q7 review required")
    # Validate the destination before creating it; immutable attempts preserve raw inputs.
    evidence = root / "evidence"
    if evidence.is_symlink() or not evidence.is_dir():
        raise common.PipelineError("unsafe evidence directory")
    destination = evidence / "candidate-audits"
    if destination.is_symlink():
        raise common.PipelineError("unsafe candidate-audits directory")
    number, attempt = common.next_attempt(destination)
    report["attempt"] = number
    report["report_payload_sha256"] = common.canonical_sha256(report)
    common.write_new_json(attempt / "report.json", report)
    # A screen never authorizes promotion; even clean evidence needs human review.
    return report, 2


def current_diagnostic(workspace: Path, task_sha256: str) -> dict:
    """Return the current bound diagnostic, or empty when inputs changed."""
    root = Path(workspace)
    path = _latest_summary(root / "evidence/candidate-audits")
    if path is None:
        return {}
    path = path.with_name("report.json")
    try:
        report = _json(root, path)
        if report.get("task_sha256") != task_sha256:
            return {}
        payload = {key: value for key, value in report.items() if key != "report_payload_sha256"}
        if report.get("report_payload_sha256") != common.canonical_sha256(payload):
            return {}
        policy = report.get("heuristics")
        if (not isinstance(policy, dict)
                or not common.finite_number(policy.get("near_reference_fraction"))
                or not common.finite_number(policy.get("max_budget_fraction"))
                or policy["near_reference_fraction"] > .85
                or policy["max_budget_fraction"] < .01
                or type(policy.get("min_ablations")) is not int
                or policy["min_ablations"] < 3
                or not common.finite_number(policy.get("min_ablation_effect_fraction"))
                or policy["min_ablation_effect_fraction"] < .02
                or not common.finite_number(policy.get("min_combined_effect_fraction"))
                or policy["min_combined_effect_fraction"] < .1):
            # A lenient per-invocation override cannot waive the production
            # pilot diagnostic before main-agent review and rollout.
            return {}
        for directory, field in (
            ("measurements", "measurement_summary_sha256"),
            ("generic-controls", "controls_summary_sha256"),
        ):
            current = _latest_summary(root / "evidence" / directory)
            if current is None or report.get(field) != _hash(root, current):
                return {}
        pack = root / "evidence/difficulty-packs/latest.json"
        if (not pack.is_file() or pack.is_symlink()
                or report.get("difficulty_pack_sha256") != _hash(root, pack)):
            return {}
        pack_data = _json(root, pack)
        pack_attempt = pack_data.get("attempt")
        if type(pack_attempt) is not int or pack_attempt <= 0:
            return {}
        stored_pack = root / f"evidence/difficulty-packs/attempt-{pack_attempt:04d}/summary.json"
        if _json(root, stored_pack) != pack_data:
            return {}
        probes = report.get("ablation_probes")
        if not isinstance(probes, list):
            return {}
        for probe in probes:
            if not isinstance(probe, dict) or not isinstance(probe.get("receipt"), str):
                return {}
            receipt = Path(probe["receipt"])
            if receipt.is_absolute() or ".." in receipt.parts or str(receipt) != probe["receipt"]:
                return {}
            current_probe = root / receipt
            if current_probe.is_symlink() or not current_probe.is_file():
                return {}
            if not common.HEX64.fullmatch(str(probe.get("receipt_sha256"))):
                return {}
            if _hash(root, current_probe) != probe["receipt_sha256"]:
                return {}
        return report
    except (common.PipelineError, OSError, TypeError, KeyError):
        return {}
