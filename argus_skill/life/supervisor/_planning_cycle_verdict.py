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
from ._constants import PLAN_AWAITING, PLAN_ERROR, PLAN_RETRY
from ._planning_cycle_helpers import _PlanCycleState, _render_revision_request

log = logging.getLogger(__name__)


def _is_content_filter_failure(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values).casefold()
    return "content filtering blocked" in text or "blocked by content filtering" in text


class PlanningCycleVerdictMixin:
    """Planner invocation and error/overlap normalization."""

    def _pause_empty_plan_for_operator(
        self,
        state: _PlanCycleState,
    ) -> str:
        """Make an exhausted empty plan visible instead of silently backing off."""
        verdict = state.verdict
        reason = str(verdict.reason or verdict.error or "").strip()
        from ...manager.directive import active_operator_question_policy

        if active_operator_question_policy(self.memory.root) == "forbid":
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": verdict.error or reason,
                "raw_text": verdict.raw_text,
                "operator_alert": False,
                "recoverable": True,
                "stop_kind": "planner_empty_plan",
            })
            self._emit_status(
                "planner has no concrete task; operator questions are forbidden, "
                f"so autonomous retry/backoff remains active: {reason[:240]}"
            )
            return PLAN_ERROR
        question = (
            "Planner cannot identify a concrete next task. "
            + (f"It reported: {reason[:900]} " if reason else "")
            + "Please tell Argus what direction to try next, provide the missing "
            "decision/resource, or say that this campaign should stop."
        )
        item = None
        revision = state.revision_request or {}
        item_id = str(revision.get("item_id") or "")
        if item_id:
            item = next(
                (row for row in self.memory.backlog.history() if row.id == item_id),
                None,
            )
        if item is None:
            from ..memory import BacklogItem

            item = self.memory.backlog.add(
                BacklogItem.new(
                    title="Planner needs operator direction",
                    objective=str(self.config.continuous_objective or question),
                    tags=["planner", "operator_decision", "scope:bounded"],
                    iterate=False,
                )
            )
        try:
            from ...core.operator_decision import build_operator_decision

            card = build_operator_decision(
                item_id=item.id,
                title=item.title,
                reason=reason or "Planner produced no executable task.",
                question=question,
                project_id=self.memory.root.name,
            )
        except Exception:  # noqa: BLE001 - the plain question is sufficient
            card = {}
        self.memory.backlog.update(
            item.id,
            status="paused_operator",
            pending_question=question,
            operator_decision=card,
            last_error=reason,
        )
        self._emit({
            "type": EventType.LIFE_OPERATOR_QUESTION_PENDING,
            "item_id": item.id,
            "title": item.title,
            "question": question,
            "agent_layer": "planner",
        })
        self._emit({
            "type": EventType.LIFE_PLANNER_ERROR,
            "cycle": self._planning_cycles,
            "error": verdict.error,
            "raw_text": verdict.raw_text,
            "operator_alert": True,
            "recoverable": True,
            "stop_kind": "operator_input_required",
        })
        self._emit_status("planner has no concrete task; waiting for operator direction")
        return PLAN_AWAITING

    def _pc_invoke_planner(self, state: _PlanCycleState) -> Any | None:
        if state.verdict is not None:
            return None
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
        stuck_families_note = self._stuck_subagent_families_note(state.subagent_family_failures)

        try:
            from ...planner import Planner

            refresh_skill_store = getattr(
                self.runner, "_refresh_manager_skill_store", None
            )
            runner_args = getattr(self.runner, "_args", None)
            if callable(refresh_skill_store) and runner_args is not None:
                refresh_skill_store(
                    runner_args,
                    workdir=self._planner_workdir(),
                )
            latest_skill_store = getattr(self.runner, "_manager_skill_store", None)
            if latest_skill_store is not None:
                self.skill_store = latest_skill_store
            planner = Planner(
                self.planner_runner,
                skill_store=self.skill_store,
                memory_maintenance_enabled=getattr(
                    self.config,
                    "role_skill_maintenance_enabled",
                    True,
                ),
            )
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                state.verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,
                    research_plan=self._render_research_plan_for_planner(),
                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary="\n\n".join(
                        part
                        for part in (
                            self._manager_intent_prompt_block(
                                state.manager_intent,
                                self.config.continuous_objective,
                            ),
                            operator_note,
                            self._planner_authorization_prompt_block(),
                            stuck_families_note,
                            runtime_note,
                            revision_note,
                        )
                        if part
                    ),
                    config=self._planner_config(),
                )
                self._apply_research_plan_update(
                    getattr(state.verdict, "raw_text", "") or ""
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            if revision_request is not None:
                self._emit(
                    {
                        "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                        "reason": f"planner raised: {type(exc).__name__}: {exc}",
                        "expected_plan_id": state.expected_plan_id,
                        "expected_plan_version": state.expected_plan_version,
                    }
                )
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_ERROR,
                    "cycle": self._planning_cycles,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            self._enter_idle_backoff()
            return PLAN_ERROR
        return None

    def _pc_normalize_verdict(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        verdict = state.verdict

        if verdict.error:
            from ...planner import PLANNER_SUPERSEDED_ERROR

            if str(verdict.error).startswith(PLANNER_SUPERSEDED_ERROR):
                self._emit({
                    "type": "life.planner.superseded",
                    "cycle": self._planning_cycles,
                    "reason": PLANNER_SUPERSEDED_ERROR,
                })
                self._emit_status(
                    "planner: stopped obsolete planning after a newer operator instruction"
                )
                self._reset_idle_backoff()
                return PLAN_RETRY
            if revision_request is not None:
                self._emit(
                    {
                        "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                        "reason": verdict.error,
                        "expected_plan_id": state.expected_plan_id,
                        "expected_plan_version": state.expected_plan_version,
                    }
                )
            reconciliation = ""
            from ...planner import NO_CONCRETE_TASKS_ERROR

            if str(verdict.error).startswith(NO_CONCRETE_TASKS_ERROR):
                # "Not done, and I have no task to propose" is the Planner
                # reporting that the work has run out at this stage — most
                # sharply on a replan, where the Reviewer has just called the
                # present direction a dead end. Handing that to the Manager, the
                # sole stage authority, to roll back or hold is what lets the
                # Planner enqueue earlier-stage work next cycle and get itself
                # out. Nothing here judges the science; the Manager decides.
                #
                # This was skipped whenever a revision was in flight, which is
                # exactly when the Planner most needs it: the verdict became a
                # plain error, the cycle backed off, the pending item was claimed
                # again, and the same mission reran. One project did that 100
                # times across 75 hours without ever changing course.
                reconciliation = self._reconcile_open_ended_terminal_stage_action(verdict)
                if not reconciliation and revision_request is None:
                    # Replaying an unassessed review is post-upgrade recovery;
                    # during a replan that review has already been assessed —
                    # assessing it is what produced the revision request.
                    reconciliation = self._reconcile_reviewed_stage_empty_plan(verdict)
            if reconciliation in {"advance", "complete", "rollback"}:
                return PLAN_RETRY
            if reconciliation == "hold":
                return self._pc_complete_terminal_empty_plan(state)
            if str(verdict.error).startswith(NO_CONCRETE_TASKS_ERROR):
                return self._pause_empty_plan_for_operator(state)
            content_filtered = _is_content_filter_failure(
                verdict.error,
                verdict.raw_text,
            )
            if content_filtered:
                # Replaying identical bytes reproduces a provider policy refusal.
                # Disarm the standing campaign and require an operator-authored
                # reformulation instead of retrying forever.
                try:
                    from ...daemon.state import (
                        compare_and_swap_continuous_config,
                        read_continuous_state,
                    )

                    current = read_continuous_state(self.memory.root)
                    if current.enabled:
                        compare_and_swap_continuous_config(
                            self.memory.root,
                            expected=current,
                            enabled=False,
                            objective=current.objective,
                            done_reason=(
                                "planner response blocked by content filtering; "
                                "operator reformulation required"
                            ),
                        )
                except Exception:  # noqa: BLE001 - event still surfaces the block
                    log.exception("failed to disarm content-filtered campaign")
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_ERROR,
                    "cycle": self._planning_cycles,
                    "error": verdict.error,
                    "raw_text": verdict.raw_text,
                    **(
                        {
                            "operator_alert": True,
                            "recoverable": False,
                            "stop_kind": "permanent_error",
                        }
                        if content_filtered
                        else {}
                    ),
                }
            )
            self._emit_status(
                (
                    "planner blocked by content filtering; campaign paused for "
                    "operator reformulation"
                )
                if content_filtered
                else f"planner error: {verdict.error}; retry later"
            )
            # A planner error is a no-work outcome: back off before retrying so
            # a persistently-failing planner cannot spin every poll interval.
            self._enter_idle_backoff()
            return PLAN_ERROR

        for diagnostic in getattr(verdict, "diagnostics", ()):
            log.warning("planner verdict normalized: %s", diagnostic)
            self._emit({
                "type": "life.planner.normalized",
                "cycle": self._planning_cycles,
                "diagnostic": str(diagnostic),
            })

        if revision_request is None:
            verdict = self._normalize_live_subagent_wait(verdict)
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
            self._emit(
                {
                    "type": "life.planner.wait_overridden",
                    "cycle": self._planning_cycles,
                    "task_title": overlap_task.title,
                    "reason": verdict.reason,
                }
            )
        state.verdict = verdict
        return None


__all__ = ["PlanningCycleVerdictMixin"]
