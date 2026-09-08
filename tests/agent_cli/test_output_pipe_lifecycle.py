"""Output ownership and cleanup under real Windows pipe lifetimes."""
import ctypes
import gc
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.agent_cli import _run_exec
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


def wait_file(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
    raise AssertionError(f"fixture did not become ready: {path}")


class HeldProcess:
    def __init__(self, pid):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.api.OpenProcess.restype = ctypes.c_void_p
        self.api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.api.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.api.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
        assert self.handle

    def exited(self):
        return self.api.WaitForSingleObject(self.handle, 2000) == 0

    def close(self):
        if self.api.WaitForSingleObject(self.handle, 0) != 0:
            self.api.TerminateProcess(self.handle, 1)
            assert self.exited()
        self.api.CloseHandle(self.handle)


def run_fixture(monkeypatch, tmp_path, body, prompt, *, options=None, callback=None):
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex", event_callback=callback)
    monkeypatch.setattr(
        runner, "_build_command", lambda **kw: [sys.executable, "-X", "utf8", "-u", "-c", body]
    )
    processes = []
    original = _run_exec.subprocess.Popen

    def capture(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(_run_exec.subprocess, "Popen", capture)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            runner.run_exec,
            prompt=prompt,
            resume_thread_id=None,
            options=options or RunnerOptions(working_dir=str(tmp_path)),
            run_label="engineer-r1",
        )
        try:
            result = future.result(timeout=5)
            return result, time.monotonic() - start, processes
        finally:
            for proc in processes:
                runner._terminate_process(proc)
            # If an assertion or timeout fired, terminating the identified root
            # releases a blocked stdin write before joining the executor.
            if not future.done():
                future.result(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows independent inherited pipes")
def test_inherited_pipes_return_promptly_and_keep_independent_writer_healthy(monkeypatch, tmp_path):

    ready, release, done = (tmp_path / name for name in ("ready.json", "release", "done.json"))
    child = (
        "import os,time,json;from pathlib import Path;"
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid()]));"
        f"release=Path({str(release)!r});deadline=time.monotonic()+12\n"
        "while not release.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "os.write(1,b'late-output'*32768+b'\\n');os.write(2,b'late-error'*32768+b'\\n');"
        f"Path({str(done)!r}).write_text('true')"
    )
    body = (
        "import sys,subprocess,time,json;from pathlib import Path;sys.stdin.read();"
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_BREAKAWAY_FROM_JOB);"
        f"ready=Path({str(ready)!r})\n"
        "while not ready.exists(): time.sleep(.01)\n"
        "print(json.dumps({'type':'turn.completed'}),flush=True)"
    )
    held = None
    processes = []
    try:
        result, elapsed, processes = run_fixture(monkeypatch, tmp_path, body, "fixture")
        assert elapsed < 3 and result.turn_completed
        held = HeldProcess(wait_file(ready)[0])
        assert held.api.WaitForSingleObject(held.handle, 0) == 258
        # The two readers intentionally remain until the independent writer's
        # EOF; a late write must not get BrokenPipe or block behind a full pipe.
        assert not processes[0].stdout.closed
        release.touch()
        assert wait_file(done) is True
        assert held.exited()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            not p.stdout.closed or not p.stderr.closed for p in processes
        ):
            time.sleep(0.02)
        assert all(p.stdout.closed and p.stderr.closed for p in processes)
        assert not any("late-output" in line for line in result.stdout_lines)
    finally:
        release.touch()
        if held is None and ready.exists():
            held = HeldProcess(json.loads(ready.read_text())[0])
        if held:
            held.close()


def test_callback_exception_still_releases_process_and_readers(monkeypatch, tmp_path):
    def fail(*args):
        raise ValueError("injected observer failure")

    before = {t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")}
    with pytest.raises(ValueError, match="injected observer"):
        run_fixture(
            monkeypatch,
            tmp_path,
            "import time;print('hello',flush=True);time.sleep(20)",
            "fixture",
            callback=fail,
        )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        after = {
            t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")
        }
        if after <= before:
            break
        time.sleep(0.02)
    assert after <= before


def test_repeated_calls_do_not_accumulate_reader_threads(monkeypatch, tmp_path):
    before = {t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")}
    for _ in range(12):
        with monkeypatch.context() as patch:
            result, _, processes = run_fixture(
                patch,
                tmp_path,
                'import sys;sys.stdin.read();print(\'{"type":"turn.completed"}\',flush=True)',
                "fixture",
            )
            assert result.turn_completed
            assert all(p.stdout.closed and p.stderr.closed for p in processes)
    gc.collect()
    assert {
        t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")
    } <= before


def test_reader_failure_reaches_caller_instead_of_hanging(monkeypatch, tmp_path):
    original = _run_exec.subprocess.Popen

    class FailedReader:
        def __init__(self, stream):
            self.stream = stream

        def __iter__(self):
            raise OSError("injected read error")

        def close(self):
            self.stream.close()

    def spawn(*args, **kwargs):
        p = original(*args, **kwargs)
        p.stdout = FailedReader(p.stdout)
        return p

    monkeypatch.setattr(_run_exec.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="Provider output reader failed") as error:
        run_fixture(monkeypatch, tmp_path, "import time;time.sleep(20)", "fixture")
    assert isinstance(error.value.__cause__, OSError)


def test_second_reader_start_failure_releases_first_reader(monkeypatch, tmp_path):
    original = threading.Thread.start

    def start(thread):
        if thread.name.startswith("argus-provider-pipe-") and thread.name.endswith("-stderr"):
            raise RuntimeError("injected thread start error")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    before = {t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")}
    with pytest.raises(RuntimeError, match="injected thread start"):
        run_fixture(monkeypatch, tmp_path, "import time;time.sleep(20)", "fixture")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        after = {
            t.ident for t in threading.enumerate() if t.name.startswith("argus-provider-pipe-")
        }
        if after <= before:
            break
        time.sleep(0.02)
    assert after <= before
