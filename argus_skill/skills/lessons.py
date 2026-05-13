"""Compatibility helpers for the legacy lessons module."""
from __future__ import annotations

from pathlib import Path


def default_pending_lessons_dir(base_dir: str | Path | None = None) -> Path:
    if base_dir:
        root = Path(base_dir).expanduser()
        pending = root.parent / "pending_lessons"
    else:
        pending = Path.cwd() / "pending_lessons"
    pending.mkdir(parents=True, exist_ok=True)
    return pending
