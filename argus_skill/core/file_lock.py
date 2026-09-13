"""Small cross-platform advisory file-lock primitives."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import BinaryIO, Callable, Iterator, TextIO

import portalocker

# Lock contention is bounded so a crashed holder is reported at the state boundary.
DEFAULT_FILE_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_FILE_LOCK_POLL_SECONDS = 0.05
_WAIT_BUDGET: ContextVar[tuple[float, Callable[[], bool] | None] | None] = ContextVar("argus_file_lock_wait_budget", default=None)


class FileLockCancelled(RuntimeError):
    """An explicitly cancellable state read stopped before acquiring its lock."""


def current_file_lock_wait_budget() -> tuple[float, Callable[[], bool] | None] | None:
    """Let state stores with an additional thread lock inherit the same budget."""
    return _WAIT_BUDGET.get()


@contextmanager
def bounded_file_lock_wait(
    *, timeout_seconds: float, cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Bound nested read-lock waits in this thread without changing writers."""
    previous = _WAIT_BUDGET.get()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    callback = cancelled
    if previous is not None:
        deadline = min(deadline, previous[0])
        callback = lambda: bool((cancelled and cancelled()) or (previous[1] and previous[1]()))
    token = _WAIT_BUDGET.set((deadline, callback))
    try:
        yield
    finally:
        _WAIT_BUDGET.reset(token)


@contextmanager
def exclusive_file_lock(
    handle: BinaryIO | TextIO,
    *,
    timeout_seconds: float = DEFAULT_FILE_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_FILE_LOCK_POLL_SECONDS,
    lock_name: str = "file lock",
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Hold an exclusive advisory lock, failing instead of waiting forever."""
    timeout = max(0.0, float(timeout_seconds))
    poll = max(0.001, float(poll_seconds))
    deadline = time.monotonic() + timeout
    budget = _WAIT_BUDGET.get()
    if budget is not None:
        deadline = min(deadline, budget[0])
        timeout = max(0.0, deadline - time.monotonic())
    while True:
        if (cancelled and cancelled()) or (budget and budget[1] and budget[1]()):
            raise FileLockCancelled(f"cancelled while acquiring {lock_name}")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            break
        except portalocker.exceptions.LockException as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out acquiring {lock_name} after {timeout:g}s"
                ) from exc
            time.sleep(poll)
    try:
        yield
    finally:
        portalocker.unlock(handle)


__all__ = [
    "DEFAULT_FILE_LOCK_POLL_SECONDS",
    "DEFAULT_FILE_LOCK_TIMEOUT_SECONDS",
    "FileLockCancelled",
    "bounded_file_lock_wait",
    "current_file_lock_wait_budget",
    "exclusive_file_lock",
]
