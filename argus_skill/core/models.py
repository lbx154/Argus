"""Core dataclasses shared across the loop.

Provenance: most types here are vendored or adapted from
``ArgusBot/codex_autoloop/models.py``. Trimmed to what argus-skill actually
uses (no planner snapshots — argus-skill is reviewer-only for v0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReviewStatus = Literal["done", "continue", "blocked"]
LoopStatus = Literal["done", "max_rounds", "blocked", "no_progress", "error"]


@dataclass
class RunnerOptions:
    """Per-call knobs for an LLM runner backend.

    Vendored shape from ArgusBot's RunnerOptions but stripped of fields
    we don't propagate yet (watchdog callbacks, plugin dirs, etc.). New
    backends only have to honour what's listed here; unknown fields
    travel through ``extra_args`` if needed.
    """
    model: str | None = None
    reasoning_effort: str | None = None
    output_schema_path: str | None = None
    working_dir: str | None = None
    extra_args: list[str] | None = None
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False


@dataclass
class RunnerResult:
    """Result returned by a RunnerBackend.run_exec call.

    A slim version of ArgusBot's CodexRunResult — we keep only the parts
    the loop / reviewer / parsers actually look at.
    """
    exit_code: int
    agent_messages: list[str] = field(default_factory=list)
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    thread_id: str | None = None
    fatal_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def last_agent_message(self) -> str:
        if not self.agent_messages:
            return ""
        return self.agent_messages[-1]

    @property
    def message(self) -> str:
        """Concatenated agent message text (skill_store.find_relevant uses
        this name to read the matcher response)."""
        return "\n".join(self.agent_messages)


@dataclass
class CheckResult:
    """An acceptance-check command's result. Vendored from ArgusBot."""
    command: str
    exit_code: int
    passed: bool
    output_tail: str


@dataclass
class ReviewDecision:
    """Reviewer verdict on one engineer round. Vendored from ArgusBot."""
    status: ReviewStatus
    confidence: float
    reason: str
    next_action: str
    round_summary_markdown: str = ""
    completion_summary_markdown: str = ""


@dataclass
class RoundRecord:
    """A snapshot of one engineer round + reviewer verdict."""
    round_index: int
    engineer_message: str
    engineer_exit_code: int
    checks: list[CheckResult]
    review: ReviewDecision
    fatal_error: str | None = None


@dataclass
class LoopOutcome:
    """What SkillLoop.run returns.

    Captures both the final verdict and the round-by-round trail so the
    caller can render a report, persist a decision log, or write the
    successful trajectory back into the skill store.
    """
    status: LoopStatus
    rounds: list[RoundRecord]
    skill_used: str | None
    skill_distilled: bool
    final_message: str
    reason: str
    workdir: str
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return self.status == "done"

    @property
    def round_count(self) -> int:
        return len(self.rounds)
