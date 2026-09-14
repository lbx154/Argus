"""Pool control plane: a tiny shared file the lead writes (its intent) and the
resident Curator reads each tick.

``width`` is the target in-flight teammate count. It is **absent until the lead
sets it**; an explicit ``0`` means *pause* (target zero in flight) — distinct
from unset, which lets the Curator fall back to its own default width.
``state`` is ``running``/``draining``.

The daemon-resident Curator owns teammate process lifetime, so this control
plane contains no liveness timestamp.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import _store

_DEFAULT: dict[str, Any] = {"state": "running"}
_STATES = frozenset({"running", "draining", "dissolved"})
# Width is an admission/resource ceiling, not a teammate work deadline.
_MAX_WIDTH_ENV = "ARGUS_TEAM_MAX_WIDTH"


def _path(root: Path) -> Path:
    return Path(root) / "pool.json"


def _lock(root: Path) -> Path:
    return Path(root) / ".pool.lock"


def read(root: Path) -> dict[str, Any]:
    doc = _store.read_json(_path(root), default=None)
    merged = dict(_DEFAULT)
    if isinstance(doc, dict):
        merged.update(doc)
        merged.pop("lead_heartbeat_ts", None)
    return merged


def update(
    root: Path,
    *,
    width: int | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Merge-write the lead's width/state intent.

    ``width=0`` is a real value (pause), so it is written like any other; only
    ``None`` (the default) leaves width untouched.
    """
    with _store.locked(_lock(root)):
        doc = read(root)
        if width is not None:
            normalized_width = int(width)
            maximum_width = int(os.environ.get(_MAX_WIDTH_ENV, "64"))
            if normalized_width < 0 or maximum_width <= 0:
                raise ValueError("team pool width bounds must be non-negative")
            if normalized_width > maximum_width:
                raise ValueError(
                    f"team pool width {normalized_width} exceeds "
                    f"{_MAX_WIDTH_ENV}={maximum_width}"
                )
            doc["width"] = normalized_width
        if state is not None:
            if state not in _STATES:
                raise ValueError(f"unsupported team pool state: {state!r}")
            doc["state"] = state
        _store.atomic_write_json(_path(root), doc)
        return doc
