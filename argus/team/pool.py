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


def _provider_concurrency() -> int:
    """Host-wide provider call limit (0 = unlimited), read through the knob layer."""
    try:
        from ..core.knobs import resolve_knob

        return max(0, int(str(resolve_knob("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "0").value).strip() or 0))
    except Exception:  # noqa: BLE001 — an unreadable knob must not break pool sizing
        return 0


def default_width() -> int:
    """Execution concurrency is independent of the number of research candidates.

    Teammates share the host's provider slots with the lead that dispatched
    them (its Manager supervision, Engineer rounds and Reviewer). On the stable
    web trial (2026-09-16) two workers held both of a two-slot host's calls and
    the mission itself sat in provider cooldown for as long as they ran, so the
    width also leaves one slot for the lead whenever a provider limit is set.
    """
    width = int(os.environ.get("ARGUS_TEAM_DEFAULT_WIDTH", "2"))
    maximum = int(os.environ.get(_MAX_WIDTH_ENV, "64"))
    if width <= 0 or maximum <= 0:
        raise ValueError("team concurrency limits must be positive")
    width = min(width, maximum)
    slots = _provider_concurrency()
    if slots > 0:
        width = min(width, max(1, slots - 1))
    return width


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
    cooldown_until: float | None = None,
) -> dict[str, Any]:
    """Merge-write the lead's width/state intent.

    ``width=0`` is a real value (pause), so it is written like any other; only
    ``None`` (the default) leaves width untouched. ``cooldown_until`` is a
    wall-clock instant before which the Curator must not spawn into this pool:
    a teammate that was turned away by the provider sets it so its siblings
    are not spawned into the same wall one after another.
    """
    with _store.locked(_lock(root)):
        doc = read(root)
        if cooldown_until is not None:
            doc["cooldown_until"] = max(0.0, float(cooldown_until))
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
