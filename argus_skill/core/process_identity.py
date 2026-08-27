"""Portable process identity reads for long-lived ownership records.

Linux PIDs are reusable, so a durable owner is identified by both its PID and
the process start tick from procfs.  On platforms without readable procfs the
helpers deliberately fall back to the existing PID liveness check.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from .daemon_lock import is_pid_running


def _proc_stat(proc_root: Path, pid: int) -> tuple[int, str]:
    text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    suffix = text.rpartition(") ")[2].split()
    if len(suffix) < 20:
        raise RuntimeError(f"malformed {proc_root}/{pid}/stat")
    return int(suffix[1]), suffix[19]


def read_process_metadata(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Read one race-aware Linux process identity without GPU dependencies."""
    process_root = proc_root / str(pid)
    try:
        ppid, start_time_ticks = _proc_stat(proc_root, pid)
        executable = str((process_root / "exe").resolve(strict=True))
        cwd = str((process_root / "cwd").resolve(strict=True))
        cmdline_bytes = (process_root / "cmdline").read_bytes()
    except OSError as exc:
        return {
            "metadata_available": False,
            "process_present_after_metadata_read": process_root.exists(),
            "metadata_error": f"{type(exc).__name__}: {exc}",
            "ppid": None,
            "start_time_ticks": None,
            "executable": None,
            "cwd": None,
            "cmdline": None,
            "cmdline_sha256": None,
        }
    return {
        "metadata_available": True,
        "process_present_after_metadata_read": True,
        "metadata_error": "",
        "ppid": ppid,
        "start_time_ticks": start_time_ticks,
        "executable": executable,
        "cwd": cwd,
        "cmdline": (
            cmdline_bytes.replace(b"\0", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        ),
        "cmdline_sha256": hashlib.sha256(cmdline_bytes).hexdigest(),
    }


def read_process_start_ticks(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> str | None:
    """Read the PID-reuse discriminator without requiring exe/cwd access."""
    try:
        _ppid, start_time_ticks = _proc_stat(proc_root, int(pid))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return start_time_ticks


def capture_process_identity(pid: int) -> dict[str, Any]:
    """Return the durable identity fields available for *pid*."""
    identity: dict[str, Any] = {"pid": int(pid)}
    start_ticks = read_process_start_ticks(int(pid))
    if start_ticks is not None:
        identity["start_time_ticks"] = start_ticks
    return identity


def process_identity_is_running(
    pid: int,
    identity: Mapping[str, Any] | None,
    *,
    pid_is_running: Callable[[int], bool] = is_pid_running,
    proc_root: Path = Path("/proc"),
) -> bool:
    """Check PID liveness and, when recorded/readable, its process start tick."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0 or not pid_is_running(value):
        return False
    if not isinstance(identity, Mapping):
        return True
    try:
        recorded_pid = int(identity.get("pid") or value)
    except (TypeError, ValueError):
        return False
    if recorded_pid != value:
        return False
    expected_ticks = identity.get("start_time_ticks")
    if expected_ticks in (None, ""):
        return True
    actual_ticks = read_process_start_ticks(value, proc_root=proc_root)
    if actual_ticks is None:
        # Non-Linux/minimal environments retain the historical PID check.
        return True
    return str(actual_ticks) == str(expected_ticks)


__all__ = [
    "capture_process_identity",
    "process_identity_is_running",
    "read_process_metadata",
    "read_process_start_ticks",
]
