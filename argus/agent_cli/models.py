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
    stdout_line_count: int = 0
    stderr_line_count: int = 0
    json_event_count: int = 0
    turn_completed: bool = False
    turn_failed: bool = False
    fatal_error: str | None = None
    stop_kind: str | None = None
    # Provider request/response round trips observed inside this ONE call, and
    # whether the per-call allowance (ARGUS_SKILL_PROVIDER_TURN_CAP) ended it.
    # An allowance stop is routine housekeeping, not a failure: the round loop
    # keeps the work and continues in a fresh session from the checkpoint.
    provider_turns: int = 0
    provider_turn_cap_hit: bool = False
    tool_activity_observed: bool = False
    usage_model: str = ""
    orphan_process_group_id: int = 0
    orphan_process_group_cleanup_succeeded: bool = False

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
