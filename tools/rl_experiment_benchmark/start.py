"""Resume a declared local benchmark case without duplicating its processes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def process_matches(pid, marker):
    try:
        return marker.encode() in Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError, TypeError):
        return False


def resume_command(command, session):
    """Keep a persisted Manager objective instead of replaying the initial one."""
    try:
        saved = json.loads((session / "continuous.json").read_text())
    except (OSError, ValueError):
        saved = {}
    if not saved.get("objective"):
        return list(command)
    result = []
    tokens = iter(command)
    for token in tokens:
        if token == "--continuous":
            continue
        if token in {"--objective", "--objective-file"}:
            next(tokens, None)
            continue
        if token.startswith(("--objective=", "--objective-file=")):
            continue
        result.append(token)
    if "--resume-continuous" not in result:
        result.append("--resume-continuous")
    return result


def start(root):
    root = Path(root).resolve()
    case_path = root / "case.json"
    case = json.loads(case_path.read_text())
    workspace = Path(case["workspace"]).resolve()
    session = Path(case["session_root"]).resolve()
    if not workspace.is_dir() or not session.is_dir():
        raise ValueError("workspace and session_root must identify an existing local Argus case")
    if not case.get("started_at"):
        case["started_at"] = datetime.now(timezone.utc).isoformat()
        case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n")
    runtime = case.get("runtime_current")
    python = case.get("python", sys.executable)
    launch = case.get("launch", {})
    if case.get("launch_file"):
        launch = json.loads((root / case["launch_file"]).read_text())
    command = launch.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(s, str) for s in command):
        raise ValueError("case.launch.command must be a nonempty argument array")
    substitutions = {"workspace": str(workspace), "session_root": str(session), "case_root": str(root), "python": str(python)}
    def expand(value):
        for key, replacement in substitutions.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    command = [expand(s) for s in command]
    environment = os.environ.copy()
    environment.update({k: expand(v) for k, v in launch.get("settings", {}).items()})
    if runtime:
        environment["PYTHONPATH"] = str(Path(runtime).resolve()) + os.pathsep + environment.get("PYTHONPATH", "")
    collector_script = Path(__file__).with_name("collect.py").resolve()
    record_path = root / "collector-process.json"
    try:
        collector = json.loads(record_path.read_text())
    except (OSError, ValueError):
        collector = {}
    if not (
        process_matches(collector.get("pid"), str(collector_script))
        and process_matches(collector.get("pid"), "--root\0" + str(root))
    ):
        with (root / "collector.log").open("ab", buffering=0) as log:
            proc = subprocess.Popen(
                [str(python), str(collector_script), "--root", str(root), "--watch"],
                cwd=root, env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        collector = {"pid": proc.pid, "started_at": time.time(), "script": str(collector_script), "model_calls": False}
        record_path.write_text(json.dumps(collector) + "\n")
    try:
        daemon_pid = int((session / "daemon.pid").read_text().strip())
    except (OSError, ValueError):
        daemon_pid = None
    if not process_matches(daemon_pid, "--resume\0" + session.name):
        with (root / "launch.log").open("ab", buffering=0) as log:
            result = subprocess.run(
                resume_command(command, session), cwd=expand(launch.get("cwd", str(workspace))),
                env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
        if result.returncode:
            raise RuntimeError(f"Argus launch exited {result.returncode}; see {root / 'launch.log'}")
        daemon_pid = int((session / "daemon.pid").read_text().strip())
    return {"collector_pid": collector["pid"], "daemon_pid": daemon_pid, "case_id": case["case_id"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Private case directory containing case.json")
    args = parser.parse_args()
    print(json.dumps(start(args.root)))


if __name__ == "__main__":
    main()
