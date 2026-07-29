"""Process-tree helpers for safely settling completed MLE campaign slots."""

from __future__ import annotations

import os
import signal
from pathlib import Path


def process_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def process_parent_map() -> dict[int, int]:
    parents: dict[int, int] = {}
    for proc_dir in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(proc_dir.name)
            status = (proc_dir / "status").read_text(errors="replace")
            ppid_line = next(line for line in status.splitlines() if line.startswith("PPid:"))
            parents[pid] = int(ppid_line.split()[1])
        except (OSError, StopIteration, ValueError, IndexError):
            continue
    return parents


def descendant_pids(root_pid: int, parents: dict[int, int]) -> set[int]:
    """Return all transitive children from one PID -> PPID snapshot."""
    descendants: set[int] = set()
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for child, child_parent in parents.items():
            if child_parent != parent or child in descendants:
                continue
            descendants.add(child)
            frontier.append(child)
    return descendants


def stop_descendant_argus_daemon(root_pid: int) -> bool:
    """TERM the Argus daemon while leaving its wrapper alive to write results."""
    descendants = descendant_pids(root_pid, process_parent_map())
    for pid in sorted(descendants, reverse=True):
        parts = process_cmdline(pid)
        if "--daemon-fg" not in parts:
            continue
        if not any(Path(part).name == "argus-skill" for part in parts):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True
    return False


__all__ = [
    "descendant_pids",
    "process_cmdline",
    "process_parent_map",
    "stop_descendant_argus_daemon",
]
