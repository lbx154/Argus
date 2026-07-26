"""Planning-cycle phase: waiting-verdict handling and project_done normalization.

Covers the planner's ``waiting`` branch (Manager-feedback-unresolved check,
open-ended wait reconciliation, plan-revision rollback, recorded waiting entry,
and the stall-breaker verification probe), followed by the full-paper-gate
override, the research-target completion gate, revision/project_done
rejection, open-ended vs. plain ``project_done`` handling, and the
degenerate no-new-tasks rejection. All of this runs after the planner has
returned a non-error verdict but before any backlog dedupe/enqueue.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ..memory import BacklogItem
from ._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
    PLANNER_SCOPE_FINAL_SUBMISSION,
)
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _research_project_done_issue,
    _staged_goal_completion_issue,
    staged_goal_gate_scope,
)


class PlanningCycleCompletionMixin:
    """Waiting handling + project_done normalization + no-tasks rejection."""

    def _pc_complete_terminal_empty_plan(self, state: _PlanCycleState) -> Any:
        verdict = state.verdict
        terminal_signature = self._open_ended_terminal_idle_signature()
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.COMPLETED,
            completion_kind="terminal_stage_hold",
            resume_outcome=PLAN_TERMINAL_IDLE,
            terminal_signature=terminal_signature,
            cycle=self._planning_cycles,
            project_done=False,
            reason=verdict.reason,
            task_count=0,
            enqueued_tasks=0,
            skipped_duplicate_tasks=0,
            enqueued_titles=[],
            skipped_duplicate_titles=[],
            open_ended_objective=True,
        )
        if not delivered:
            return PLAN_RETRY
        self._enter_idle_backoff()
        self._emit_status("planner: terminal stage certified and Manager held; idling")
        return PLAN_TERMINAL_IDLE

    def _pc_handle_waiting(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version

        if verdict.waiting:
            if self._load_manager_planner_feedback() is not None:
                self._emit(
                    {
                        "type": "life.manager.feedback.unresolved",
                        "reason": "planner returned waiting instead of revision tasks",
                    }
                )
                self._emit_status("planner ignored unresolved Manager feedback; retry later")
                self._enter_idle_backoff()
                return PLAN_ERROR
            if revision_request is not None:
                reconciliation_result = self._reconcile_open_ended_planner_waiting(verdict)
                if reconciliation_result == "rollback":
                    superseding_plan_id = f"manager-rollback-{BacklogItem.new_id()}"
                    try:
                        result = self.memory.backlog.supersede_active_plan(
                            expected_plan_id=expected_plan_id,
                            expected_version=expected_plan_version,
                            supersede_item_ids=[item.id for item in state.revision_active_items],
                            superseded_by_plan_id=superseding_plan_id,
                            reason=verdict.waiting_reason or verdict.reason,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._emit(
                            {
                                "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                                "reason": (
                                    "Manager rolled back stage but active plan could "
                                    f"not be retired: {type(exc).__name__}: {exc}"
                                ),
                                "expected_plan_id": expected_plan_id,
                                "expected_plan_version": expected_plan_version,
                            }
                        )
                        return PLAN_ERROR
                    for item_id in result.superseded_ids:
                        self._emit(
                            {
                                "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                                "item_id": item_id,
                                "plan_id": expected_plan_id,
                                "plan_version": expected_plan_version,
                                "superseded_by_plan_id": superseding_plan_id,
                                "reason": verdict.waiting_reason or verdict.reason,
                            }
                        )
                    self._emit(
                        {
                            "type": "life.plan.revision.rolled_back",
                            "old_plan_id": expected_plan_id,
                            "old_plan_version": expected_plan_version,
                            "superseded_item_ids": list(result.superseded_ids),
                            "reason": verdict.waiting_reason or verdict.reason,
                        }
                    )
                    return PLAN_RETRY
                if reconciliation_result:
                    return PLAN_RETRY
                self._emit(
                    {
                        "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                        "reason": "replacement planner returned waiting",
                        "expected_plan_id": expected_plan_id,
                        "expected_plan_version": expected_plan_version,
                    }
                )
            if revision_request is None and self._reconcile_open_ended_planner_waiting(verdict):
                return PLAN_RETRY
            record = self._record_planner_waiting(verdict)
            # Stall-breaker: if the planner has idled K+ cycles on the same
            # blocker, force a verification probe so reality (not a memory of
            # the blocker) drives the next decision. Running it next tick resets
            # the idle backoff via _reset_idle_backoff().
            if self._maybe_dispatch_verification_probe(verdict):
                return True
            return record

        # The Planner has explicitly moved on from waiting. Preserve the
        # historical probed-token set for deduplication, but stop injecting the
        # old blocker into subsequent planning context.
        self._deactivate_planner_waiting_contract()
        self._last_planner_wait_reconciliation_key = None
        self._planner_waits_since_reconciliation = 0
        return None

    def _pc_normalize_project_done(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version
        planner_declared_done = bool(verdict.project_done)

        # The planner's tasks are trusted. Deterministic gate-repair is only
        # used as a fallback when the planner itself fails (verdict.error above).

        if (
            verdict.project_done
            and self._effective_full_paper_gate(self._artifact_root())
            and not self._journal_has_full_paper_gate_success()
        ):
            from ...planner import TaskSpec

            verdict = replace(
                verdict,
                project_done=False,
                reason=(
                    "full-pipeline final-submission readiness is required before "
                    "project_done; queueing final submission proof"
                ),
                new_tasks=[
                    TaskSpec(
                        title="Prove final submission readiness",
                        objective=(
                            "Project-final task. Scope: final_submission. Read and "
                            "improve the paper as a whole, then let the independent "
                            "Reviewer judge whether the research objective and selected "
                            "venue bar are genuinely met. Repair substantive experiment, "
                            "analysis, claim, citation, writing, or layout weaknesses. "
                            "Do not create an assurance memo or evidence package merely "
                            "to certify the workflow."
                        ),
                        impact_score=5,
                        impact_area="requirement_gap",
                        evidence=(
                            "The project has not yet received an independent final paper review."
                        ),
                        scope=PLANNER_SCOPE_FINAL_SUBMISSION,
                        stage_closing=True,
                    )
                ],
            )

        if verdict.project_done:
            research_done_issue = _research_project_done_issue(
                self._artifact_root(),
                self.memory.journal.all(),
            )
            if research_done_issue:
                verdict = replace(
                    verdict,
                    project_done=False,
                    reason=(
                        "Research project completion gate held: "
                        f"{research_done_issue}. A completed report or bounded cycle "
                        "does not satisfy the persisted research target."
                    ),
                    new_tasks=[],
                )

        staged_goal_candidate = bool(
            verdict.project_done
            or (not planner_declared_done and not verdict.waiting and not verdict.new_tasks)
        )
        if staged_goal_candidate and revision_request is None:
            goal_gate_issue = _staged_goal_completion_issue(self._artifact_root())
            if goal_gate_issue:
                from ...planner import TaskSpec

                verdict = replace(
                    verdict,
                    project_done=False,
                    reason=(
                        f"Staged project completion held: {goal_gate_issue}. "
                        "Planner project_done cannot replace the Goal Gate."
                    ),
                    new_tasks=[
                        TaskSpec(
                            title="Complete and certify the current Goal Gate",
                            objective=(
                                "Goal Gate mission for the active staged project. "
                                f"Current unmet gate: {goal_gate_issue}. "
                                "Re-read the original operator objective, current-stage "
                                "checklist, and actual artifacts. Complete any missing "
                                "current-stage work, then obtain an independent Reviewer "
                                "judgment. `done` requires positive evidence that the "
                                "requested outcome for this stage exists; a clean process, "
                                "honest partial result, or absence of detected errors is not "
                                "completion. At the final stage, verify that the original "
                                "Goal Gate itself is achieved. The Manager alone records "
                                "stage advance or completion."
                            ),
                            impact_score=5,
                            impact_area="requirement_gap",
                            evidence=goal_gate_issue,
                            acceptance_check=(
                                "The independent Reviewer satisfies every current-stage "
                                "checklist item with concrete evidence and the Manager "
                                "records advance or final completion."
                            ),
                            non_goals=[
                                "Treat an error-free process or honest partial progress as "
                                "project completion."
                            ],
                            scope=staged_goal_gate_scope(self._artifact_root()),
                            stage_closing=True,
                        )
                    ],
                )

        if revision_request is not None and verdict.project_done:
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "replacement planner cannot declare project_done",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                }
            )
            return PLAN_ERROR

        if verdict.project_done and self.config.open_ended:
            self._enter_idle_backoff()
            terminal_signature = self._open_ended_terminal_idle_signature()
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=PLAN_RETRY,
                terminal_signature=terminal_signature,
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=len(verdict.new_tasks),
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                open_ended_objective=True,
            )
            if not delivered:
                return PLAN_RETRY
            self._emit_status("planner: project done — continuing later for open-ended objective")
            return PLAN_RETRY

        if verdict.project_done:
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=False,
                terminal_signature=self._open_ended_terminal_idle_signature(),
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=len(verdict.new_tasks),
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
            )
            if not delivered:
                return PLAN_RETRY
            self._emit_status(f"planner: project done — {verdict.reason}")
            return False

        state.verdict = verdict
        return None

    def _pc_reject_if_no_tasks(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        if verdict.new_tasks:
            return None
        if revision_request is not None:
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "planner produced no replacement tasks",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                }
            )
        else:
            reconciliation = self._reconcile_open_ended_terminal_stage_action(verdict)
            if reconciliation == "rollback":
                return PLAN_RETRY
            if reconciliation == "hold":
                return self._pc_complete_terminal_empty_plan(state)
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            }
        )
        self._emit_status("planner error: produced no tasks; retry later")
        # No tasks, no waiting flag, not done: a degenerate no-work cycle.
        # Back off so repeated empty plans cannot spin the daemon.
        self._enter_idle_backoff()
        return PLAN_ERROR


__all__ = ["PlanningCycleCompletionMixin"]
