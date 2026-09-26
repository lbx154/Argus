import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from argus.core.call_bound_execution import (
    CallBoundTasks,
    ExecutionCancelled,
    ProcessCleanupError,
    run_process,
)
from argus.core.role_tool_bridge import MAX_ACTIVE_OPERATIONS, ToolBridgeBusy


def test_async_admission_scope_and_close_are_bounded():
    tasks = CallBoundTasks()
    cancelled = []

    def work(stop):
        cancelled.append(stop)
        assert stop.wait(3)
        return {"status": "cancelled"}

    for index in range(MAX_ACTIVE_OPERATIONS):
        tasks.submit(str(index), work)
    with pytest.raises(ToolBridgeBusy, match="busy"):
        tasks.submit("overflow", work)
    with pytest.raises(ValueError, match="does not belong"):
        tasks.wait("../outside", 0)
    assert tasks.wait("0", 0.01) is None
    tasks.close(1)
    assert all(stop.is_set() for stop in cancelled)
    assert all(tasks.wait(str(index), 0) == {"status": "cancelled"} for index in range(MAX_ACTIVE_OPERATIONS))
    with pytest.raises(ToolBridgeBusy, match="turn ended"):
        tasks.submit("late", work)


@pytest.mark.parametrize("observed", [False, True])
def test_worker_exceptions_are_not_successful_results(observed):
    tasks = CallBoundTasks()

    def fail(_stop):
        raise ValueError("internal failure")

    tasks.submit("failure", fail)
    tasks.submit("another-failure", fail)
    tasks.submit("active", lambda stop: {"cancelled": stop.wait(3)})
    if observed:
        with pytest.raises(ValueError, match="internal failure"):
            tasks.wait("failure", 1)
    with pytest.raises(RuntimeError, match="Role commands failed: failure, another-failure") as failure:
        tasks.close(1)
    assert isinstance(failure.value.__cause__, ValueError)
    assert str(failure.value.__cause__) == "internal failure"
    assert tasks.wait("active", 0) == {"cancelled": True}


def test_incomplete_cleanup_is_explicit_and_has_a_shared_deadline():
    tasks = CallBoundTasks()
    release = threading.Event()
    tasks.submit("unfinished", lambda _stop: {"released": release.wait(3)})
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="cleanup did not finish: unfinished"):
            tasks.close(0.02)
        assert time.monotonic() - started < 1
    finally:
        release.set()
        tasks.close(1)


@pytest.mark.skipif(os.name == "nt", reason="Reviewer Docker execution requires Linux")
def test_failed_process_reaping_preserves_the_original_interruption(monkeypatch):
    killed = []

    def wait(*, timeout):
        assert timeout == 2
        raise subprocess.TimeoutExpired(["synthetic"], timeout)

    process = SimpleNamespace(pid=123456789, stdout=None, stderr=None, wait=wait)
    monkeypatch.setattr("argus.core.call_bound_execution.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("argus.core.call_bound_execution.os.killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(ProcessCleanupError) as failure:
        run_process(["synthetic"], env={}, timeout=0)
    assert killed == [123456789]
    assert isinstance(failure.value.interrupted, subprocess.TimeoutExpired)


@pytest.mark.skipif(os.name == "nt", reason="Reviewer Docker execution requires Linux")
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_process_interruption_kills_and_reaps_the_owned_group(tmp_path, mode):
    started = tmp_path / "started"
    stop = threading.Event()
    script = (
        "import os, pathlib, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(started)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_process, [sys.executable, "-c", script],
            env={"PATH": os.defpath}, timeout=0.5 if mode == "timeout" else None,
            cancelled=stop,
        )
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        if mode == "cancel":
            stop.set()
        with pytest.raises(ExecutionCancelled if mode == "cancel" else subprocess.TimeoutExpired):
            future.result(3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(started.read_text()), 0)
    # A killed descendant can briefly remain as a zombie until the host reaps
    # it; no live process in the owned group may survive cancellation.
    import psutil

    assert not [
        proc.pid for proc in psutil.process_iter(["pid", "status"])
        if proc.info["status"] != psutil.STATUS_ZOMBIE
        and proc.pid != os.getpid() and _same_group(proc.pid, int(started.read_text()))
    ]


def _same_group(pid, group):
    try:
        return os.getpgid(pid) == group
    except ProcessLookupError:
        return False
