"""Small cross-platform advisory file-lock primitives."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import BinaryIO, Iterator, TextIO

import portalocker

# Lock contention is bounded so a crashed holder is reported at the state boundary.
DEFAULT_FILE_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_FILE_LOCK_POLL_SECONDS = 0.05


@contextmanager
def exclusive_file_lock(
    handle: BinaryIO | TextIO,
    *,
    timeout_seconds: float = DEFAULT_FILE_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_FILE_LOCK_POLL_SECONDS,
    lock_name: str = "file lock",
) -> Iterator[None]:
    """Hold an exclusive advisory lock, failing instead of waiting forever."""
    timeout = max(0.0, float(timeout_seconds))
    poll = max(0.001, float(poll_seconds))
    deadline = time.monotonic() + timeout
    while True:
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
    "exclusive_file_lock",
]
