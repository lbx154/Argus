"""Process-group lifecycle: emitting output lines, resolving the executable
path, and terminating the child (and its whole process group) on watchdog /
external-interrupt / KeyboardInterrupt paths. Extracted verbatim from
``agent_cli_runner.py``.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time


class ProcessControlMixin:
    """Output emission + executable resolution + process-group termination."""

    def _emit(self, stream: str, line: str) -> None:
        if self.event_callback is None:
            return
        self.event_callback(stream, line)

    @staticmethod
    def _stream_name(stream: str, run_label: str | None) -> str:
        if not run_label:
            return stream
        return f"{run_label}.{stream}"

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        if os.path.dirname(executable) or "/" in executable or "\\" in executable:
            return executable
        resolved = shutil.which(executable)
        if resolved:
            return resolved
        return executable

    @staticmethod
    def _process_group_alive(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @classmethod
    def _wait_process_group_exit(cls, process_group_id: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not cls._process_group_alive(process_group_id):
                return True
            time.sleep(0.05)
        return not cls._process_group_alive(process_group_id)

    @classmethod
    def _terminate_process(cls, process: subprocess.Popen[str]) -> None:
        if os.name != "nt":
            process_group_id = process.pid
            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                if process.poll() is not None:
                    return
                process.terminate()
            try:
                process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass
            if not cls._wait_process_group_exit(process_group_id, 2.0):
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    if process.poll() is None:
                        process.kill()
                cls._wait_process_group_exit(process_group_id, 5.0)
            if process.poll() is None:
                try:
                    process.wait(timeout=0.1)
                except (subprocess.TimeoutExpired, ChildProcessError):
                    pass
            return

        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            # A child stuck in uninterruptible sleep (D-state) / under ptrace may
            # not be reaped immediately even after SIGKILL, so this wait can time
            # out again. Mirror CPython's subprocess.run: swallow it and give up
            # gracefully rather than letting it abort the caller.
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
