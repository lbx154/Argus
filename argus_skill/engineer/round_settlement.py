"""Round-loop phase: round decision, semantic-stall, and replan settlement.

Owns everything that happens once a real Reviewer verdict is in hand: scope-
change escalation to Manager/Planner arbitration, the semantic decision-stall
streak, repeated-failure-signature replan detection, the Dynamic-Plan
``reconsider`` confirmation cadence, upstream-stage reconciliation, the
Reviewer-requested ``wait_for_subagent`` control action, and finally
``_classify`` — the terminal-status decision gate (``done`` / ``blocked`` /
``replan_requested`` / ``no_progress`` / escalated ``blocked``) derived from
the verdict plus the accumulated streaks. The Reviewer's verdict itself is
never second-guessed here: this phase only derives bookkeeping and streak-
based escalations FROM that verdict; it never fabricates or overrides one.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

from ..core.claim_synthesis import claim_synthesis_for_review
from ..core.event_catalog import EventType
from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from ..reviewer.failure_taxonomy import REPAIRABLE_FAILURE_LAYERS, resolve_failure_layer
from .background_subagents import inspect_wait_target
from .round_signals import (
    _DECISION_PROGRESS_CLASSES,
    _next_decision_stall_streak,
    _normalize_dynamic_plan_mode,
    _pause_decision_clock,
    _plan_signal_event,
    _promote_scope_change_to_replan,
    _review_event_payload,
    _review_progress_class,
    _review_scope_change_reason,
    _review_wait_rejection,
    _upstream_stage_reconciliation_target,
)
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
    control_return,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


class RoundSettlementMixin:
    """Mixin providing ``SupervisedEngineer``'s round-settlement phase and
    the ``_classify`` terminal-status decision gate."""

    @staticmethod
    def _classify(
        *,
        review: ReviewDecision,
        no_progress_streak: int,
        no_progress_threshold: int,
        semantic_stall_streak: int = 0,
        stall_threshold: int = 0,
        round_index: int,
        max_rounds: int,
        hard_escalate_rounds: int = 0,
        decision_idle_seconds: float = 0.0,
        decision_timeout_seconds: int = 0,
    ) -> tuple[LoopStatus | None, str]:
        if review.status == "done":
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status == "blocked":
            failure_layer = resolve_failure_layer(
                failure_layer=getattr(review, "failure_layer", ""),
                failure_cause=getattr(review, "failure_cause", ""),
            )
            if failure_layer in REPAIRABLE_FAILURE_LAYERS and not review.operator_question:
                return (
                    "replan_requested",
                    f"Repairable {failure_layer} failure; scientific state is "
                    "unchanged. Replan to repair the failed layer, validate it, "
                    "and resume the scientific experiment. " + (review.reason or ""),
                )
            claim_synthesis = claim_synthesis_for_review(review)
            if failure_layer == "scientific" and claim_synthesis is not None:
                advance = bool(claim_synthesis.get("advance_to_analysis_or_report"))
                return (
                    "replan_requested",
                    "Valid scientific result routed to "
                    f"{claim_synthesis['route']} / {claim_synthesis['action']}; "
                    + (
                        "develop the independently defensible publication thesis. "
                        if advance
                        else (
                            "preserve the data, but do not auto-draft a paper; "
                            "diagnose implementation adequacy or pivot the research. "
                        )
                    )
                    + (review.reason or ""),
                )
            if review.failure_cause == "environmental" and not review.operator_question:
                return "infra_blocked", review.reason or "Research infrastructure blocked progress."
            return "blocked", review.reason or "Reviewer blocked progress."
        if review.status == "replan_requested":
            return (
                "replan_requested",
                review.reason or "Reviewer requested a Manager-owned replacement plan.",
            )
        if review.status in {
            "research_incomplete",
            "paused_no_breakthrough",
            "exhausted_current_methods",
        }:
            return (
                cast(LoopStatus, review.status),
                review.reason or "Reviewer ended this research cycle without certifying success.",
            )
        if no_progress_streak >= no_progress_threshold:
            return (
                "no_progress",
                "Engineer produced no effective output for "
                f"{no_progress_streak} consecutive rounds.",
            )
        if (
            stall_threshold > 0
            and semantic_stall_streak >= stall_threshold
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                "Reviewer reported no decision progress for "
                f"{semantic_stall_streak} consecutive rounds.",
            )
        if (
            decision_timeout_seconds > 0
            and decision_idle_seconds >= decision_timeout_seconds
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                f"Reached {decision_timeout_seconds} seconds without decision progress.",
            )
        if (
            hard_escalate_rounds > 0
            and round_index >= hard_escalate_rounds
            and review.status == "continue"
        ):
            return (
                "blocked",
                f"Escalated: ran {round_index} rounds without completing — the "
                "mission is likely stuck on an external / unresolved constraint. "
                "Ending so the planner can re-plan or decompose. " + (review.reason or ""),
            )
        return None, ""

    def _settle_round(
        self,
        *,
        review: ReviewDecision,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        review_completed_hook,
        continue_adaptor,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        engineer_message = outcome.engineer_message
        state.reviewer_backend_failure_streak = 0
        state.pending_secret_guard_notes.clear()
        scope_change_reason = _review_scope_change_reason(review)
        if scope_change_reason:
            _promote_scope_change_to_replan(
                review,
                reason=scope_change_reason,
            )
            if on_event:
                on_event(
                    {
                        "type": "round.review.scope_change_escalated",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "reason": scope_change_reason,
                        "next_action": str(review.next_action or ""),
                        "text": (
                            "Reviewer scope-changing guidance escalated to "
                            "Manager/Planner arbitration; no direct Engineer retry"
                        ),
                    }
                )
        progress_class = _review_progress_class(review)
        next_semantic_stall_streak = _next_decision_stall_streak(
            review,
            state.semantic_stall_streak,
        )
        planner_report = getattr(review, "planner_report", None)
        upstream_stage_target = _upstream_stage_reconciliation_target(
            review,
            workdir=workdir,
        )
        reconsidered = (
            review.status == "continue"
            and isinstance(planner_report, dict)
            and planner_report.get("plan_signal") == "reconsider"
        )
        state.plan_reconsider_streak = state.plan_reconsider_streak + 1 if reconsidered else 0
        dynamic_plan_mode = _normalize_dynamic_plan_mode(supervised_config.dynamic_plan_mode)
        confirm_rounds = max(1, int(supervised_config.dynamic_plan_confirm_rounds or 1))
        plan_signal_event = _plan_signal_event(
            review,
            mode=dynamic_plan_mode,
            streak=max(1, state.plan_reconsider_streak),
            confirm_rounds=confirm_rounds,
        )
        plan_reconsider_confirmed = bool(
            plan_signal_event is not None and plan_signal_event.get("confirmed") is True
        )
        now_monotonic = time.monotonic()
        next_decision_progress_at = (
            now_monotonic
            if progress_class in _DECISION_PROGRESS_CLASSES
            else state.last_decision_progress_at
        )
        if on_event:
            on_event(
                _review_event_payload(
                    review,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    text=f"review: {review.status} — {review.reason}",
                )
            )
            if plan_signal_event is not None:
                plan_signal_event["round_index"] = round_index
                on_event(plan_signal_event)
        record = RoundRecord(
            round_index=round_index,
            engineer_message=engineer_message,
            engineer_exit_code=engineer_result.exit_code,
            review=review,
            fatal_error=engineer_result.fatal_error,
        )
        state.rounds.append(record)
        state.reviewer_next_action = review.next_action if review.status == "continue" else None
        if review_completed_hook is not None:
            try:
                review_completed_hook(record)
            except Exception:  # noqa: BLE001 - memory capture never owns verdict
                log.warning("review completion hook failed", exc_info=True)

        if upstream_stage_target:
            reconciliation_reason = str(
                (getattr(review, "harness_control", {}) or {}).get("reason") or review.reason or ""
            ).strip()
            if on_event:
                on_event(
                    {
                        "type": EventType.LIFE_PLAN_SIGNAL,
                        "mode": "active",
                        "signal": "stage_reconciliation",
                        "confirmed": True,
                        "target_stage": upstream_stage_target,
                        "reason": reconciliation_reason,
                        "round_index": round_index,
                    }
                )
            return control_return(
                (
                    "replan_requested",
                    state.rounds,
                    state.last_engineer_message,
                    reconciliation_reason,
                    None,
                )
            )

        if scope_change_reason:
            return control_return(
                (
                    "replan_requested",
                    state.rounds,
                    state.last_engineer_message,
                    scope_change_reason,
                    None,
                )
            )

        if plan_reconsider_confirmed:
            return control_return(
                (
                    "replan_requested",
                    state.rounds,
                    state.last_engineer_message,
                    str(review.reason or "").strip(),
                    None,
                )
            )

        if getattr(review, "control_action", "") == "wait_for_subagent":
            rejection_code = ""
            rejection_reason = ""
            wait_task_id = str(getattr(review, "control_task_id", "") or "").strip()
            if not supervised_config.background_subagent_advisory:
                rejection_code = "advisory_disabled"
                rejection_reason = "background-subagent advisory is disabled in this mission"
            elif review.status != "continue":
                rejection_code = "review_status_not_continue"
                rejection_reason = f"review status `{review.status}` cannot request a wait"
            elif inspect_wait_target(workdir, wait_task_id)[0] == "waitable":
                # Call through the ``runner`` module attribute (not a static
                # imported name) so tests that monkeypatch
                # ``runner._run_background_wait`` keep observing it, exactly
                # as when this call lived directly inside ``runner.py``.
                from . import runner as _runner_module

                _, waited_s = _runner_module._run_background_wait(
                    workdir=workdir,
                    task_id=wait_task_id,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    on_event=on_event,
                )
                state.last_decision_progress_at = _pause_decision_clock(
                    next_decision_progress_at,
                    waited_s,
                )
                return control_continue_loop()
            else:
                rejection_code, rejection_reason = _review_wait_rejection(
                    workdir,
                    wait_task_id,
                )
            if on_event:
                on_event(
                    {
                        "type": "round.background_wait.rejected",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "task_id": wait_task_id,
                        "reason_code": rejection_code,
                        "reason": rejection_reason,
                        "text": (f"reviewed background wait rejected: {rejection_reason}"),
                    }
                )

        state.semantic_stall_streak = next_semantic_stall_streak
        state.last_decision_progress_at = next_decision_progress_at
        decision_idle_seconds = max(
            0.0,
            now_monotonic - state.last_decision_progress_at,
        )
        if on_event and state.semantic_stall_streak > 0:
            on_event(
                {
                    "type": EventType.ROUND_STALL,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "progress_class": progress_class,
                    "semantic_stall_streak": state.semantic_stall_streak,
                    "stall_threshold": supervised_config.stall_threshold,
                    "decision_idle_seconds": round(decision_idle_seconds, 1),
                    "text": (
                        f"no decision progress {state.semantic_stall_streak}/"
                        f"{supervised_config.stall_threshold} rounds"
                    ),
                }
            )
        terminal_status, reason = self._classify(
            review=review,
            no_progress_streak=state.no_progress_streak,
            no_progress_threshold=supervised_config.no_progress_threshold,
            semantic_stall_streak=state.semantic_stall_streak,
            stall_threshold=supervised_config.stall_threshold,
            round_index=round_index,
            max_rounds=supervised_config.max_rounds,
            hard_escalate_rounds=supervised_config.hard_escalate_rounds,
            decision_idle_seconds=decision_idle_seconds,
            decision_timeout_seconds=(supervised_config.decision_progress_timeout_seconds),
        )
        if terminal_status is not None:
            return control_return(
                (
                    terminal_status,
                    state.rounds,
                    state.last_engineer_message,
                    reason,
                    None,
                )
            )

        if continue_adaptor is not None:
            try:
                continue_adaptor(state.rounds)
            except Exception:  # noqa: BLE001 — adaptation is advisory
                log.debug("continue adaptor failed", exc_info=True)
        return control_proceed()
