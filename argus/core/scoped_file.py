"""Open a canonical regular file, pinning each parent directory on POSIX.

Platforms without ``dir_fd`` support (Windows) cannot pin parents, so they
check every component for symlinks and reparse points before opening and then
confirm the opened handle is the same regular file the path names.
"""
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
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(absolute.anchor, directory_flags)
            for part in absolute.parts[1:-1]:
                child = os.open(part, directory_flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(absolute.name, flags, 0o600, dir_fd=directory)
        else:
            _reject_link_components(absolute)
            descriptor = os.open(absolute, flags, 0o600)
            # Re-check after opening: a parent swapped for a link during the
            # open is visible now, and the handle must be the file the
            # canonical path still names.
            _reject_link_components(absolute)
            opened = os.fstat(descriptor)
            named = os.lstat(absolute)
            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise ValueError("file changed while it was being opened")
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


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# Reparse tags with the name-surrogate bit redirect to another path (symlinks,
# junctions). Others, such as cloud-sync placeholders, are ordinary files.
_NAME_SURROGATE_TAG_BIT = 0x20000000


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if not getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        return False
    return bool(getattr(info, "st_reparse_tag", _NAME_SURROGATE_TAG_BIT) & _NAME_SURROGATE_TAG_BIT)


def _reject_link_components(absolute: Path) -> None:
    """Refuse a path whose parents or leaf are symlinks or junctions."""
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        if _is_link(info):
            raise ValueError("file path must not traverse a symlink or reparse point")
