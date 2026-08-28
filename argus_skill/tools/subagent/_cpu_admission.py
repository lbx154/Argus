"""CPU admission and lease selection for durable background jobs."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no detached subagent support
    fcntl = None  # type: ignore[assignment]

_ACTIVE_CPU_LEASE_STATES = frozenset({
    "starting", "preflight", "waiting_resource", "running",
})
_STARTING_LEASE_GRACE_SECONDS = 60.0
_PROCESS_LOCK = threading.Lock()


class CpuAdmissionError(ValueError):
    """A requested CPU allocation cannot be admitted safely."""


def available_cpu_ids() -> tuple[int, ...]:
    """CPUs this process is currently allowed to use."""
    get_affinity = getattr(os, "sched_getaffinity", None)
    if not callable(get_affinity):
        return ()
    return tuple(sorted(int(cpu_id) for cpu_id in get_affinity(0)))


def _normalized_cpu_ids(value: object, *, field: str) -> tuple[int, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        raw_values: Iterable[object] = value.split(",")
    elif isinstance(value, Sequence):
        raw_values = value
    else:
        raise CpuAdmissionError(f"{field} must be a comma-separated list of CPU ids")

    cpu_ids: list[int] = []
    for raw in raw_values:
        text = str(raw).strip()
        if not text:
            continue
        try:
            cpu_id = int(text)
        except ValueError as exc:
            raise CpuAdmissionError(f"{field} contains non-integer CPU id {text!r}") from exc
        if cpu_id < 0:
            raise CpuAdmissionError(f"{field} contains negative CPU id {cpu_id}")
        cpu_ids.append(cpu_id)
    if len(cpu_ids) != len(set(cpu_ids)):
        raise CpuAdmissionError(f"{field} contains duplicate CPU ids")
    return tuple(cpu_ids)


def _task_lease_is_active(
    task: Mapping[str, Any],
    *,
    is_pid_alive: Callable[[int], bool],
    now: float,
) -> bool:
    if str(task.get("state") or "") not in _ACTIVE_CPU_LEASE_STATES:
        return False
    for key in ("pid", "worker_pid", "submitter_pid"):
        try:
            pid = int(task.get(key) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and is_pid_alive(pid):
            return True
    if str(task.get("state") or "") != "starting":
        return False
    try:
        submitted_at = float(task.get("submitted_at") or 0.0)
    except (TypeError, ValueError):
        submitted_at = 0.0
    return submitted_at > 0 and now - submitted_at < _STARTING_LEASE_GRACE_SECONDS


def leased_cpu_ids(
    tasks: Iterable[Mapping[str, Any]],
    *,
    is_pid_alive: Callable[[int], bool],
    now: float | None = None,
) -> tuple[int, ...]:
    """CPU ids held by live participating Argus subagent tasks."""
    checked_at = time.time() if now is None else float(now)
    leased: set[int] = set()
    for task in tasks:
        if not _task_lease_is_active(task, is_pid_alive=is_pid_alive, now=checked_at):
            continue
        try:
            leased.update(_normalized_cpu_ids(task.get("cpu_ids"), field="active task cpu_ids"))
        except CpuAdmissionError as exc:
            task_id = str(task.get("task_id") or "<unknown>")
            raise CpuAdmissionError(
                f"active task {task_id!r} has malformed CPU lease metadata: {exc}"
            ) from exc
    return tuple(sorted(leased))


def select_cpu_ids(
    *,
    cpu_count: object = 0,
    cpu_ids: object = None,
    tasks: Iterable[Mapping[str, Any]],
    is_pid_alive: Callable[[int], bool],
) -> tuple[int, ...]:
    """Select a non-overlapping CPU allocation without mutating project state."""
    try:
        count = int(cpu_count or 0)
    except (TypeError, ValueError) as exc:
        raise CpuAdmissionError("cpu_count must be an integer") from exc
    if count < 0:
        raise CpuAdmissionError("cpu_count must be non-negative")
    requested_ids = _normalized_cpu_ids(cpu_ids, field="cpu_ids")
    if count and requested_ids:
        raise CpuAdmissionError("cpu_count and cpu_ids are mutually exclusive")
    if not count and not requested_ids:
        return ()

    available = available_cpu_ids()
    if not available:
        raise CpuAdmissionError(
            "CPU affinity enforcement is unavailable on this platform"
        )
    leased = leased_cpu_ids(tasks, is_pid_alive=is_pid_alive)
    leased_set = set(leased)
    available_set = set(available)

    if requested_ids:
        outside = tuple(cpu_id for cpu_id in requested_ids if cpu_id not in available_set)
        if outside:
            raise CpuAdmissionError(
                f"requested CPU ids {list(outside)} are outside process affinity "
                f"{list(available)}"
            )
        conflicts = tuple(cpu_id for cpu_id in requested_ids if cpu_id in leased_set)
        if conflicts:
            raise CpuAdmissionError(
                f"requested CPU ids {list(conflicts)} are already leased by live tasks"
            )
        return requested_ids

    free = tuple(cpu_id for cpu_id in available if cpu_id not in leased_set)
    if len(free) < count:
        raise CpuAdmissionError(
            f"need {count} distinct CPUs, only {len(free)} are free "
            f"(affinity={list(available)}, leased={list(leased)})"
        )
    return free[:count]


def apply_current_process_affinity(cpu_ids: Sequence[int]) -> None:
    """Constrain this worker; its subsequently spawned job inherits the mask."""
    selected = tuple(int(cpu_id) for cpu_id in cpu_ids)
    if not selected:
        return
    set_affinity = getattr(os, "sched_setaffinity", None)
    get_affinity = getattr(os, "sched_getaffinity", None)
    if not callable(set_affinity) or not callable(get_affinity):
        raise RuntimeError("CPU affinity enforcement is unavailable on this platform")
    allowed = {int(cpu_id) for cpu_id in get_affinity(0)}
    if not set(selected) <= allowed:
        raise RuntimeError(
            f"CPU allocation {list(selected)} is no longer within affinity "
            f"{sorted(allowed)}"
        )
    set_affinity(0, set(selected))
    applied = {int(cpu_id) for cpu_id in get_affinity(0)}
    if applied != set(selected):
        raise RuntimeError(
            f"failed to enforce CPU affinity {list(selected)}; applied {sorted(applied)}"
        )


@contextmanager
def cpu_admission_lock(root: Path | str = ".") -> Iterator[None]:
    """Serialize admission without creating project artifacts on rejection."""
    if fcntl is None:
        with _PROCESS_LOCK:
            yield
        return
    fd = os.open(Path(root), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


__all__ = [
    "CpuAdmissionError",
    "apply_current_process_affinity",
    "available_cpu_ids",
    "cpu_admission_lock",
    "leased_cpu_ids",
    "select_cpu_ids",
]
