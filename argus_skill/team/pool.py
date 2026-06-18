"""Coordinator control plane: a tiny shared file the lead writes and the
coordinator reads each tick.

``width`` is the target in-flight teammate count, ``state`` is
``running``/``draining``, and every ``update`` refreshes ``lead_heartbeat_ts``
so the coordinator can detect a dead lead and never orphan-spawn.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store

_DEFAULT: dict[str, Any] = {"width": 0, "state": "running", "lead_heartbeat_ts": 0.0}


def _path(root: Path) -> Path:
    return Path(root) / "pool.json"


def _lock(root: Path) -> Path:
    return Path(root) / ".pool.lock"


def read(root: Path) -> dict[str, Any]:
    doc = _store.read_json(_path(root), default=None)
    merged = dict(_DEFAULT)
    if isinstance(doc, dict):
        merged.update(doc)
    return merged


def update(root: Path, *, width: int | None = None, state: str | None = None,
           now: float) -> dict[str, Any]:
    """Merge-write the control file; always refresh the lead heartbeat."""
    with _store.locked(_lock(root)):
        doc = read(root)
        if width is not None:
            doc["width"] = int(width)
        if state is not None:
            doc["state"] = state
        doc["lead_heartbeat_ts"] = now
        _store.atomic_write_json(_path(root), doc)
        return doc
