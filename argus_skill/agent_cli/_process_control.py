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
from typing import Any

from ..core.daemon_lock import is_process_group_running


def windows_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return Windows-only options for a non-interactive child process.

    A GUI host has no console to inherit.  Console-subsystem executables and
    npm ``.cmd`` wrappers otherwise create a visible black window even when all
    stdio handles are redirected.  This helper deliberately returns no POSIX
    session option so daemon maintenance commands can reuse it unchanged.
    """
    if os.name != "nt":
        return {}
    options: dict[str, Any] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    startup_factory = getattr(subprocess, "STARTUPINFO", None)
    if callable(startup_factory):
        startup = startup_factory()
        startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        options["startupinfo"] = startup
    return options


def background_subprocess_kwargs() -> dict[str, Any]:
    """Return non-interactive spawn options without a Windows console flash.

    Agent CLIs are invoked through pipes, never an interactive terminal.  npm
    `.cmd` launchers and console-subsystem CLIs otherwise create a brief black
    window for every Manager call or CLI switch.  Keep POSIX's private session
    behavior unchanged, and combine `CREATE_NO_WINDOW` with STARTUPINFO on
    Windows for both native executables and batch wrappers.
    """
    if os.name != "nt":
        return {"start_new_session": True}
    return windows_hidden_subprocess_kwargs()


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
        return is_process_group_running(process_group_id)

    @classmethod
    def _wait_process_group_exit(cls, process_group_id: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not cls._process_group_alive(process_group_id):
                return True
            time.sleep(0.05)
        return not cls._process_group_alive(process_group_id)

    @staticmethod
    def _detached_child_process_groups(process_id: int) -> set[int]:
        try:
            output = subprocess.run(
                ["ps", "-axo", "ppid=,pgid="],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        groups: set[int] = set()
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2:
                continue
            try:
                parent_pid, process_group_id = map(int, fields)
            except ValueError:
                continue
            if parent_pid == process_id and process_group_id > 0:
                groups.add(process_group_id)
        groups.discard(process_id)
        groups.discard(os.getpgrp())
        return groups

    @classmethod
    def _terminate_process(
        cls,
        process: subprocess.Popen[str],
        *,
        include_detached_children: bool = False,
    ) -> None:
        if os.name != "nt":
            process_group_id = process.pid
            child_groups: set[int] = set()
            if include_detached_children:
                try:
                    os.killpg(process_group_id, signal.SIGSTOP)
                except (ProcessLookupError, OSError):
                    pass
                child_groups = cls._detached_child_process_groups(process.pid)
                for group_id in child_groups:
                    try:
                        os.killpg(group_id, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                if not child_groups:
                    return
            except OSError:
                if process.poll() is None:
                    process.terminate()
                elif not child_groups:
                    return
            if include_detached_children:
                try:
                    os.killpg(process_group_id, signal.SIGCONT)
                except (ProcessLookupError, OSError):
                    pass
            try:
                process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass
            live_groups = {
                group_id
                for group_id in {process_group_id, *child_groups}
                if not cls._wait_process_group_exit(group_id, 2.0)
            }
            for group_id in live_groups:
                try:
                    os.killpg(group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    if group_id == process_group_id and process.poll() is None:
                        process.kill()
                cls._wait_process_group_exit(group_id, 5.0)
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
