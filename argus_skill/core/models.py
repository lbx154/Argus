"""Core dataclasses shared across the loop.

Provenance: most types here are vendored or adapted from
``ArgusBot/codex_autoloop/models.py``. Trimmed to what argus-skill actually
uses (no planner snapshots — argus-skill is reviewer-only for v0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

ReviewStatus = Literal["done", "continue", "blocked"]
LoopStatus = Literal["done", "max_rounds", "blocked", "no_progress", "error"]


@dataclass
class RunnerOptions:
    """Per-call knobs for an LLM runner backend.

    Vendored shape from ArgusBot's RunnerOptions. Watchdog hooks are
    optional and only honoured by backends that wrap a real subprocess
    (e.g. ``CodexRunnerBackend``); ``MemoryBackend`` and other
    deterministic backends ignore them.
    """
    model: str | None = None
    reasoning_effort: str | None = None
    output_schema_path: str | None = None
    working_dir: str | None = None
    extra_args: list[str] | None = None
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    # Watchdog hooks — propagated to the codex subprocess so an outer
    # supervisor (e.g. ArgusBot's LoopEngine, the MissionDaemon) can
    # interrupt a long-running engineer turn promptly.
    #
    # ``external_interrupt_reason_provider`` is polled by the runner
    # while the subprocess is alive; when it returns a non-empty
    # string the subprocess is terminated and the result carries
    # ``fatal_error="External interrupt: <reason>"``.
    #
    # ``inactivity_callback`` is invoked on soft-idle boundaries (no
    # stdout for ``watchdog_soft_idle_seconds``); it can return
    # ``"restart"`` to force termination + retry semantics, or any
    # other value to keep waiting.
    #
    # ``watchdog_soft_idle_seconds`` / ``watchdog_hard_idle_seconds``
    # are absolute idle thresholds; ``0`` disables that level.
    external_interrupt_reason_provider: Callable[[], str | None] | None = None
    inactivity_callback: Callable[[Any], str | None] | None = None
    watchdog_soft_idle_seconds: int = 0
    watchdog_hard_idle_seconds: int = 0


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
