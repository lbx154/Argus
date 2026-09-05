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
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
)
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _research_project_done_issue,
    _staged_goal_completion_issue,
    goal_gate_task_title,
)


class PlanningCycleCompletionMixin:
    """Waiting handling + project_done normalization + no-tasks rejection."""

    def _manager_project_completion_context(self) -> dict[str, Any]:
        """Collect every stage and transition for Manager's completion report."""
        from ...core.pipeline_state import read_pipeline_state
        from ...core.stage_certificate import all_stage_reviews

        pipeline = read_pipeline_state(self._artifact_root())
        stages = pipeline.get("stages")
        stage_history = pipeline.get("stage_history")
        rollback_history = pipeline.get("rollback_history")
        return {
            "current_stage": str(pipeline.get("current_stage") or ""),
            "stages": dict(stages) if isinstance(stages, dict) else {},
            "stage_history": (
                [dict(row) for row in stage_history if isinstance(row, dict)]
                if isinstance(stage_history, list)
                else []
            ),
            "rollback_history": (
                [dict(row) for row in rollback_history if isinstance(row, dict)]
                if isinstance(rollback_history, list)
                else []
            ),
            "stage_reviews": all_stage_reviews(self.memory.root),
        }

    def _manager_publish_project_report(self, completion_reason: str) -> str:
        """Have Manager summarize the completed full stage ledger to the operator."""
        import hashlib
        import json

        stage = str(self._current_pipeline_stage() or "")
        context = self._manager_project_completion_context()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "objective": self.config.continuous_objective,
                    "reason": completion_reason,
                    "context": context,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        message_id = f"manager-project-report-{fingerprint}"
        from ...core.transcript import read_turns

        if any(
            turn.get("message_id") == message_id
            for turn in read_turns(self.memory.root)
        ):
            return "reported"

        report = self._bound_manager().report_project_completion(
            completion_context=context,
            continuous_objective=self.config.continuous_objective,
            completion_reason=completion_reason,
            on_event=getattr(self.sink, "handle_event", None),
        )
        if not str(report or "").strip():
            self._emit_status("Manager could not produce the project completion report")
            return PLAN_ERROR
        try:
            from ...core.operator_messages import publish_operator_message

            published = publish_operator_message(
                self.memory.root,
                text=str(report).strip(),
                message_id=message_id,
                event_fields={
                    "manager_project_report": True,
                    "current_stage": stage,
                },
            )
            if not published and not any(
                turn.get("message_id") == message_id
                for turn in read_turns(self.memory.root)
            ):
                self._emit_status("Manager project report could not be published")
                return PLAN_ERROR
        except Exception as exc:  # noqa: BLE001 - notification cannot own the verdict
            self._emit({
                "type": "life.manager.project_report.failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            return PLAN_ERROR
        self._emit({
            "type": "life.manager.project_report",
            "stage": stage,
            "report": str(report).strip(),
            "stage_count": len(context["stages"]),
            "transition_count": len(context["stage_history"]),
            "rollback_count": len(context["rollback_history"]),
            "message_id": message_id,
        })
        self._clear_manager_planner_feedback()
        return "reported"

    def _manager_final_stage_is_completed(self) -> bool:
        """Whether Manager has already persisted the terminal stage completion."""
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_has_current_completion_certificate,
        )

        root = self._artifact_root()
        return vertical_has_current_completion_certificate(
            root,
            resolve_vertical(root),
        )

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
            feedback = self._load_manager_planner_feedback()
            if feedback is not None:
                diagnostic = str(feedback.get("diagnostic") or "")
                if diagnostic == PLANNER_TASKS_FILTERED_DIAGNOSTIC:
                    # Filtered-task feedback says every proposed task
                    # duplicated live backlog work. A deliberate wait answers
                    # that feedback instead of dodging it: nothing new is
                    # schedulable until the live work moves, so hand control
                    # to the waiting contract rather than another replan.
                    self._clear_manager_planner_feedback()
                else:
                    self._emit(
                        {
                            "type": "life.manager.feedback.unresolved",
                            "reason": "planner returned waiting instead of revision tasks",
                        }
                    )
                    self._emit_status(
                        "planner ignored unresolved Manager feedback; retry later"
                    )
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

        def reject_completion(reason: str, diagnostic: str) -> str:
            stage = str(self._current_pipeline_stage() or "")
            if not self._persist_manager_planner_feedback(
                stage=stage,
                reason=reason,
                diagnostic=diagnostic,
            ):
                self._emit_status(
                    "failed to persist completion rejection; retry later"
                )
                return PLAN_ERROR
            self._reset_idle_backoff()
            self._emit({
                "type": "life.planner.completion_rejected",
                "stage": stage,
                "reason": reason,
                "diagnostic": diagnostic,
            })
            self._emit_status(
                "planner completion rejected; returning the invariant to Planner"
            )
            state.verdict = replace(
                verdict,
                project_done=False,
                reason=reason,
                new_tasks=[],
            )
            return PLAN_RETRY

        if (
            verdict.project_done
            and self._effective_final_certification_gate(self._artifact_root())
            and not self._journal_has_final_certification()
        ):
            return reject_completion(
                "final submission requires an independent certification",
                "final_certification_missing",
            )

        if verdict.project_done:
            research_done_issue = _research_project_done_issue(
                self._artifact_root(),
                self.memory.journal.all(),
                current_signature=self._final_submission_signature(),
                evidence_root=self._project_workdir(),
            )
            if research_done_issue:
                return reject_completion(
                    "Research project completion held: "
                    f"{research_done_issue}. A completed report or bounded cycle "
                    "does not satisfy the persisted research target.",
                    "research_target_incomplete",
                )

        if verdict.project_done:
            from ...core.external_completion_gate import external_completion_gate_issue

            external_gate_issue = external_completion_gate_issue(self._artifact_root())
            if external_gate_issue:
                return reject_completion(
                    f"Project completion held: {external_gate_issue}. The external "
                    "controller alone decides it.",
                    "external_completion_gate_held",
                )

        staged_goal_candidate = bool(
            verdict.project_done
            or (not planner_declared_done and not verdict.waiting and not verdict.new_tasks)
        )
        if staged_goal_candidate and revision_request is None:
            goal_gate_issue = _staged_goal_completion_issue(self._artifact_root())
            if goal_gate_issue:
                return reject_completion(
                    f"{goal_gate_task_title(self._artifact_root())}: "
                    f"{goal_gate_issue}. "
                    "Planner project_done cannot replace stage certification.",
                    "staged_goal_gate_incomplete",
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
            # A standing campaign cannot be completed by one Planner increment.
            # The Planner class repairs this once before returning, but keep the
            # supervisor guard for injected/fake/custom planners. Stay alive and
            # ask again after backoff instead of recording project_done and then
            # idling until the daemon exits.
            sleep_s = self._enter_pause_backoff()
            self._emit({
                "type": "life.planner.continuation_required",
                "cycle": self._planning_cycles,
                "reason": verdict.reason,
                "suggested_sleep_s": sleep_s,
            })
            self._emit_status(
                "planner completed one increment, but the standing objective "
                "remains active; requesting the next delegated task"
            )
            return PLAN_RETRY

        if verdict.project_done:
            if not self._manager_final_stage_is_completed():
                stage = str(self._current_pipeline_stage() or "")
                reason = (
                    f"Project completion cannot be recorded yet: final stage "
                    f"{stage!r} has not been completed by Manager."
                )
                if not self._persist_manager_planner_feedback(
                    stage=stage,
                    reason=reason,
                    diagnostic="manager_final_stage_not_completed",
                ):
                    return PLAN_ERROR
                self._emit_status(reason)
                return PLAN_RETRY
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
