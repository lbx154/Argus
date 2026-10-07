"""Bounded command I/O and owned-child cleanup, independent of Argus runtime."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import math
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

CLEANUP_SECONDS = 5.0


@dataclass(frozen=True)
class OwnedResult:
    returncode: int
    reason: str | None
    cleanup_complete: bool
    stdout_bytes: int
    stderr_bytes: int
    pid: int = 0


def _children() -> list[int]:
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    return [int(value) for value in path.read_text().split()]


def _cleanup_children(process: subprocess.Popen) -> bool:
    started = time.monotonic()
    while True:
        children = _children()
        if not children:
            process.poll()
            return True
        for pid in children:
            # These are direct children of this single-threaded subreaper.
            # They cannot be reused until this process reaps them.
            try:
                os.kill(pid, signal.SIGTERM if time.monotonic() - started < 0.25 else signal.SIGKILL)
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        for pid in children:
            try:
                reaped, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if reaped == process.pid:
                process.returncode = os.waitstatus_to_exitcode(status)
        if time.monotonic() - started >= CLEANUP_SECONDS - 0.5:
            return not _children()
        time.sleep(0.01)


def _supervise(command: list[str], *, parent_pid: int, timeout: float, status_path: Path) -> int:
    stop = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    libc = ctypes.CDLL(None, use_errno=True)
    # Isolated supervisor only: never change subreaper state in the caller.
    if libc.prctl(36, 1, 0, 0, 0) or libc.prctl(1, signal.SIGTERM, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "Cannot establish owned process supervision")
    if os.getppid() != parent_pid:
        stop = True
    if stop:
        status_path.write_text(json.dumps({
            "returncode": 130, "reason": "cancelled", "cleanup_complete": True,
        }))
        return 130
    reason = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    except OSError:
        status_path.write_text(json.dumps({
            "returncode": 127, "reason": "launch_error", "cleanup_complete": True,
        }))
        return 127
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if stop:
                reason = "cancelled"
                break
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            time.sleep(0.01)
    finally:
        cleaned = _cleanup_children(process)
    code = process.returncode if process.returncode is not None else -1
    status_path.write_text(json.dumps({
        "returncode": code, "reason": reason, "cleanup_complete": cleaned, "pid": process.pid,
    }))
    return 0


def _terminate(process: subprocess.Popen, *, supervised: bool) -> bool | None:
    if supervised:
        if process.poll() is None:
            process.terminate()
    elif os.name == "nt":
        from argus.core.windows_job import terminate_owned_process

        return terminate_owned_process(process)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_owned(
    command: list[str], *, cwd: Path, environment: Mapping[str, str],
    timeout: float, stdout_path: Path, stderr_path: Path | None = None,
    output_limit: int, cancelled: Callable[[], bool] | None = None,
) -> OwnedResult:
    """Bound each output stream and stop private descendants before returning."""
    if not math.isfinite(timeout) or timeout <= 0 or output_limit <= 0:
        raise ValueError("Command timeout and output limit must be positive")
    supervised = sys.platform == "linux"
    messages: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=16)
    readers_stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="pr-gate-owner-") as temporary, contextlib.ExitStack() as handles:
        outputs = {"stdout": handles.enter_context(stdout_path.open("wb"))}
        if stderr_path is not None:
            outputs["stderr"] = handles.enter_context(stderr_path.open("wb"))
        status_path = Path(temporary) / "status.json"
        argv = (
            [sys.executable, str(Path(__file__).resolve()), "--supervise",
             "--parent", str(os.getpid()), "--timeout", str(timeout),
             "--status", str(status_path), "--", *command]
            if supervised else command
        )
        options = dict(
            cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if stderr_path is not None else subprocess.STDOUT,
        )
        if os.name == "nt":
            from argus.core.windows_job import spawn_owned_process

            process = spawn_owned_process(argv, **options)
        else:
            process = subprocess.Popen(argv, start_new_session=True, **options)
        reason = None
        cleanup_ok = True
        deadline = time.monotonic() + timeout
        stopping_at = None
        totals = {"stdout": 0, "stderr": 0}
        streams = {"stdout": process.stdout}
        if stderr_path is not None:
            streams["stderr"] = process.stderr

        def terminate() -> None:
            nonlocal cleanup_ok
            if _terminate(process, supervised=supervised) is False:
                cleanup_ok = False

        def read_stream(name: str, stream) -> None:
            try:
                while not readers_stop.is_set():
                    data = os.read(stream.fileno(), 4096)
                    while not readers_stop.is_set():
                        try:
                            messages.put((name, data or None), timeout=0.05)
                            break
                        except queue.Full:
                            continue
                    if not data:
                        break
            finally:
                stream.close()

        readers = [
            threading.Thread(target=read_stream, args=(name, stream), daemon=True)
            for name, stream in streams.items()
        ]
        eof: set[str] = set()
        try:
            for reader in readers:
                reader.start()
            while True:
                if reason is None:
                    if cancelled is not None and cancelled():
                        reason = "cancelled"
                    elif time.monotonic() >= deadline:
                        reason = "timeout"
                if reason is not None and stopping_at is None:
                    stopping_at = time.monotonic()
                    terminate()
                if not supervised and process.poll() is not None and len(eof) < len(streams):
                    terminate()
                try:
                    name, data = messages.get(timeout=0.02)
                except queue.Empty:
                    name, data = "", b""
                if name and data is None:
                    eof.add(name)
                elif name:
                    remaining = max(0, output_limit - totals[name])
                    outputs[name].write(data[:remaining])
                    outputs[name].flush()
                    totals[name] += len(data)
                    if totals[name] > output_limit and reason is None:
                        reason = "output_limit"
                if process.poll() is not None and len(eof) == len(streams):
                    break
                if stopping_at is not None and time.monotonic() - stopping_at > CLEANUP_SECONDS + 1:
                    break
        finally:
            if process.poll() is None:
                terminate()
                try:
                    process.wait(timeout=CLEANUP_SECONDS + 1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            readers_stop.set()
            for reader in readers:
                reader.join(timeout=0.2)
            for output in outputs.values():
                output.close()
            if not supervised and os.name == "nt":
                from argus.core.windows_job import terminate_owned_process

                if terminate_owned_process(process) is False:
                    cleanup_ok = False
            elif not supervised:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        cleaned = cleanup_ok and not any(reader.is_alive() for reader in readers)
        code = process.returncode
        command_pid = process.pid
        if supervised:
            if status_path.exists():
                status = json.loads(status_path.read_text())
                code = status["returncode"]
                reason = reason or status["reason"]
                cleaned = cleaned and status["cleanup_complete"]
                command_pid = status.get("pid", 0)
            else:
                cleaned = False
                reason = reason or "supervision_failed"
        return OwnedResult(code, reason, cleaned, totals["stdout"], totals["stderr"], command_pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--supervise", action="store_true", required=True)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    raise SystemExit(_supervise(
        command, parent_pid=args.parent, timeout=args.timeout, status_path=args.status,
    ))
