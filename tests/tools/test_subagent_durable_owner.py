from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-loss integration test")
def test_direct_job_survives_worker_owner_death(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    submit = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.subagent",
            "submit",
            "--task-id",
            "durable",
            "--description",
            "owner-loss test",
            "--command",
            "sleep 2; printf survived",
            "--timeout",
            "20",
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert submit.stdout is not None
    submitted_line = submit.stdout.readline()
    submit.wait(timeout=5)
    assert submit.returncode == 0, submit.stderr.read() if submit.stderr else ""
    worker_pid = int(json.loads(submitted_line)["pid"])
    record_path = tmp_path / ".argus_subagents" / "durable.json"
    deadline = time.time() + 5
    record = {}
    while time.time() < deadline:
        if record_path.exists():
            record = json.loads(record_path.read_text())
            if record.get("state") == "running" and record.get("pid") != worker_pid:
                break
        time.sleep(0.05)
    assert record.get("state") == "running", record
    os.kill(worker_pid, signal.SIGKILL)
    time.sleep(2.4)
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.subagent",
            "status",
            "--task-id",
            "durable",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["state"] == "done"
    assert payload["exit_code"] == 0
    assert payload["terminal_owner"] == "exit_sidecar_reconciler"
    assert "survived" in payload["stdout_tail"]
