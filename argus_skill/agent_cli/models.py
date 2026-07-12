from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentRunResult:
    command: list[str]
    exit_code: int
    thread_id: str | None = None
    agent_messages: list[str] = field(default_factory=list)
    json_events: list[dict[str, Any]] = field(default_factory=list)
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    turn_completed: bool = False
    turn_failed: bool = False
    fatal_error: str | None = None
    tool_activity_observed: bool = False

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]


@dataclass
class InactivitySnapshot:
    idle_seconds: float
    command: list[str]
    thread_id: str | None
    last_agent_message: str
    stdout_tail: list[str]
    stderr_tail: list[str]
    run_label: str | None = None
