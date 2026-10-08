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

from argus.daemon.state import (
    _process_alive as process_alive,
)
from argus.daemon.state import (
    _terminate_windows_process_tree as terminate_windows_process_tree,
)
from argus.tools.subagent import _registry


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


def test_task_record_concurrent_process_writers_publish_complete_json(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    gate = tmp_path / "publish"
    script = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from argus.tools.subagent import _registry\n"
        "root, gate, writer = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]\n"
        "gate.with_name('ready-' + writer).write_text('ready')\n"
        "while not gate.exists(): time.sleep(0.01)\n"
        "for index in range(40):\n"
        "    _registry._write_task('shared', {'task_id': 'shared', 'run_id': 'run', 'writer': writer, 'index': index, 'payload': writer * 10000}, registry_root=root)\n"
        "    current = json.loads(_registry._registry_path('shared', registry_root=root).read_text())\n"
        "    assert current['payload'] == current['writer'] * 10000\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    writers: list[subprocess.Popen] = []
    try:
        for writer in ("first", "second"):
            writers.append(subprocess.Popen(
                [sys.executable, "-c", script, str(root), str(gate), writer],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all((tmp_path / f"ready-{name}").exists() for name in ("first", "second")):
            time.sleep(0.01)
        assert all((tmp_path / f"ready-{name}").exists() for name in ("first", "second"))
        gate.touch()
        for process in writers:
            output, errors = process.communicate(timeout=20)
            assert process.returncode == 0, output + errors
        current = _registry._read_task("shared", registry_root=root)
        assert current is not None
        assert current["index"] == 39
        assert current["payload"] == current["writer"] * 10000
        assert not list(root.glob("*.tmp"))
    finally:
        gate.touch()
        for process in writers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("state", ["running", "done"])
def test_parent_identity_merge_preserves_worker_progress(tmp_path: Path, state: str) -> None:
    identity = {"pid": 456, "start_time_ticks": 123}
    current = {"task_id": "shared", "run_id": "run", "state": state,
               "pid": 789, "worker_pid": 456, "worker_process_identity": identity,
               "stdout_tail": "worker result", "exit_code": 0}
    _registry._write_task("shared", current, registry_root=tmp_path)
    before = _registry._read_task("shared", registry_root=tmp_path)
    assert _registry._merge_worker_identity("shared", "run", 999, {"pid": 999}, registry_root=tmp_path)
    assert _registry._read_task("shared", registry_root=tmp_path) == before


def test_parent_identity_merge_fills_startup_fields_and_rejects_new_run(tmp_path: Path) -> None:
    _registry._write_task("shared", {"task_id": "shared", "run_id": "run", "state": "starting"}, registry_root=tmp_path)
    assert _registry._merge_worker_identity("shared", "run", 456, {"pid": 456}, registry_root=tmp_path)
    current = _registry._read_task("shared", registry_root=tmp_path)
    assert current is not None
    assert current["state"] == "starting"
    assert current["worker_pid"] == current["pid"] == 456
    assert current["worker_process_identity"] == {"pid": 456}
    assert not _registry._merge_worker_identity("shared", "obsolete", 999, {"pid": 999}, registry_root=tmp_path)
    assert _registry._read_task("shared", registry_root=tmp_path) == current


def test_task_temp_collision_preserves_foreign_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedUuid:
        hex = "0" * 32
    monkeypatch.setattr(_registry.uuid, "uuid4", FixedUuid)
    foreign = tmp_path / f".argus-{'0' * 32}.tmp"
    foreign.write_text("other writer")
    with pytest.raises(FileExistsError):
        _registry._write_task("shared", {"task_id": "shared"}, registry_root=tmp_path)
    assert foreign.read_text() == "other writer"
    assert not _registry._registry_path("shared", registry_root=tmp_path).exists()


def test_task_replace_failure_keeps_previous_record_and_cleans_owned_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _registry._write_task("shared", {"task_id": "shared", "state": "running"}, registry_root=tmp_path)
    before = _registry._read_task("shared", registry_root=tmp_path)
    def reject_replace(source: Path, target: Path) -> None:
        raise PermissionError("synthetic replacement denied")
    monkeypatch.setattr(_registry.os, "replace", reject_replace)
    with pytest.raises(PermissionError, match="synthetic replacement denied"):
        _registry._write_task("shared", {"task_id": "shared", "state": "done"}, registry_root=tmp_path)
    assert _registry._read_task("shared", registry_root=tmp_path) == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-loss integration test")
def test_direct_job_survives_worker_owner_death(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")
    submit = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus.tools.subagent",
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
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert submit.returncode == 0, submit.stderr
    worker_pid = int(json.loads(submit.stdout)["pid"])
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
    status_command = [
        sys.executable,
        "-m",
        "argus.tools.subagent",
        "status",
        "--task-id",
        "durable",
    ]
    deadline = time.time() + 10
    payload = {}
    while time.time() < deadline:
        status = subprocess.run(
            status_command,
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert status.returncode == 0, status.stderr
        payload = json.loads(status.stdout)
        if payload.get("state") == "done":
            break
        time.sleep(0.05)
    assert payload["state"] == "done"
    assert payload["exit_code"] == 0
    assert payload["terminal_owner"] == "exit_sidecar_reconciler"
    assert "survived" in payload["stdout_tail"]


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="native Windows durable worker")
def test_windows_direct_worker_owner_loss_reconciles_exit_sidecar(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")
    command = "Start-Sleep -Milliseconds 800; [Console]::Out.Write('survived')"
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus.tools.subagent",
            "_worker",
            "--task-id",
            "durable-win",
            "--description",
            "owner-loss test",
            "--command",
            command,
            "--timeout",
            "20",
            "--cwd",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    record_path = tmp_path / ".argus_subagents" / "durable-win.json"
    owner_pid = 0
    job_pid = 0
    try:
        deadline = time.time() + 10
        record: dict = {}
        while time.time() < deadline:
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding="utf-8"))
                owner_pid = int(record.get("worker_pid") or worker.pid)
                job_pid = int(record.get("pid") or 0)
                if record.get("state") == "running" and job_pid != owner_pid:
                    break
            time.sleep(0.05)
        assert record.get("state") == "running", record
        assert job_pid and job_pid != owner_pid

        os.kill(owner_pid, signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline and process_alive(owner_pid):
            time.sleep(0.05)

        status_command = [
            sys.executable,
            "-m",
            "argus.tools.subagent",
            "status",
            "--task-id",
            "durable-win",
        ]
        deadline = time.time() + 10
        payload = {}
        while time.time() < deadline:
            status = subprocess.run(
                status_command,
                cwd=tmp_path,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            assert status.returncode in {0, 1}, status.stderr
            payload = json.loads(status.stdout)
            if payload.get("state") == "done":
                break
            time.sleep(0.05)

        assert payload["state"] == "done"
        assert payload["exit_code"] == 0
        assert payload["terminal_owner"] == "exit_sidecar_reconciler"
        assert "survived" in payload["stdout_tail"]
    finally:
        if worker.poll() is None:
            worker.kill()
        if job_pid and process_alive(job_pid):
            terminate_windows_process_tree(
                job_pid,
                identity_check=lambda: process_alive(job_pid),
            )
            if process_alive(job_pid):
                os.kill(job_pid, signal.SIGTERM)
        worker.communicate(timeout=10)


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="native Windows durable worker")
def test_windows_submit_cwd_keeps_registry_in_submitter_cwd(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    submitter = tmp_path / "submitter"
    workload = tmp_path / "workload"
    submitter.mkdir()
    workload.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["ARGUS_SKILL_HOME"] = str(tmp_path / "argus-home")
    escaped_workload = str(workload).replace("'", "''")
    command = (
        f"if ((Get-Location).Path -ne '{escaped_workload}') {{ exit 7 }}; "
        "[Console]::Out.Write('workload-ok')"
    )
    task_id = "different-cwd"
    base_command = [
        sys.executable,
        "-m",
        "argus.tools.subagent",
    ]

    submit = subprocess.run(
        [
            *base_command,
            "submit",
            "--task-id",
            task_id,
            "--description",
            "different cwd registry test",
            "--command",
            command,
            "--timeout",
            "20",
            "--cwd",
            str(workload),
        ],
        cwd=submitter,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert submit.returncode == 0, submit.stderr
    assert json.loads(submit.stdout)["state"] == "submitted"

    wait = subprocess.run(
        [
            *base_command,
            "wait",
            "--task-id",
            task_id,
            "--timeout",
            "20",
        ],
        cwd=submitter,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert wait.returncode == 0, wait.stderr
    wait_payload = json.loads(wait.stdout)

    status = subprocess.run(
        [
            *base_command,
            "status",
            "--task-id",
            task_id,
        ],
        cwd=submitter,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)

    assert wait_payload["state"] == "done"
    assert wait_payload["exit_code"] == 0
    assert wait_payload["cwd"] == str(workload)
    assert "workload-ok" in wait_payload["stdout_tail"]
    assert status_payload["state"] == "done"
    assert status_payload["run_id"] == wait_payload["run_id"]
    assert status_payload["stdout_tail"] == wait_payload["stdout_tail"]
    assert (submitter / ".argus_subagents" / f"{task_id}.json").exists()
    assert not (workload / ".argus_subagents").exists()


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
            "argus.tools.subagent",
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

    ready = tmp_path / "workload-ready"
    release = tmp_path / "release-workload"
    record_path = tmp_path / ".argus_subagents" / "detached-capture.json"
    script = (
        "from pathlib import Path\n"
        "import time\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        f"while not Path({str(release)!r}).exists():\n"
        "    time.sleep(0.05)\n"
        "print('done', end='')\n"
    )
    try:
        submit = subprocess.run(
            [
                sys.executable,
                "-m",
                "argus.tools.subagent",
                "submit",
                "--task-id",
                "detached-capture",
                "--command",
                f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                "--timeout",
                "40",
            ],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert submit.returncode == 0, submit.stderr
        assert json.loads(submit.stdout)["state"] == "submitted"
        deadline = time.monotonic() + 10
        running: dict = {}
        while time.monotonic() < deadline:
            if record_path.exists():
                running = json.loads(record_path.read_text())
            if ready.exists() and running.get("state") == "running":
                break
            time.sleep(0.05)
        assert ready.exists(), running
        assert running.get("state") == "running", running
        # Captured pipes have reached EOF while the workload is still blocked.
        assert not release.exists()
    finally:
        release.write_text("release")
        record = _wait_for_terminal_record(record_path, timeout=10)
    assert record.get("state") == "done", record
    assert record["exit_code"] == 0
    assert record["stdout_tail"] == "done"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor integration test")
def test_detach_reopens_previously_closed_standard_descriptors(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    marker = tmp_path / "descriptor-result"
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from argus.tools.subagent._cli import _detach_child_stdio\n"
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
