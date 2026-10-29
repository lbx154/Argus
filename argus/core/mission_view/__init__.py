"""Event-sourced Mission View read model, computed from the event log on read.

The reducer only consumes structured event fields. Free-form text may be shown
as detail, but it is never parsed to infer scientific state.

This package was split out of a single ~1.5k-line module. It is organized as:

- ``_view_state``: schema defaults and the projected event types.
- ``_reduce_helpers``: small shared primitives (text/number coercion,
  timeline/role-work upserts) used by every event-family reducer.
- ``_wording``: every sentence shown to a person, keyed by the stable code
  (``kind``) the reducers stamp on roles and timeline rows, in the session's
  language.
- ``_reduce_manager`` / ``_reduce_mission`` / ``_reduce_research``: one
  module per mission-view event family, each exposing a small
  ``reduce_<family>_event(view, event, *, event_type, ts, mission)`` function.
- ``_dispatch``: the stable ``event_type -> family reducer`` table and the
  public ``reduce_mission_view_event``.
- ``_replay``: the view computed from the event log. Nothing is written; each
  process caches the views it computed with the log position consumed, so
  ordinary polling reads only appended bytes.
- ``_snapshot``: live daemon/session merge
  (``snapshot_mission_view`` / ``merge_mission_view_snapshot``).

The public API (every name importable as ``argus.core.mission_view.X``)
is re-exported here so existing imports keep working unchanged.
"""
from __future__ import annotations

from ._dispatch import reduce_mission_view_event
from ._reduce_helpers import _event_id as work_record_event_id
from ._snapshot import (
    awaits_operator,
    merge_mission_view_snapshot,
    operator_objective,
    snapshot_mission_view,
)
from ._view_state import (
    MISSION_VIEW_SCHEMA_VERSION,
    empty_mission_view,
    load_mission_view,
    mission_view_handles_event,
)
from ._wording import (
    conversation_is_chinese,
    say,
    sentences,
    session_is_chinese,
    stage_label,
    uses_cjk,
    written_language,
)

__all__ = [
    "MISSION_VIEW_SCHEMA_VERSION",
    "awaits_operator",
    "empty_mission_view",
    "load_mission_view",
    "merge_mission_view_snapshot",
    "mission_view_handles_event",
    "operator_objective",
    "reduce_mission_view_event",
    "say",
    "sentences",
    "session_is_chinese",
    "snapshot_mission_view",
    "uses_cjk",
    "conversation_is_chinese",
    "written_language",
    "work_record_event_id",
    "stage_label",
]
