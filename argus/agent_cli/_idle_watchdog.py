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


_STARTED_STATUSES = {"", "pending", "running", "in_progress", "started"}


def _tool_label(value: object, *, fallback: object = "") -> str:
    """One-line label for a tool call: its shell command when it has one, else its name."""
    if isinstance(value, dict):
        for key in ("command", "cmd", "description"):
            text = value.get(key)
            if isinstance(text, list):
                text = " ".join(str(part) for part in text)
            if isinstance(text, str) and text.strip():
                return " ".join(text.split())[:300]
    text = str(fallback or "").strip()
    return " ".join(text.split())[:300]


def running_tool_after_event(event: dict, current: str) -> str:
    """The tool a streamed CLI call is waiting on after ``event``.

    Each CLI dialect announces a tool start and its end differently; this
    reads the ones that name the command so a silence stop can say which
    command was stuck. Events that neither start nor end a tool keep
    ``current``; an end clears it. Dialects that never name the command
    leave it empty, and the stop falls back to the generic guidance.
    """
    if not isinstance(event, dict):
        return current
    event_type = str(event.get("type") or "").strip().casefold()
    item = event.get("item")
    if isinstance(item, dict) and str(item.get("type") or "").casefold() == "command_execution":
        if event_type == "item.started":
            return _tool_label(item) or current
        if event_type == "item.completed":
            return ""
        return current
    data = event.get("data")
    if event_type in {"tool.execution_start", "tool_execution_start"}:
        data = data if isinstance(data, dict) else event
        arguments = data.get("arguments") or data.get("args") or data.get("input")
        return _tool_label(arguments, fallback=data.get("toolName") or data.get("name")) or current
    if event_type in {"tool.execution_complete", "tool_execution_end"}:
        return ""
    message = event.get("message")
    if event_type in {"assistant", "user"} and isinstance(message, dict):
        content = message.get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        if event_type == "user" and any(block.get("type") == "tool_result" for block in blocks):
            return ""
        for block in reversed(blocks):
            if block.get("type") == "tool_use":
                return _tool_label(block.get("input"), fallback=block.get("name")) or current
        return current
    part = event.get("part")
    if event_type == "tool_use" and isinstance(part, dict):
        raw_state = part.get("state")
        state = raw_state if isinstance(raw_state, dict) else {}
        status = str(state.get("status") or "").casefold()
        if status in _STARTED_STATUSES:
            return _tool_label(state.get("input"), fallback=part.get("tool")) or current
        return ""
    return current
