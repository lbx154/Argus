"""A file's identity as plain stat facts, compared directly instead of hashed.

Every write changes the size, the modification time or both, and always the
status-change time, which a user cannot set back: a backdated edit still shows.
"""
from __future__ import annotations

import os
from pathlib import Path


def file_facts(path: Path | str) -> list[int]:
    """``[size, mtime_ns, ctime_ns]`` of ``path``; a list so it survives JSON."""
    stat = os.stat(path)
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


__all__ = ["file_facts"]
