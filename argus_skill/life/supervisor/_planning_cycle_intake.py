"""Planning-cycle phase: request intake, gating, and preflight short-circuits.

Covers everything that can decide "there is nothing to plan right now" before
the planner is ever invoked: draining operator messages, Manager→Planner
feedback exhaustion, dynamic-plan revision-request validation, retrying a
pending planner verdict, terminal-idle / event-wait outcomes, the wiki-collect
maintenance task, the missing-planner-runner error, the operator
external-blocker short circuit, and the bounded-vertical-reached-terminal-stage
fast path.
"""

from __future__ import annotations

from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ._constants import (
    MANAGER_FEEDBACK_REPLAN_LIMIT,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _research_project_done_issue,
    _revision_reason,
)


class PlanningCycleIntakeMixin:
    """Gate checks + preflight short-circuits run before planner invocation."""

    def _pc_intake_gate(self, state: _PlanCycleState) -> Any | None:
        """Drain operator input and reject/idle before touching the planner.

        Returns a non-``None`` result when ``_plan_next_work`` should return
        immediately; returns ``None`` to continue the cycle.
        """
        revision_request = state.revision_request
        state.operator_messages = (
            self._drain_user_inbox() if revision_request is None else []
        )
        if state.operator_messages:
            self._deactivate_planner_waiting_contract()
            self._clear_manager_planner_feedback()
            self._reset_idle_backoff()
        if revision_request is None:
            feedback = self._load_manager_planner_feedback()
            if feedback is not None:
                recorded_signature = str(
                    feedback.get("evidence_signature") or ""
                )
                current_signature = self._manager_feedback_evidence_signature()
                if (
                    recorded_signature
                    and current_signature
                    and recorded_signature != current_signature
                ):
                    self._clear_manager_planner_feedback()
                    self._reset_idle_backoff()
                    feedback = None
            feedback_attempts = int(
                (feedback or {}).get("attempts") or (1 if feedback else 0)
            )
            if feedback is not None and feedback_attempts >= MANAGER_FEEDBACK_REPLAN_LIMIT:
                sleep_s = self._enter_idle_backoff()
                self._emit({
                    "type": "life.manager.feedback.exhausted",
                    "stage": feedback.get("stage") or "",
                    "diagnostic": feedback.get("diagnostic") or "",
                    "reason": feedback.get("reason") or "",
                    "attempts": feedback_attempts,
                    "suggested_sleep_s": sleep_s,
                })
                self._emit_status(
                    "Manager→Planner feedback repeated without a commit; "
                    "entering terminal idle for operator/new-evidence wake-up"
                )
                return PLAN_TERMINAL_IDLE

        if revision_request is not None:
            state.expected_plan_id = str(
                revision_request.get("expected_plan_id") or ""
            )
            state.expected_plan_version = int(
                revision_request.get("expected_plan_version") or 0
            )
            if not state.expected_plan_id:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "unversioned backlog items cannot be revised",
                    "expected_plan_id": "",
                    "expected_plan_version": state.expected_plan_version,
                })
                return PLAN_ERROR
            try:
                state.revision_active_items = [
                    item
                    for item in self.memory.backlog.all()
                    if item.plan_id == state.expected_plan_id
                    and item.plan_version == state.expected_plan_version
                    and item.status not in {"done", "failed", "skipped", "superseded"}
                ]
            except Exception as exc:  # noqa: BLE001
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"cannot inspect active plan: {type(exc).__name__}: {exc}",
                })
                return PLAN_ERROR
            requested_item_id = str(revision_request.get("item_id") or "")
            if not state.revision_active_items or requested_item_id not in {
                item.id for item in state.revision_active_items
            }:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "plan revision conflict: active revision changed",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
                return PLAN_ERROR
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_PROPOSED,
                "expected_plan_id": state.expected_plan_id,
                "expected_plan_version": state.expected_plan_version,
                "active_item_ids": [item.id for item in state.revision_active_items],
                "reason": _revision_reason(revision_request),
            })

        if revision_request is None:
            retried, retry_outcome = self._retry_pending_planner_verdict()
            if retried:
                return retry_outcome
        terminal_idle = (
            None
            if revision_request is not None
            else self._maybe_idle_after_unchanged_open_ended_done()
        )
        if terminal_idle is not None:
            return terminal_idle

        if revision_request is None:
            event_wait_outcome = self._planner_event_wait_outcome()
            if event_wait_outcome:
                return event_wait_outcome

        self._planning_cycles += 1
        state.manager_intent = self._manager_intent_context()
        self._emit({
            "type": EventType.LIFE_PLANNER_START,
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
            "manager_intent": state.manager_intent,
        })
        return None

    def _pc_preflight_shortcircuits(self, state: _PlanCycleState) -> Any | None:
        """Wiki-maintenance / no-runner / external-blocker / bounded-terminal."""
        revision_request = state.revision_request

        wiki_collect_task = (
            None
            if revision_request is not None
            else self._wiki_collect_task_if_due_under_blocker()
        )
        if wiki_collect_task is not None:
            return self._enqueue_wiki_collect_task(wiki_collect_task)

        if self.planner_runner is None:
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "no planner runner wired",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
            self._emit_status("planner error: no planner runner wired; retry later")
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "no planner runner wired",
            })
            self._enter_idle_backoff()
            return PLAN_ERROR

        # Only skip the planner on an operator-only external blocker when the
        # full EMNLP gate is active. A ``--bounded`` mission
        # (``full_paper_gate=False``) does not require the external benchmark
        # targets, so it must fall through to the planner and reach its own
        # ``project_done`` instead of waiting forever on artifacts it never
        # needs. Mirrors the gating in
        # ``_defer_project_done_for_operator_external_blocker``.
        short_circuit = None
        if (
            revision_request is None
            and self._effective_full_paper_gate(self._artifact_root())
        ):
            short_circuit = self._operator_external_blocker_short_circuit_decision(
                project_root=self._project_workdir(),
            )
        if short_circuit is not None:
            return self._record_planner_waiting(short_circuit)

        # The mission is now committing to real planning work — every idle /
        # blocked / no-runner / done short-circuit above has returned. Decide +
        # persist the vertical here (once, guarded), so the planner and its
        # downstream gate reads see a stable vertical. Placing it AFTER the
        # short-circuits means a blocked/idle cycle never triggers a Manager
        # decision (nor a wasted planner-runner call).
        self._resolve_vertical_once()

        artifact_root = self._artifact_root()
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_has_current_completion_certificate,
        )

        vertical = resolve_vertical(artifact_root)
        if (
            revision_request is None
            and
            not getattr(self.config, "open_ended", False)
            and not self._effective_full_paper_gate(artifact_root)
            and vertical_has_current_completion_certificate(artifact_root, vertical)
            and not _research_project_done_issue(
                artifact_root,
                self.memory.journal.all(),
            )
        ):
            reason = f"bounded {vertical} vertical reached terminal stage"
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=False,
                terminal_signature=self._open_ended_terminal_idle_signature(),
                cycle=self._planning_cycles,
                project_done=True,
                reason=reason,
                task_count=0,
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
            )
            if not delivered:
                return PLAN_RETRY
            self._emit_status(f"planner: project done — {reason}")
            return False
        return None


__all__ = ["PlanningCycleIntakeMixin"]
