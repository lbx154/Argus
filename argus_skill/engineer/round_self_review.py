"""Round-loop phase: progress tracking, skill maintenance, self-review.

Owns the bookkeeping that happens once a round is confirmed to be a normal
reviewed round (not a stop-kind shortcircuit, not an agent-driven wait): the
no-progress streak update, same-session Engineer skill maintenance (create /
update triggered by the Engineer's own completion decision), and — if the
Engineer's completion decision both requests a review skip AND the mission
allows self-review — building and recording the Engineer self-approved
verdict. Self-review is an Engineer-owned judgment the harness parses but
never second-guesses; when it fires this phase ends the mission with
``done``. Otherwise it lets the round proceed to the independent Reviewer.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..core.models import RoundRecord
from .round_signals import _review_event_payload
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_proceed,
    control_return,
)
from .round_stop_signals import _runner_result_has_successful_work_signal
from .self_review import EngineerSkillMaintenanceOutcome, engineer_self_approved_review

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


class RoundSelfReviewMixin:
    """Mixin providing ``SupervisedEngineer``'s self-review-acceptance phase."""

    def _handle_progress_and_self_review(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        engineer_skill_maintenance,
        review_completed_hook,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        engineer_message = outcome.engineer_message
        round_thread_id = outcome.round_thread_id
        completion_decision = outcome.completion_decision
        state.backend_failure_streak = 0
        if not _runner_result_has_successful_work_signal(
            engineer_result, engineer_message=engineer_message
        ):
            state.no_progress_streak += 1
        else:
            state.no_progress_streak = 0

        if (
            completion_decision is not None
            and completion_decision.skill_action == "none"
            and supervised_config.required_skill_action in {"create", "update"}
        ):
            completion_decision = replace(
                completion_decision,
                skill_action=supervised_config.required_skill_action,
                skill_name=supervised_config.required_skill_name,
            )
        maintenance = EngineerSkillMaintenanceOutcome()
        if (
            completion_decision is not None
            and completion_decision.skill_action != "none"
            and supervised_config.allow_engineer_skill_maintenance
            and engineer_skill_maintenance is not None
        ):
            try:
                maintenance = engineer_skill_maintenance(
                    completion_decision,
                    round_thread_id,
                    engineer_message,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Engineer same-session skill maintenance failed")
                maintenance = EngineerSkillMaintenanceOutcome(
                    attempted=True,
                    success=False,
                    summary=f"failed: {type(exc).__name__}: {exc}",
                    thread_id=round_thread_id,
                )
        if (
            completion_decision is not None
            and completion_decision.requests_review_skip
            and supervised_config.allow_engineer_self_review
        ):
            # Engineer owns this judgment. The harness parses the explicit
            # decision but does not second-guess it with extra gates.
            review = engineer_self_approved_review(
                completion_decision,
                maintenance_summary=maintenance.summary,
            )
            state.pending_secret_guard_notes.clear()
            if on_event:
                on_event({
                    "type": EventType.ENGINEER_SELF_REVIEW_ACCEPTED,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "skill_action": completion_decision.skill_action,
                    "skill_maintenance_attempted": maintenance.attempted,
                    "skill_maintenance_success": maintenance.success,
                    "text": (
                        "Engineer self-verification accepted; "
                        "independent Reviewer waived"
                    ),
                })
                on_event(_review_event_payload(
                    review,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    text=(
                        "review: skipped (Engineer self-verification) — "
                        + review.reason
                    ),
                    review_skipped=True,
                    review_source="engineer_self_review",
                ))
            record = RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
            )
            state.rounds.append(record)
            if review_completed_hook is not None:
                try:
                    review_completed_hook(record)
                except Exception:  # noqa: BLE001
                    log.warning(
                        "self-review completion hook failed",
                        exc_info=True,
                    )
            return control_return((
                "done",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                maintenance.thread_id or round_thread_id,
            ))
        return control_proceed()
