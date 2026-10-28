"""Atomic per-user trial configuration writes; never store keys in browser state."""
from __future__ import annotations

from pathlib import Path

from ..core.atomic_replace import write_atomic


def write_private(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_atomic(path, content, fsync=True, mode=0o600)
