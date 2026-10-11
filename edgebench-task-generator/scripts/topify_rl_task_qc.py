#!/usr/bin/env python3
"""Run the repository's rl-task-qc static gate on a final package.

The gate is intentionally an external final check. It does not mutate a task,
workspace, directory or archive. Runtime, native architecture and model-trial
checks remain separate evidence gates.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

import topify_common as common


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise common.PipelineError(f"unsafe ZIP member: {member.filename}")
            target = destination / path
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(((member.external_attr >> 16) & 0o777) or 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(member))
            mode = (member.external_attr >> 16) & 0o777
            target.chmod(mode or 0o644)


def run(target: str | Path, delivery_format: str = "auto", output: str | Path | None = None,
        profile: str | Path | None = None) -> tuple[dict, int]:
    source = Path(target).absolute()
    if not source.exists() or source.is_symlink():
        raise common.PipelineError(f"QC target does not exist or is a symlink: {source}")
    project_root = Path(__file__).resolve().parents[2]
    checker = project_root / "rl-task-qc/scripts/qc_static.py"
    if not checker.is_file():
        raise common.PipelineError(f"rl-task-qc checker is unavailable: {checker}")
    if delivery_format not in {"auto", "sforge-only", "harbor"}:
        raise common.PipelineError("QC format must be auto, sforge-only or harbor")
    temp = tempfile.TemporaryDirectory(prefix="edgebench-rl-qc-")
    root = Path(temp.name) / "package"
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            root.mkdir()
            _safe_extract(source, root)
            # A ZIP may contain one named customer folder. qc_static accepts
            # either that folder or the extraction root; choose the one with
            # the actual task payload when present.
            candidates = [path for path in root.iterdir() if path.is_dir()]
            if len(candidates) == 1 and (candidates[0] / "tasks").is_dir():
                root = candidates[0]
        elif source.is_dir():
            root = source
        else:
            raise common.PipelineError("QC target must be a directory or ZIP")
        report_path = Path(temp.name) / "qc.json"
        command = ["python3", str(checker), str(root), "--json", str(report_path), "--format", delivery_format]
        if profile is not None:
            command.extend(["--profile", str(Path(profile).absolute())])
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        findings = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else []
        result = {
            "status": "pass" if process.returncode == 0 else "fail",
            "delivery_format": delivery_format,
            "profile": str(Path(profile).absolute()) if profile is not None else None,
            "target": str(source),
            "command": command,
            "returncode": process.returncode,
            "finding_count": len(findings),
            "fail_count": sum(item.get("level") == "FAIL" for item in findings),
            "warn_count": sum(item.get("level") == "WARN" for item in findings),
            "findings": findings,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        if output is not None:
            destination = Path(output).absolute()
            if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
                raise common.PipelineError(f"QC output must be a new file in an existing directory: {destination}")
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result, process.returncode
    finally:
        temp.cleanup()
