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
