"""Native Windows process ownership checks; synthetic commands, no provider calls."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus_skill.agent_cli._run_exec import _StreamState
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job Objects")


class HeldProcess:
    """Retain process identity before the action; never clean up a reused PID."""

    def __init__(self, pid: int):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.api.OpenProcess.restype = ctypes.c_void_p
        self.api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.api.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.api.OpenProcess(0x100000 | 0x1000 | 0x1, False, pid)
        assert self.handle, ctypes.WinError(ctypes.get_last_error())

    def exited(self, timeout_ms: int = 3000) -> bool:
        return self.api.WaitForSingleObject(self.handle, timeout_ms) == 0

    def close(self):
        if not self.exited(0):
            assert self.api.TerminateProcess(self.handle, 1)
            assert self.exited()
        assert self.api.CloseHandle(self.handle)


def wait_json(path: Path, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
    raise AssertionError(f"Fixture did not become ready: {path}")


def spawn_tree(monkeypatch, tmp_path):
    marker = tmp_path / "descendants.json"
    release = tmp_path / "release"
    child = (
        "import subprocess,sys,os,json,time;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid]));"
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"release=Path({str(release)!r});\n"
        "while not release.exists(): time.sleep(0.02)\n"
    )
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex")
    monkeypatch.setattr(runner, "_build_command", lambda **kw: [sys.executable, "-u", "-c", parent])
    _, process, failure, prompt_path = runner._spawn_turn_process(
        prompt="fixture", resume_thread_id=None, options=RunnerOptions(working_dir=str(tmp_path)),
    )
    assert failure is None and prompt_path is None
    held = [HeldProcess(pid) for pid in wait_json(marker)]
    return runner, process, held, release


@pytest.mark.parametrize("action", ["stop", "parent_first_exit"])
def test_actual_runner_reaps_owned_child_and_grandchild(monkeypatch, tmp_path, action):
    runner, process, held, release = spawn_tree(monkeypatch, tmp_path)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    try:
        if action == "parent_first_exit":
            release.touch()
            process.wait(timeout=5)
            runner._cleanup_orphan_process_group(process, _StreamState(thread_id=None))
        else:
            runner._terminate_process(process)
        assert process.poll() is not None
        assert all(child.exited() for child in held), "Owned descendants survived turn cleanup"
        assert unrelated.poll() is None, "An unrelated process was terminated"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for child in held:
            child.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize("action", ["stop", "watchdog", "natural"])
def test_streaming_turn_reaps_children(monkeypatch, tmp_path, action):
    runner, process, held, release = spawn_tree(monkeypatch, tmp_path)
    options = RunnerOptions(working_dir=str(tmp_path), watchdog_hard_idle_seconds=1)
    if action == "stop":
        options.external_interrupt_reason_provider = lambda: "synthetic stop"
    if action == "natural":
        release.touch()
    try:
        state = runner._stream_turn_output(
            process=process, command=[sys.executable], options=options,
            run_label="engineer-r1", thread_id=None,
        )
        assert state.watchdog_terminated is (action != "natural")
        assert all(child.exited() for child in held)
    finally:
        runner._terminate_process(process)
        for child in held:
            child.close()


def test_host_exit_closes_uninherited_job_and_reaps_tree(tmp_path):
    host_ready = tmp_path / "host.json"
    marker = tmp_path / "children.json"
    child = (
        "import subprocess,sys,os,json,time;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
    )
    host_body = (
        "import sys,json,time;from pathlib import Path;"
        "from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner,RunnerOptions;"
        "r=AgentCliRunner(agent_bin=sys.executable,backend='codex');"
        f"r._build_command=lambda **kw:[sys.executable,'-c',{child!r}];"
        "_,p,_,_=r._spawn_turn_process(prompt='fixture',resume_thread_id=None,options=RunnerOptions());"
        f"Path({str(host_ready)!r}).write_text(json.dumps(p.pid));time.sleep(60)"
    )
    host = subprocess.Popen([sys.executable, "-c", host_body])
    held = []
    try:
        held = [HeldProcess(wait_json(host_ready))]
        held.extend(HeldProcess(pid) for pid in wait_json(marker))
        host.kill()
        host.wait(timeout=5)
        assert all(child.exited() for child in held), "Owned process survived abrupt host exit"
    finally:
        if host.poll() is None:
            host.kill()
        host.wait(timeout=5)
        for child in held:
            child.close()


@pytest.mark.parametrize("failure", ["spawn", "assign", "resume"])
def test_setup_failure_never_executes_provider_or_leaks_handles(monkeypatch, tmp_path, failure):
    from argus_skill.core import windows_job

    marker = tmp_path / "executed"
    processes = []
    jobs = []
    original_init = windows_job._WindowsTurnJob.__init__

    def initialize(job):
        original_init(job)
        jobs.append(job)

    def fail(*args, **kwargs):
        raise OSError(f"injected {failure} failure")

    def spawn(*args, **kwargs):
        if failure == "spawn":
            return fail()
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(windows_job._WindowsTurnJob, "__init__", initialize)
    if failure in {"assign", "resume"}:
        monkeypatch.setattr(windows_job._WindowsTurnJob, failure, fail)
    with pytest.raises(OSError, match=f"injected {failure}"):
        windows_job.spawn_owned_process(
            [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
            popen_factory=spawn, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    assert not marker.exists(), "Provider ran before ownership setup completed"
    assert all(process.poll() is not None for process in processes)
    assert all(process.stdout.closed and process.stderr.closed for process in processes)
    assert jobs and all(job.handle is None for job in jobs)


@pytest.mark.parametrize("launcher", ["worker", "command"])
def test_official_durable_launchers_survive_turn_stop(monkeypatch, tmp_path, launcher):
    durable_ready = tmp_path / "durable-ready"
    durable_release = tmp_path / "durable-release"
    started = tmp_path / "started.json"
    def escape(path):
        return str(path).replace("'", "''")
    command = (
        f"[IO.File]::WriteAllText('{escape(durable_ready)}','ready'); "
        f"while (-not (Test-Path -LiteralPath '{escape(durable_release)}')) "
        "{ Start-Sleep -Milliseconds 50 }; exit 0"
    )
    if launcher == "worker":
        launch = (
            "from argus_skill.tools.subagent._cli import _spawn_windows_worker;"
            "p=_spawn_windows_worker(task_id='owned-test',description='fixture',"
            f"command={command!r},mode='direct',timeout=30,monitor_interval=1,"
            f"model=None,cwd={str(tmp_path)!r},run_dir=None,preflight=False,cpu_ids=());"
        )
    else:
        launch = (
            "from argus_skill.tools.subagent._registry import _launch_durable_command;"
            "p=_launch_durable_command(task_id='owned-test',run_id='fixture',"
            f"command={command!r},cwd={str(tmp_path)!r},"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        )
    body = (
        "import subprocess,sys,json,time;from pathlib import Path;"
        + launch + f"Path({str(started)!r}).write_text(json.dumps(p.pid));time.sleep(60)"
    )
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setattr(runner, "_build_command", lambda **kw: [sys.executable, "-c", body])
    _, process, failure, _ = runner._spawn_turn_process(
        prompt="fixture", resume_thread_id=None, options=RunnerOptions(working_dir=str(tmp_path)),
    )
    assert failure is None
    held = None
    try:
        held = HeldProcess(wait_json(started))
        deadline = time.monotonic() + 15
        while not durable_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert durable_ready.exists(), "Durable command did not start"
        runner._terminate_process(process)
        assert not held.exited(100), "Registered independent work died with provider"
        durable_release.touch()
        assert held.exited(10000), "Durable command failed to finish independently"
    finally:
        durable_release.touch()
        runner._terminate_process(process)
        if held:
            held.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
