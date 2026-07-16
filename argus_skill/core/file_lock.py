"""Small cross-platform advisory file-lock primitives."""

from __future__ import annotations

from contextlib import contextmanager
from typing import BinaryIO, Iterator, TextIO

import portalocker


@contextmanager
def exclusive_file_lock(handle: BinaryIO | TextIO) -> Iterator[None]:
    """Hold an exclusive advisory lock on an already-open file handle."""
    portalocker.lock(handle, portalocker.LOCK_EX)
    try:
        yield
    finally:
        portalocker.unlock(handle)


__all__ = ["exclusive_file_lock"]
