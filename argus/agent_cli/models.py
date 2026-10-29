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
    model_progress_observed: bool = False
    # Model output of any kind (items including reasoning, text, tool use),
    # beyond request lifecycle events. Decides whether a failed call ran.
    model_output_observed: bool = False
    # Stderr written after the latest model progress event (empty when none
    # was observed): the CLI's account of how the current turn ended.
    terminal_stderr_lines: list[str] = field(default_factory=list)
    tool_activity_observed: bool = False
    usage_model: str = ""
    orphan_process_group_id: int = 0
    orphan_process_group_cleanup_succeeded: bool = False
    # Set when the CLI reported a session id other than the one this call was
    # bound to (pre-allocated or resumed). ``thread_id`` is then ``None`` on
    # purpose: usage must stay unresolved rather than charge another session.
    session_identity_conflict: str | None = None

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
    running_tool: str = ""
