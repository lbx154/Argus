"""Compatibility wrapper for the legacy mission loop API."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import CheckResult, ReviewDecision, RunnerOptions, RunnerResult
from ..engineer.checks import run_checks
from ..engineer.reviewer import ReviewerConfig
from ..engineer.runner import (
    backend_failure_review_decision,
    daemon_stop_review_decision,
    fatal_error_looks_like_backend_failure,
    fatal_error_looks_like_daemon_stop_request,
    should_clear_thread_id_after_outcome,
)


@dataclass
class MissionLoopConfig:
    objective: str
    max_rounds: int = 1
    check_commands: list[str] = field(default_factory=list)
    main_model: str = ""
    reviewer_model: str = ""
    reviewer_reasoning_effort: str = "high"
    plan_mode: str = "off"
    allow_follow_up_phase: bool = False
    pending_lessons_dir: str = ""
    mission_id: str = ""
    on_skill_lesson: Any = None
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0


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
        backend_failure_streak = 0
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
            round_thread_id = engineer_result.thread_id or last_thread_id
            final_message = engineer_result.last_agent_message or final_message

            if self.event_sink is not None:
                self.event_sink({
                    "type": "round.main.completed",
                    "round_index": round_index,
                    "session_id": round_thread_id,
                    "exit_code": engineer_result.exit_code,
                    "fatal_error": engineer_result.fatal_error,
                    "input_tokens": engineer_result.input_tokens,
                    "cached_input_tokens": engineer_result.cached_input_tokens,
                    "output_tokens": engineer_result.output_tokens,
                    "usage_scope": "delta",
                })

            if should_clear_thread_id_after_outcome(
                status="error" if engineer_result.exit_code != 0 else "done",
                fatal_error=engineer_result.fatal_error,
            ):
                last_thread_id = None
            else:
                last_thread_id = round_thread_id

            if fatal_error_looks_like_daemon_stop_request(engineer_result.fatal_error):
                review = daemon_stop_review_decision(
                    fatal_error=engineer_result.fatal_error,
                    exit_code=engineer_result.exit_code,
                )
                if self.event_sink is not None:
                    self.event_sink(review.to_event_payload(
                        round_index=round_index,
                        session_id=self.config.mission_id or None,
                        review_skipped=True,
                    ))
                rounds.append(
                    MissionRoundRecord(
                        round_index=round_index,
                        main_exit_code=engineer_result.exit_code,
                        main_turn_completed=bool(engineer_result.agent_messages),
                        main_turn_failed=True,
                        thread_id=last_thread_id,
                        review=review,
                    )
                )
                return MissionLoopResult(
                    status="error",
                    rounds=rounds,
                    final_message=final_message,
                    reason=review.reason,
                    last_thread_id=None,
                )

            if fatal_error_looks_like_backend_failure(engineer_result.fatal_error):
                backend_failure_streak += 1
                review = backend_failure_review_decision(
                    fatal_error=engineer_result.fatal_error,
                    exit_code=engineer_result.exit_code,
                    streak=backend_failure_streak,
                    threshold=self.config.backend_failure_threshold,
                )
                prev_review_summary = review.round_summary_markdown or review.reason or ""
                if self.event_sink is not None:
                    self.event_sink(review.to_event_payload(
                        round_index=round_index,
                        session_id=self.config.mission_id or None,
                        review_skipped=True,
                    ))
                rounds.append(
                    MissionRoundRecord(
                        round_index=round_index,
                        main_exit_code=engineer_result.exit_code,
                        main_turn_completed=bool(engineer_result.agent_messages),
                        main_turn_failed=True,
                        thread_id=last_thread_id,
                        review=review,
                    )
                )
                threshold = max(1, int(self.config.backend_failure_threshold or 1))
                if backend_failure_streak >= threshold or round_index >= self.config.max_rounds:
                    return MissionLoopResult(
                        status="error",
                        rounds=rounds,
                        final_message=final_message,
                        reason=review.reason,
                        last_thread_id=None,
                    )
                backoff_seconds = max(0.0, float(self.config.backend_failure_backoff_seconds or 0.0))
                if backoff_seconds:
                    time.sleep(backoff_seconds)
                continue

            backend_failure_streak = 0

            checks_results: list[CheckResult] = []
            if self.config.check_commands:
                checks_results = run_checks(
                    list(self.config.check_commands),
                    timeout_seconds=600,
                )

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
                self.event_sink(review.to_event_payload(
                    round_index=round_index,
                    session_id=self.config.mission_id or None,
                ))

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
