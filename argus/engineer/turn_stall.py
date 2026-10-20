"""A turn that stops progressing is the Manager's to judge, not a timer's to kill."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..core.event_catalog import EventType
from .round_manager_wait import manager_direction_revision

# How often the backend consults the watch while a turn shows no progress:
# the delay before a stall is reported and before a Manager restart lands.
LOOK_SECONDS = 60


class TurnStallWatch:
    """Report a stalled Engineer turn once; stop it only on the Manager's word.

    The backend calls this every ``LOOK_SECONDS`` while the turn shows no
    progress: no model stream event and no CPU time in the commands it runs.
    Past ``stall_seconds`` the stall becomes one ``round.stall`` event, which
    asks the Manager to look. A Manager supervision directive issued after
    that is its decision to restart the turn; the directive then reaches the
    next Engineer prompt. Otherwise the turn keeps waiting, and only progress
    followed by a new stall is reported again.
    """

    def __init__(
        self,
        *,
        stall_seconds: float,
        state_root: Path | None,
        run_label: str,
        on_event: Callable[[dict], None] | None,
    ) -> None:
        self.stall_seconds = stall_seconds
        self.state_root = state_root
        self.run_label = run_label
        self.on_event = on_event
        self._idle = 0.0
        self._directive_at_report: str | None = None

    def __call__(self, snapshot: Any) -> str | None:
        idle = float(snapshot.idle_seconds)
        if idle < self._idle:  # progress since the last look ended that stall
            self._directive_at_report = None
        self._idle = idle
        if idle < self.stall_seconds:
            return None
        if self._directive_at_report is None:
            self._directive_at_report = manager_direction_revision(self.state_root)
            self._report(idle, str(getattr(snapshot, "running_tool", "") or ""))
            return None
        current = manager_direction_revision(self.state_root)
        if current and current != self._directive_at_report:
            return "restart: the Manager decided to restart the stalled turn"
        return None

    def _report(self, idle: float, tool: str) -> None:
        if self.on_event is None:
            return
        running = f" while running `{tool}`" if tool else ""
        self.on_event({
            "type": EventType.ROUND_STALL,
            "stall_reason": "turn_stalled",
            "run_label": self.run_label,
            "idle_seconds": int(idle),
            "stalled_since": int(time.time() - idle),
            "running_tool": tool,
            "text": f"the Engineer's turn has made no progress for {int(idle // 60)} min{running}",
        })


__all__ = ["LOOK_SECONDS", "TurnStallWatch"]
