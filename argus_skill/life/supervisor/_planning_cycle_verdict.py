"""Planning-cycle phase: planner invocation and verdict error/overlap handling.

Covers building the planner prompt context and calling ``planner.plan_next()``
(with exception handling), then ``verdict.error`` /
operator-external-blocker-defer / independent-overlap-task normalization that
happens before any waiting/project_done interpretation.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from ...core.event_catalog import EventType
from ._constants import PLAN_ERROR, PLAN_RETRY
from ._planning_cycle_helpers import _PlanCycleState, _render_revision_request

log = logging.getLogger(__name__)


class PlanningCycleVerdictMixin:
    """Planner invocation and error/overlap normalization."""

    def _pc_invoke_planner(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        journal_tail = self._render_journal_for_planner()

        runtime_note = self._planner_runtime_with_idle_note()
        operator_note = (
            "LIVE OPERATOR GUIDANCE (supersedes stale blocker state):\n"
            + "\n".join(f"- {message}" for message in state.operator_messages)
            if state.operator_messages
            else ""
        )
        revision_note = (
            _render_revision_request(revision_request, state.revision_active_items)
            if revision_request is not None
            else ""
        )

        state.subagent_family_failures = self._recent_subagent_family_failures()
        stuck_families_note = self._stuck_subagent_families_note(
            state.subagent_family_failures
        )

        try:
            from ...planner import Planner

            planner = Planner(self.planner_runner, skill_store=self.skill_store)
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                state.verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,
                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary="\n\n".join(
                        part for part in (
                            self._manager_intent_prompt_block(
                                state.manager_intent,
                                self.config.continuous_objective,
                            ),
                            operator_note,
                            self._planner_authorization_prompt_block(),
                            stuck_families_note,
                            runtime_note,
                            revision_note,
                        ) if part
                    ),
                    config=self._planner_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"planner raised: {type(exc).__name__}: {exc}",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            self._enter_idle_backoff()
            return PLAN_ERROR
        return None

    def _pc_normalize_verdict(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        verdict = state.verdict

        state.schema_repair_details = (
            verdict.schema_repair_event_payload()
            if hasattr(verdict, "schema_repair_event_payload")
            else {}
        )

        if verdict.error:
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": verdict.error,
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
            reconciliation = ""
            if (
                revision_request is None
                and verdict.error
                == "planner said not done but produced no concrete tasks"
            ):
                reconciliation = self._reconcile_open_ended_terminal_stage_action(
                    verdict
                )
            if reconciliation == "rollback":
                return PLAN_RETRY
            if reconciliation == "hold":
                return self._pc_complete_terminal_empty_plan(state)
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": verdict.error,
                "raw_text": verdict.raw_text,
                **state.schema_repair_details,
            })
            self._emit_status(f"planner error: {verdict.error}; retry later")
            # A planner error is a no-work outcome: back off before retrying so
            # a persistently-failing planner cannot spin every poll interval.
            self._enter_idle_backoff()
            return PLAN_ERROR

        verdict = self._defer_project_done_for_operator_external_blocker(verdict)

        overlap_task = self._independent_overlap_task(verdict)
        if overlap_task is not None:
            verdict = replace(
                verdict,
                waiting=False,
                waiting_reason="",
                waiting_contract=None,
                reason=(
                    "healthy background job continues; scheduling independent "
                    "overlap work instead of idling"
                ),
                new_tasks=[overlap_task],
            )
            self._emit({
                "type": "life.planner.wait_overridden",
                "cycle": self._planning_cycles,
                "task_title": overlap_task.title,
                "reason": verdict.reason,
            })
        state.verdict = verdict
        return None


__all__ = ["PlanningCycleVerdictMixin"]
