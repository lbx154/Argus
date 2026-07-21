"""Durable event-sourced Mission View read model.

The reducer only consumes structured event fields. Free-form text may be shown
as detail, but it is never parsed to infer scientific state.

This package was split out of a single ~1.5k-line module. It is organized as:

- ``_view_state``: on-disk schema defaults, file locking, load/bootstrap.
- ``_reduce_helpers``: small shared primitives (text/number coercion,
  timeline/role-work upserts) used by every event-family reducer.
- ``_reduce_manager`` / ``_reduce_mission`` / ``_reduce_research`` /
  ``_reduce_skill`` / ``_reduce_wiki``: one module per mission-view
  event family, each exposing a small ``reduce_<family>_event(view, event,
  *, event_type, ts, mission)`` function.
- ``_dispatch``: the stable ``event_type -> family reducer`` table plus the
  public ``reduce_mission_view_event`` / ``update_mission_view_event``
  entry points.
- ``_snapshot``: live daemon/session merge and disk bootstrap
  (``snapshot_mission_view`` / ``merge_mission_view_snapshot``).

The public API (every name importable as ``argus_skill.core.mission_view.X``)
is re-exported here so existing imports keep working unchanged.
"""
from __future__ import annotations

from ._dispatch import reduce_mission_view_event, update_mission_view_event
from ._snapshot import merge_mission_view_snapshot, snapshot_mission_view
from ._view_state import (
    MISSION_VIEW_FILE,
    MISSION_VIEW_SCHEMA_VERSION,
    empty_mission_view,
    load_mission_view,
    mission_view_handles_event,
)

__all__ = [
    "MISSION_VIEW_FILE",
    "MISSION_VIEW_SCHEMA_VERSION",
    "empty_mission_view",
    "load_mission_view",
    "merge_mission_view_snapshot",
    "mission_view_handles_event",
    "reduce_mission_view_event",
    "snapshot_mission_view",
    "update_mission_view_event",
]
