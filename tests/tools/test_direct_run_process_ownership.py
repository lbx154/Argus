"""A finished shell cannot leave an unowned experiment writing shared outputs."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from argus_skill.core.daemon_lock import is_pid_running, is_process_group_running
from argus_skill.tools.subagent import _direct_run, _registry


@pytest.mark.skipif(os.name == "nt", reason="POSIX private process groups")
@pytest.mark.parametrize("shell_exit", ["terminated", "background_success"])
def test_direct_owner_settles_children_before_terminal_state_and_release(
    tmp_path, monkeypatch, shell_exit,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_registry, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(_direct_run, "experiment_launch_preflight", lambda **_kw: (False, ""))
    monkeypatch.setattr(_direct_run, "release_experiment_launch_claim", lambda **_kw: None)
    monkeypatch.setattr(_direct_run, "_alert_engineer", lambda *_args: None)
    monkeypatch.setattr(_direct_run, "command_env", lambda _lease: os.environ.copy())
    original_terminate = _direct_run._terminate_proc
    monkeypatch.setattr(_direct_run, "_terminate_proc", lambda proc: original_terminate(proc, grace=0.1))

    child_file = tmp_path / "child.pid"
    script = tmp_path / "child.py"
    script.write_text(
        "import os, signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(child_file)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(0.02)\n",
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    if shell_exit == "background_success":
        command += f" & while [ ! -f {shlex.quote(str(child_file))} ]; do sleep 0.01; done"

    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True,
    )
    released = []

    class Lease:
        ttl_seconds = 1.0

        def renew(self):
            return True

        def release(self):
            assert not is_pid_running(int(child_file.read_text()))
            assert unrelated.poll() is None
            released.append(True)

    monkeypatch.setattr(_direct_run, "acquire_for_task", lambda *_args, **_kw: Lease())
    writes = []
    real_write = _registry._write_task

    def record_write(task_id, record):
        if record["state"] in {"done", "error", "timeout"}:
            assert not is_pid_running(int(child_file.read_text()))
        writes.append(record.copy())
        return real_write(task_id, record)

    monkeypatch.setattr(_direct_run, "_write_task", record_write)
    _registry._write_task("owned", {"state": "starting", "task_id": "owned", "run_id": "run-1"})
    thread = threading.Thread(
        target=_direct_run._run_direct,
        args=("owned", command, "ownership regression"),
        kwargs={"timeout": 5, "cwd": str(tmp_path)},
        daemon=True,
    )
    group = 0
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            running = next((r for r in writes if r["state"] == "running"), None)
            if child_file.exists() and running:
                group = running["process_group_id"]
                break
            time.sleep(0.01)
        assert group > 0 and child_file.exists()
        if shell_exit == "terminated":
            os.kill(group, signal.SIGTERM)  # Reproduce killing only the wrapper.
        thread.join(timeout=6)
        assert not thread.is_alive()
        assert released == [True]
        final = _registry._read_task("owned")
        assert final["state"] == "error"
        assert final["exit_code"] == (-signal.SIGTERM if shell_exit == "terminated" else 0)
        assert final["orphan_process_group_id"] == group
        assert final["process_group_cleanup_succeeded"] is True
        assert not is_process_group_running(group)
        assert unrelated.poll() is None
    finally:
        if group and is_process_group_running(group):
            os.killpg(group, signal.SIGKILL)
        thread.join(timeout=2)
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX private process groups")
def test_cleanup_refuses_the_current_process_group(monkeypatch):
    proc = SimpleNamespace(pid=os.getpid(), _argus_durable_process_group=os.getpid())
    monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("must not signal our own group"))
    assert _direct_run._owned_process_group(proc) == 0
    assert _direct_run._settle_direct_process_group(proc, None) == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX private process groups")
def test_cleanup_refuses_a_reused_group_leader_pid(monkeypatch):
    proc = SimpleNamespace(
        pid=424242, _argus_durable_process_group=424242, poll=lambda: 0,
    )
    # Our child was reaped, but a new process now answers for the old PID.
    monkeypatch.setattr(os, "kill", lambda *_args: None)
    monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("must not signal a reused group"))
    assert _direct_run._owned_process_group(proc) == 0
    _direct_run._terminate_proc(proc)
