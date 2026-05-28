from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ReviewStatus = Literal["done", "continue", "blocked"]
PlanWorkstreamStatus = Literal["done", "in_progress", "todo", "blocked"]
PlanMode = Literal["off", "auto", "record"]


@dataclass
class CodexRunResult:
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

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]


@dataclass
class CheckResult:
    command: str
    exit_code: int
    passed: bool
    output_tail: str


@dataclass
class ReviewDecision:
    status: ReviewStatus
    confidence: float
    reason: str
    next_action: str
    round_summary_markdown: str = ""
    completion_summary_markdown: str = ""


@dataclass
class PlanDecision:
    follow_up_required: bool
    next_explore: str
    main_instruction: str
    review_instruction: str
    overview_markdown: str


@dataclass
class PlanWorkstream:
    area: str
    status: PlanWorkstreamStatus
    evidence: str
    next_step: str


@dataclass
class PlanSnapshot:
    plan_id: str
    generated_at: str
    trigger: str
    terminal: bool
    summary: str
    workstreams: list[PlanWorkstream]
    done_items: list[str]
    remaining_items: list[str]
    risks: list[str]
    next_steps: list[str]
    exploration_items: list[str]
    suggested_next_objective: str
    should_propose_follow_up: bool
    report_markdown: str = ""


@dataclass
class RoundSummary:
    round_index: int
    thread_id: str | None
    main_exit_code: int
    main_turn_completed: bool
    main_turn_failed: bool
    checks: list[CheckResult]
    review: ReviewDecision
    main_last_message: str
    plan: PlanDecision | None = None
