"""Planning-cycle phase: planner invocation and verdict error/overlap handling.

Covers building the planner prompt context and calling ``planner.plan_next()``
(with exception handling), then ``verdict.error`` /
operator-external-blocker-defer / independent-overlap-task normalization that
happens before any handoff/project_done interpretation.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.runner_errors import is_execution_host_startup_error
from ._constants import PLAN_AWAITING, PLAN_ERROR, PLAN_RETRY
from ._planning_cycle_helpers import _PlanCycleState, _render_revision_request

log = logging.getLogger(__name__)


def _is_content_filter_failure(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values).casefold()
    return "content filtering blocked" in text or "blocked by content filtering" in text


class PlanningCycleVerdictMixin:
    """Planner invocation and error/overlap normalization."""

    def _pause_planner_execution_host(self, error: str, *, expected: Any) -> str:
        """Keep a failed Planner paused until the campaign is explicitly rearmed."""
        from ...daemon.state import (
            compare_and_swap_continuous_config,
        )

        if expected is None:
            return PLAN_RETRY
        if not expected.enabled:
            # An existing operator hold keeps its reason and generation.
            return PLAN_AWAITING
        continuous_root = Path(getattr(self.memory, "project_root", None) or self.memory.root)
        # Use the existing durable campaign control and preserve its objective.
        # Compare with the generation captured before the model call, not the
        # latest generation read after a potentially long-running failure.
        if not compare_and_swap_continuous_config(
            continuous_root,
            expected=expected,
            enabled=False,
            objective=expected.objective,
            open_ended=expected.open_ended,
            done_reason=error,
        ):
            return PLAN_RETRY
        self._emit({
            "type": EventType.LIFE_PLANNER_ERROR,
            "cycle": self._planning_cycles,
            "error": error,
            "operator_alert": True,
            "recoverable": True,
            "stop_kind": "backend_unavailable",
            **self._operator_words("parked_infra"),
        })
        self._emit_status(
            "Planner execution host is unavailable; restore the Codex host executable "
            "and explicitly enable the continuous campaign to retry."
        )
        self._enter_pause_backoff()
        return PLAN_AWAITING

    def _park_planner_question(
        self, state: _PlanCycleState | None, question: str, reason: str,
        *, options: tuple[dict[str, Any], ...] = (), about_waypoint: str = "",
    ) -> bool:
        """Put the planning slot's question to the operator on its one card.

        The card is the operator's to answer, or the Manager's while the
        operator cannot be asked, like any parked question. Returns False when
        the planning slot's own question still waits: it stays as it was asked.
        """
        item = None
        revision = getattr(state, "revision_request", None) or {}
        item_id = str(revision.get("item_id") or "")
        if item_id:
            item = next(
                (row for row in self.memory.backlog.history() if row.id == item_id),
                None,
            )
        asks_only = item is None
        if asks_only:
            # One open question per planning slot, kept as it was asked: a
            # Planner that comes back while it waits neither adds a card nor
            # rewords it. Their answer is new input that calls it again; a
            # question another task waits on is that task's, not this one.
            if any(
                "operator_decision" in row.tags and str(row.pending_question or "").strip()
                for row in self.memory.backlog.active()
            ):
                return False
            from ...core.operator_messages import brief_title
            from ..memory import BacklogItem

            # The work resumes under this title once answered: name the work.
            item = self.memory.backlog.add(
                BacklogItem.new(
                    title=brief_title(str(self.config.continuous_objective or question)),
                    objective=str(self.config.continuous_objective or question),
                    tags=["planner", "operator_decision", "scope:bounded"],
                    iterate=False,
                )
            )
        from .pending_notify import ask_operator

        card_fields: dict[str, Any] = {}
        if about_waypoint:
            # A doubted required step: the person keeps, changes or removes it.
            from ..waypoint_cards import dispute_card

            waypoint_states = getattr(self, "_waypoint_states", None)
            options, card_fields = dispute_card(
                waypoint_states() if callable(waypoint_states) else (), about_waypoint, question, options,
                asks_only=asks_only,
            )
        ask_operator(
            self.memory.root, item, question, reason=reason or question, layer="planner",
            save=partial(self.memory.backlog.update, item.id, last_error=reason), emit=self._emit,
            options=options, **card_fields,
        )
        return True

    def _pc_invoke_planner(self, state: _PlanCycleState) -> Any | None:
        if state.verdict is not None:
            return None
        revision_request = state.revision_request
        journal_window = self._planner_journal_window()
        recall = None if journal_window is None else self._planner_recall_context()
        journal_tail = (
            ""
            if journal_window is None
            else self._render_journal_entries_for_planner(journal_window, recall)
        )
        journal_delta, journal_window_keys = self._render_journal_delta_for_planner(
            journal_window, recall
        )
        research_plan = self._render_research_plan_for_planner()
        research_plan_unchanged = research_plan == str(
            getattr(self, "_planner_session_research_plan", "") or ""
        )

        runtime_note = self._planner_runtime_notes()
        revision_note = (
            _render_revision_request(revision_request, state.revision_active_items)
            if revision_request is not None
            else ""
        )

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
                from ...daemon.state import read_continuous_state

                continuous_root = Path(
                    getattr(self.memory, "project_root", None) or self.memory.root
                )
                state.planner_continuous_state = read_continuous_state(continuous_root)
                if (
                    state.planner_continuous_state.enabled
                    and state.planner_continuous_state.objective
                    != str(self.config.continuous_objective or "").strip()
                ):
                    return PLAN_RETRY
                state.planner_invoked = True
                planner_config = self._planner_config()
                state.verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,
                    research_plan=research_plan,
                    journal_delta=journal_delta,
                    research_plan_unchanged=research_plan_unchanged,
                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary="\n\n".join(
                        part
                        for part in (
                            self._manager_intent_prompt_block(
                                state.manager_intent,
                                self.config.continuous_objective,
                            ),
                            self._planner_authorization_prompt_block(),
                            runtime_note,
                            revision_note,
                            *state.operator_messages,
                        )
                        if part
                    ),
                    config=planner_config,
                )
                self._apply_research_plan_update(state.verdict.plan_update)
                # The role session (fresh or resumed) has now been shown this
                # window and this plan, so the next resumed cycle may send only
                # what settles after them. A backend error leaves the record
                # untouched: that session rotates, and a rotated session always
                # receives the full context again.
                if not getattr(state.verdict, "error", ""):
                    if journal_window_keys is not None:
                        self._planner_session_journal_keys = journal_window_keys
                    self._planner_session_research_plan = research_plan
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
            from ...planner import PLANNER_PAUSED_ERROR, PLANNER_SUPERSEDED_ERROR

            if is_execution_host_startup_error(verdict.error):
                return self._pause_planner_execution_host(
                    verdict.error, expected=getattr(state, "planner_continuous_state", None),
                )
            if str(verdict.error).startswith(PLANNER_PAUSED_ERROR):
                # No Planner turn took place: nothing failed or answered. The
                # guard told the operator; progress lifts it.
                state.planner_invoked = False
                self._emit_status(f"planner: {verdict.error}")
                self._enter_pause_backoff()
                return PLAN_AWAITING
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
                            **self._operator_words("parked_content_filter"),
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

        verdict = self._defer_project_done_for_operator_external_blocker(verdict)
        state.verdict = verdict
        return None


__all__ = ["PlanningCycleVerdictMixin"]
