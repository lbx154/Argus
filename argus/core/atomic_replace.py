"""Atomic file writes: ``os.replace`` that tolerates Windows' transient
sharing violations, and the one write-then-replace helper built on it."""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import IO, Any

# Windows refuses to replace a file another handle still has open without
# FILE_SHARE_DELETE (antivirus and indexer scans of a just-written file are the
# usual holders). Those holds last milliseconds, so a short bounded retry turns
# them into a wait instead of a failed write.
_RETRY_SECONDS = 2.0
_RETRY_POLL_SECONDS = 0.02


def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Atomically move ``source`` over ``target``."""
    if sys.platform != "win32":
        os.replace(source, target)
        return
    deadline = time.monotonic() + _RETRY_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline or not Path(source).exists():
                raise
            time.sleep(_RETRY_POLL_SECONDS)


def write_atomic(
    path: str | os.PathLike[str],
    data: str | bytes,
    *,
    fsync: bool = False,
    mode: int | None = None,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """Write ``data`` to a fresh sibling file, then move it over ``path``.

    Readers see the old file or the new one, never a partial write. ``str`` is
    written in text mode (``newline`` as for :func:`open`, so by default like
    ``Path.write_text``), ``bytes`` unchanged. With ``mode`` the file gets exactly
    those permission bits; without it the process umask applies, as for
    ``open``. ``fsync`` flushes the data to disk before the move. The parent
    directory must exist; the temporary file never outlives the call.
    """
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600 if mode is not None else 0o666)
    try:
        handle: IO[Any] = (
            os.fdopen(descriptor, "w", encoding=encoding, newline=newline)
            if isinstance(data, str)
            else os.fdopen(descriptor, "wb")
        )
        with handle:
            if mode is not None:
                os.chmod(temporary, mode)
            handle.write(data)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
