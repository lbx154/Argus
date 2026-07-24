from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _wait_for_terminal_record(path: Path, *, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    record: dict = {}
    while time.time() < deadline:
        if path.exists():
            record = json.loads(path.read_text())
            if record.get("state") in {"done", "error", "crashed", "timeout"}:
                return record
        time.sleep(0.05)
    return record


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-loss integration test")
def test_direct_job_survives_worker_owner_death(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")
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


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "sched_getaffinity"),
    reason="POSIX CPU-affinity integration test",
)
def test_subagent_cpu_lease_is_inherited_by_command(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")
    selected = min(os.sched_getaffinity(0))
    script = "import json,os; print(json.dumps(sorted(os.sched_getaffinity(0))))"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    submit = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.subagent",
            "submit",
            "--task-id",
            "cpu-affinity",
            "--description",
            "CPU affinity inheritance test",
            "--command",
            command,
            "--cpu-ids",
            str(selected),
            "--timeout",
            "20",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert submit.returncode == 0, submit.stderr
    assert json.loads(submit.stdout)["cpu_ids"] == [selected]
    record = _wait_for_terminal_record(
        tmp_path / ".argus_subagents" / "cpu-affinity.json"
    )
    assert record.get("state") == "done", record
    assert record["cpu_ids"] == [selected]
    assert json.loads(record["stdout_tail"].strip()) == [selected]


@pytest.mark.skipif(os.name == "nt", reason="POSIX detach integration test")
def test_submit_releases_capture_pipes_before_long_job_finishes(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")

    started = time.monotonic()
    submit = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.tools.subagent",
            "submit",
            "--task-id",
            "detached-capture",
            "--command",
            "sleep 2; printf done",
            "--timeout",
            "20",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=1,
    )

    assert submit.returncode == 0, submit.stderr
    assert time.monotonic() - started < 1
    record = _wait_for_terminal_record(
        tmp_path / ".argus_subagents" / "detached-capture.json",
        timeout=5,
    )
    assert record.get("state") == "done", record
    assert record["stdout_tail"] == "done"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor integration test")
def test_detach_reopens_previously_closed_standard_descriptors(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    marker = tmp_path / "descriptor-result"
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from argus_skill.tools.subagent._cli import _detach_child_stdio\n"
        "for fd in (0, 1, 2):\n"
        "    try: os.close(fd)\n"
        "    except OSError: pass\n"
        "_detach_child_stdio()\n"
        f"Path({str(marker)!r}).write_text("
        "','.join(str(os.fstat(fd).st_mode) for fd in (0, 1, 2)))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0
    assert len(marker.read_text().split(",")) == 3
