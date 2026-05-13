"""Compatibility wrapper for the legacy mission loop API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import CheckResult, ReviewDecision, RunnerOptions, RunnerResult
from ..engineer.checks import run_checks
from ..engineer.reviewer import ReviewerConfig


@dataclass
class MissionLoopConfig:
    objective: str
    max_rounds: int = 1
    check_commands: list[str] = field(default_factory=list)
    main_model: str = ""
    reviewer_model: str = ""
    reviewer_reasoning_effort: str = "medium"
    plan_mode: str = "off"
    allow_follow_up_phase: bool = False
    pending_lessons_dir: str = ""
    mission_id: str = ""
    on_skill_lesson: Any = None


@dataclass
class MissionRoundRecord:
    round_index: int
    main_exit_code: int
    main_turn_completed: bool
    main_turn_failed: bool
    thread_id: str | None
    review: ReviewDecision


@dataclass
class MissionLoopResult:
    status: str
    rounds: list[MissionRoundRecord]
    final_message: str
    reason: str
    last_thread_id: str | None


class MissionLoopEngine:
    def __init__(
        self,
        *,
        runner,
        reviewer,
        planner,
        config: MissionLoopConfig,
        state_store,
        event_sink=None,
    ) -> None:
        self.runner = runner
        self.reviewer = reviewer
        self.planner = planner
        self.config = config
        self.state_store = state_store
        self.event_sink = event_sink

    def run(self) -> MissionLoopResult:
        rounds: list[MissionRoundRecord] = []
        last_thread_id: str | None = None
        final_message = ""
        reason = ""
        prev_review_summary = ""
        review_config = ReviewerConfig(
            model=self.config.reviewer_model or None,
            reasoning_effort=self.config.reviewer_reasoning_effort,
            extra_args=[],
            full_auto=True,
            skip_git_repo_check=True,
            dangerous_yolo=False,
        )

        for round_index in range(1, self.config.max_rounds + 1):
            engineer_result: RunnerResult = self.runner.run_exec(
                prompt=self.config.objective,
                options=RunnerOptions(
                    model=self.config.main_model or None,
                    full_auto=True,
                    skip_git_repo_check=True,
                    dangerous_yolo=True,
                ),
                run_label=f"engineer-r{round_index}",
                resume_thread_id=last_thread_id,
            )
            last_thread_id = engineer_result.thread_id or last_thread_id
            final_message = engineer_result.last_agent_message or final_message

            checks_results: list[CheckResult] = []
            if self.config.check_commands:
                checks_results = run_checks(
                    list(self.config.check_commands),
                    timeout_seconds=600,
                )

            if self.event_sink is not None:
                self.event_sink({
                    "type": "round.main.completed",
                    "round_index": round_index,
                    "session_id": last_thread_id,
                    "exit_code": engineer_result.exit_code,
                    "fatal_error": engineer_result.fatal_error,
                    "input_tokens": engineer_result.input_tokens,
                    "cached_input_tokens": engineer_result.cached_input_tokens,
                    "output_tokens": engineer_result.output_tokens,
                })

            review = self.reviewer.evaluate(
                objective=self.config.objective,
                round_index=round_index,
                session_id=self.config.mission_id or None,
                main_summary=final_message or "(no message)",
                main_error=engineer_result.fatal_error,
                checks=checks_results,
                config=review_config,
                engineer_reasoning_summary=final_message or "",
                prev_review_summary=prev_review_summary,
            )

            prev_review_summary = (
                review.round_summary_markdown or review.reason or ""
            )

            if self.event_sink is not None:
                self.event_sink({
                    "type": "round.review.completed",
                    "round_index": round_index,
                    "session_id": self.config.mission_id or None,
                    "status": review.status,
                    "confidence": review.confidence,
                    "reason": review.reason,
                    "next_action": review.next_action,
                    "input_tokens": review.input_tokens,
                    "cached_input_tokens": review.cached_input_tokens,
                    "output_tokens": review.output_tokens,
                })

            if review.mission_lesson and callable(self.config.on_skill_lesson):
                try:
                    self.config.on_skill_lesson(
                        self.runner.config.skill_name or "",
                        review.mission_lesson,
                    )
                except Exception:  # noqa: BLE001
                    pass

            rounds.append(
                MissionRoundRecord(
                    round_index=round_index,
                    main_exit_code=engineer_result.exit_code,
                    main_turn_completed=bool(engineer_result.agent_messages),
                    main_turn_failed=bool(engineer_result.exit_code),
                    thread_id=last_thread_id,
                    review=review,
                )
            )

            if review.status == "done":
                reason = review.reason
                return MissionLoopResult(
                    status="done",
                    rounds=rounds,
                    final_message=final_message,
                    reason=reason,
                    last_thread_id=last_thread_id,
                )
            if review.status == "blocked":
                reason = review.reason
                return MissionLoopResult(
                    status="blocked",
                    rounds=rounds,
                    final_message=final_message,
                    reason=reason,
                    last_thread_id=last_thread_id,
                )

            reason = review.reason

        return MissionLoopResult(
            status="max_rounds",
            rounds=rounds,
            final_message=final_message,
            reason=reason or f"Hit max_rounds={self.config.max_rounds}.",
            last_thread_id=last_thread_id,
        )
