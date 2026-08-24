"""Interrupt Agent tool calls that foreground-wait on healthy external work."""

from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    age_seconds: float
    argv: tuple[str, ...]


def _process_snapshot(proc_root: Path = Path("/proc")) -> dict[int, ProcessInfo]:
    if os.name == "nt" or not proc_root.is_dir():
        return {}
    try:
        uptime = float((proc_root / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    snapshot: dict[int, ProcessInfo] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            _head, _separator, tail = (entry / "stat").read_text().rpartition(")")
            fields = tail.split()
            ppid = int(fields[1])
            started_at = float(fields[19]) / ticks
            argv = tuple(
                value.decode(errors="replace")
                for value in (entry / "cmdline").read_bytes().split(b"\0")
                if value
            )
        except (OSError, ValueError, IndexError):
            continue
        if argv:
            snapshot[pid] = ProcessInfo(
                pid=pid,
                ppid=ppid,
                age_seconds=max(0.0, uptime - started_at),
                argv=argv,
            )
    return snapshot


def _descends_from(
    pid: int,
    root_pid: int,
    processes: Mapping[int, ProcessInfo],
) -> bool:
    seen: set[int] = set()
    current = pid
    while current in processes and current not in seen:
        if current == root_pid:
            return True
        seen.add(current)
        current = processes[current].ppid
    return False


def _direct_shell_command(process: ProcessInfo) -> str:
    if Path(process.argv[0]).name not in {"bash", "dash", "sh", "zsh"}:
        return ""
    try:
        index = process.argv.index("-c")
        return process.argv[index + 1]
    except (ValueError, IndexError):
        return ""


def foreground_wait_shells(
    processes: Mapping[int, ProcessInfo],
    *,
    root_pid: int,
    minimum_age_seconds: float = 60.0,
) -> tuple[ProcessInfo, ...]:
    """Return direct Agent shell calls blocked only on a long local wait."""
    shell_pids: set[int] = set()
    for process in processes.values():
        if (
            process.age_seconds < minimum_age_seconds
            or not _descends_from(process.pid, root_pid, processes)
        ):
            continue
        executable = Path(process.argv[0]).name
        if (
            executable == "tail"
            and any(
                arg == "--pid" or arg.startswith("--pid=")
                for arg in process.argv
            )
            and any(
                arg in {"-f", "--follow", "--follow=name", "--follow=descriptor"}
                for arg in process.argv
            )
            and "/dev/null" in process.argv
        ):
            shell_pids.add(process.pid)
            continue
        if executable == "sleep":
            try:
                duration = float(process.argv[1])
            except (IndexError, ValueError):
                duration = 0.0
            if duration >= minimum_age_seconds:
                parent = processes.get(process.ppid)
                if parent is not None and Path(parent.argv[0]).name in {
                    "bash",
                    "dash",
                    "sh",
                    "zsh",
                }:
                    if _direct_shell_command(parent):
                        shell_pids.add(parent.pid)
                else:
                    shell_pids.add(process.pid)
            continue
        parent = processes.get(process.ppid)
        if parent is None:
            continue
        command = _direct_shell_command(parent)
        if not command:
            continue
        if (
            executable.startswith("python")
            and (
                (
                    "pidfd_open" in command
                    and "select.select" in command
                )
                or "inotify_init" in command
            )
        ):
            shell_pids.add(parent.pid)
    return tuple(
        processes[pid]
        for pid in sorted(shell_pids)
        if pid in processes
    )


class ForegroundWaitGuard:
    """Resident daemon guard for direct shell waits during external work."""

    def __init__(
        self,
        *,
        project_workdir: Path,
        stop_event: threading.Event,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        root_pid: int | None = None,
        interval_seconds: float = 5.0,
        minimum_age_seconds: float = 15.0,
    ) -> None:
        self.project_workdir = Path(project_workdir)
        self.stop_event = stop_event
        self.on_event = on_event
        self.root_pid = int(root_pid or os.getpid())
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.minimum_age_seconds = max(1.0, float(minimum_age_seconds))
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="argus-foreground-wait-guard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stopped.set()
        thread.join(timeout=self.interval_seconds + 1.0)

    def _external_work_waitable(self) -> bool:
        from ..engineer.external_work import scan_external_work

        try:
            return any(
                status.waitable
                for status in scan_external_work(self.project_workdir)
            )
        except Exception:  # noqa: BLE001 - guard failure never stops the daemon
            log.exception("foreground wait guard could not inspect external work")
            return False

    @staticmethod
    def _terminate_shell(
        shell: ProcessInfo,
        processes: Mapping[int, ProcessInfo],
    ) -> tuple[int, ...]:
        descendants = sorted(
            (
                process.pid
                for process in processes.values()
                if process.pid != shell.pid
                and _descends_from(process.pid, shell.pid, processes)
            ),
            key=lambda pid: processes[pid].age_seconds,
        )
        terminated: list[int] = []
        for pid in (*descendants, shell.pid):
            try:
                os.kill(pid, signal.SIGTERM)
                terminated.append(pid)
            except (OSError, ProcessLookupError):
                continue
        return tuple(terminated)

    def _run(self) -> None:
        while not self._stopped.wait(self.interval_seconds):
            if self.stop_event.is_set():
                return
            if not self._external_work_waitable():
                continue
            processes = _process_snapshot()
            for shell in foreground_wait_shells(
                processes,
                root_pid=self.root_pid,
                minimum_age_seconds=self.minimum_age_seconds,
            ):
                terminated = self._terminate_shell(shell, processes)
                if not terminated or self.on_event is None:
                    continue
                try:
                    self.on_event({
                        "type": "life.foreground_wait.interrupted",
                        "shell_pid": shell.pid,
                        "terminated_pids": list(terminated),
                        "command": _direct_shell_command(shell),
                        "minimum_age_seconds": self.minimum_age_seconds,
                        "text": (
                            "interrupted a foreground wait while healthy "
                            "external work continued"
                        ),
                    })
                except Exception:  # noqa: BLE001 - observability is fail-soft
                    log.exception("foreground wait guard event failed")


__all__ = [
    "ForegroundWaitGuard",
    "ProcessInfo",
    "foreground_wait_shells",
]
