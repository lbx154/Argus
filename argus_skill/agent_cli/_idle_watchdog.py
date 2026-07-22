"""Shared staged idle escalation for model-provider calls."""

from __future__ import annotations

from dataclasses import dataclass

WARNING_STAGE = "warning"
STALLED_STAGE = "stalled"
TERMINATE_STAGE = "terminate"


@dataclass
class IdleEscalation:
    """Emit each configured idle stage once until real activity resumes."""

    warning_seconds: float = 0
    stalled_seconds: float = 0
    terminate_seconds: float = 0
    _warning_emitted: bool = False
    _stalled_emitted: bool = False
    _terminate_emitted: bool = False

    def __post_init__(self) -> None:
        self.warning_seconds = max(0.0, float(self.warning_seconds))
        self.stalled_seconds = max(0.0, float(self.stalled_seconds))
        self.terminate_seconds = max(0.0, float(self.terminate_seconds))

    def reset(self) -> None:
        self._warning_emitted = False
        self._stalled_emitted = False
        self._terminate_emitted = False

    def newly_due(self, idle_seconds: float) -> tuple[str, ...]:
        idle = max(0.0, float(idle_seconds))
        due: list[str] = []
        if (
            self.warning_seconds > 0
            and idle >= self.warning_seconds
            and not self._warning_emitted
        ):
            self._warning_emitted = True
            due.append(WARNING_STAGE)
        if (
            self.stalled_seconds > 0
            and idle >= self.stalled_seconds
            and not self._stalled_emitted
        ):
            self._stalled_emitted = True
            due.append(STALLED_STAGE)
        if (
            self.terminate_seconds > 0
            and idle >= self.terminate_seconds
            and not self._terminate_emitted
        ):
            self._terminate_emitted = True
            due.append(TERMINATE_STAGE)
        return tuple(due)
