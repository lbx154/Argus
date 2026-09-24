"""Cancellable work owned by one role call, not by a transport request."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import IO, Any, Callable

from .role_tool_bridge import MAX_ACTIVE_OPERATIONS, ToolBridgeBusy

PROCESS_REAP_SECONDS = 2.0


class ExecutionCancelled(Exception):
    pass


class ProcessCleanupError(RuntimeError):
    def __init__(self, interrupted: BaseException, pid: int) -> None:
        super().__init__(f"Could not terminate and reap the owned process group {pid}.")
        self.interrupted = interrupted


def run_process(
    argv: list[str], *, env: dict[str, str], timeout: float | None,
    cancelled: threading.Event | None = None,
    stdout: IO[bytes] | None = None, stderr: IO[bytes] | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancelled is not None and cancelled.is_set():
        raise ExecutionCancelled
    process = subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=stderr if stderr is not None else subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        start_new_session=os.name != "nt",
    )
    started = time.monotonic()
    try:
        while True:
            if cancelled is not None and cancelled.is_set():
                raise ExecutionCancelled
            wait = 0.1
            if timeout is not None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                wait = min(wait, remaining)
            try:
                out, err = process.communicate(timeout=wait)
                return subprocess.CompletedProcess(argv, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                continue
    except BaseException as interrupted:
        # Kill the owned group even if its leader exited while a descendant
        # still holds a pipe. Never wait indefinitely to reap a cancelled CLI.
        try:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=PROCESS_REAP_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProcessCleanupError(interrupted, process.pid) from exc
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


@dataclass
class _Task:
    cancelled: threading.Event
    result: Future[dict[str, Any]]
    thread: threading.Thread


class CallBoundTasks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._tasks: dict[str, _Task] = {}

    def submit(
        self, identifier: str, work: Callable[[threading.Event], dict[str, Any]],
    ) -> None:
        cancelled = threading.Event()
        result: Future[dict[str, Any]] = Future()

        def execute() -> None:
            try:
                result.set_result(work(cancelled))
            except BaseException as exc:
                result.set_exception(exc)

        with self._lock:
            if self._closed:
                raise ToolBridgeBusy("role turn ended")
            if sum(not task.result.done() for task in self._tasks.values()) >= MAX_ACTIVE_OPERATIONS:
                raise ToolBridgeBusy("role tool is busy; wait for an active command")
            thread = threading.Thread(target=execute, daemon=True)
            self._tasks[identifier] = _Task(cancelled, result, thread)
            try:
                thread.start()
            except BaseException:
                del self._tasks[identifier]
                raise

    def _task(self, identifier: str) -> _Task:
        with self._lock:
            if identifier not in self._tasks:
                raise ValueError("command does not belong to this role turn")
            return self._tasks[identifier]

    def wait(self, identifier: str, timeout: float) -> dict[str, Any] | None:
        result = self._task(identifier).result
        try:
            return result.result(timeout=timeout)
        except TimeoutError:
            if result.done():
                return result.result()
            return None

    def cancel(self, identifier: str) -> None:
        self._task(identifier).cancelled.set()

    def close(self, timeout: float) -> None:
        with self._lock:
            self._closed = True
            tasks = list(self._tasks.items())
            for _, task in tasks:
                task.cancelled.set()
        deadline = time.monotonic() + timeout
        for _, task in tasks:
            task.thread.join(timeout=max(0, deadline - time.monotonic()))
        unfinished = [identifier for identifier, task in tasks if task.thread.is_alive()]
        failures = {}
        for identifier, task in tasks:
            if task.result.done() and (error := task.result.exception()) is not None:
                failures[identifier] = error
        problems = []
        if unfinished:
            problems.append(f"Role command cleanup did not finish: {', '.join(unfinished)}")
        if failures:
            problems.append(f"Role commands failed: {', '.join(failures)}")
        if problems:
            raise RuntimeError("; ".join(problems)) from next(iter(failures.values()), None)
