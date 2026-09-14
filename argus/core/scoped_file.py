"""Open a canonical regular file, pinning each parent directory on POSIX."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO


def open_regular_file(path: Path, flags: int = os.O_RDONLY) -> BinaryIO:
    absolute = Path(path).absolute()
    if absolute.resolve() != absolute or absolute.is_symlink():
        raise ValueError("file path must be canonical")
    directory: int | None = None
    descriptor: int | None = None
    try:
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(absolute.anchor, directory_flags)
            for part in absolute.parts[1:-1]:
                child = os.open(part, directory_flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(absolute.name, flags, 0o600, dir_fd=directory)
        else:
            descriptor = os.open(absolute, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("file must be a regular file")
        handle = os.fdopen(descriptor, "r+b" if flags & os.O_RDWR else "rb")
        descriptor = None
        return handle
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)
