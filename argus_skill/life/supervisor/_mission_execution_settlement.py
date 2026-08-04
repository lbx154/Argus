"""Mission lifecycle phases: repair settlement, stage guard, journal.

``MissionExecutionSettlementMixin`` covers the second half of one claimed
backlog item's life, after ``MissionExecutionRuntimeMixin`` has produced a raw
outcome: closing out the restricted validator-repair capability (if any),
enforcing the dynamic-plan stage guard, resolving the final mission status
against the backlog, and emitting the ``LIFE_MISSION_COMPLETED`` journal event
plus the ``_run_one`` return dict.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ...core.event_catalog import EventType
from ...core.stop_kinds import stop_kind_is_recoverable
from ..memory import BacklogItem
from ..mission_outcome import mission_outcome_class, mission_outcome_dimensions
from ._constants import (
    _REPLAN_STREAK_JOURNAL_WINDOW,
    PLANNER_RECENT_FAILURE_STATUS,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    consecutive_replan_escalation_threshold,
)
from ._helpers import _normalize_planner_text
from ._mission_execution_helpers import _MissionRunState

log = logging.getLogger(__name__)


class MissionExecutionSettlementMixin:
    """Repair settlement, stage guard, final status, and journal emission."""

    # ------------------------------------------------------------------
    # Phase: restricted validator-repair capability settlement
    # ------------------------------------------------------------------

    def _settle_repair_capability(self, state: _MissionRunState) -> None:
        """Close (or adopt a recovered) repair capability, mutating outcome.

        A rejected repair capability always downgrades the mission to a
        permanent error, regardless of what the Engineer/Reviewer reported.
        """
        outcome = state.outcome
        repair_settlement: dict[str, Any] | None = None
        if state.recovered_repair_settlement is not None:
            repair_settlement = state.recovered_repair_settlement
            if not bool(repair_settlement.get("accepted")):
                state.success = False
                state.status = "error"
                state.stop_kind = "permanent_error"
                guard_errors = list(repair_settlement.get("guard_errors") or [])
                state.stop_reason = (
                    "restricted validator repair rejected"
                    + (": " + "; ".join(guard_errors) if guard_errors else "")
                )
        elif state.repair_capability is not None and state.repair_store is not None:
            reviewer_status = str(
                getattr(outcome, "final_review_status", "") or ""
            ).strip().lower()
            reviewer_accepted = bool(
                state.success
                and state.status == "done"
                and reviewer_status == "done"
            )
            try:
                repair_settlement = state.repair_store.close_repair_capability(
                    capability_id=str(state.repair_capability["capability_id"]),
                    nonce=str(state.repair_capability["nonce"]),
                    identity=state.repair_identity,
                    accepted=reviewer_accepted,
                    reason=(
                        str(getattr(outcome, "stop_reason", "") or "")
                        or f"Reviewer status={reviewer_status or 'missing'}"
                    ),
                )
            except (OSError, TypeError, ValueError) as exc:
                repair_settlement = {
                    "status": "rejected",
                    "accepted": False,
                    "guard_errors": [f"{type(exc).__name__}: {exc}"],
                }
            if not bool(repair_settlement.get("accepted")):
                state.success = False
                state.status = "error"
                state.stop_kind = "permanent_error"
                guard_errors = list(repair_settlement.get("guard_errors") or [])
                state.stop_reason = (
                    "restricted validator repair rejected"
                    + (": " + "; ".join(guard_errors) if guard_errors else "")
                )
        state.repair_settlement = repair_settlement

    # ------------------------------------------------------------------
    # Phase: dynamic-plan stage guard + terminal stage-transition handling
    # ------------------------------------------------------------------

    def _apply_dynamic_plan_stage_guard(self, state: _MissionRunState) -> None:
        """Undo a premature Manager stage advance inside an unfinished DAG.

        A Planner DAG is authored entirely inside the current-stage frontier;
        the Planner is forbidden to enqueue speculative downstream-stage work.
        Therefore an intermediate node must not let the Manager advance the
        project while sibling/dependent nodes from the same plan are
        unfinished. The Manager decision has already mutated PIPELINE_STATE by
        this point, so undo that premature advance and expose a HOLD
        transition locally.
        """
        item = state.item
        outcome = state.outcome
        stage_transition = getattr(outcome, "stage_transition", {})
        stage_action = (
            str(stage_transition.get("action") or "").strip().lower()
            if isinstance(stage_transition, dict)
            else ""
        )
        normalized_tags = {
            str(tag).strip().lower().replace("-", "_")
            for tag in getattr(item, "tags", [])
        }
        planner_bounded_node = (
            "planner" in normalized_tags
            and self._planner_scope_from_item(item) == PLANNER_SCOPE_BOUNDED
        )
        unfinished_plan_nodes: list[BacklogItem] = []
        if planner_bounded_node and item.plan_id:
            try:
                unfinished_plan_nodes = [
                    sibling
                    for sibling in self.memory.backlog.all()
                    if sibling.id != item.id
                    and sibling.plan_id == item.plan_id
                    and sibling.plan_version == item.plan_version
                    and sibling.status
                    not in {"done", "failed", "skipped", "superseded"}
                ]
            except Exception:  # noqa: BLE001 - stage safety falls back to Manager
                log.exception(
                    "life supervisor: failed to inspect dynamic plan before stage guard"
                )
        if (
            stage_action == "advance"
            and state.pipeline_stage_at_start
            and unfinished_plan_nodes
        ):
            live_stage = (
                self._current_pipeline_stage() or state.pipeline_stage_at_start
            )
            guard_reason = (
                f"dynamic plan {item.plan_id} still has unfinished current-stage "
                "node(s): "
                + ", ".join(node.title for node in unfinished_plan_nodes[:6])
            )
            guard_applied = live_stage == state.pipeline_stage_at_start
            if not guard_applied:
                try:
                    from ...skills.stage_machine import rollback_stage

                    rollback_stage(
                        self._artifact_root(),
                        target_stage=state.pipeline_stage_at_start,
                        reason=guard_reason,
                        rolled_back_by="supervisor_dynamic_plan_guard",
                    )
                    guard_applied = True
                except Exception:  # noqa: BLE001
                    log.exception(
                        "life supervisor: failed to undo premature dynamic-plan "
                        "stage advance"
                    )
            if guard_applied:
                self._emit({
                    "type": EventType.LIFE_MANAGER_STAGE_DECISION,
                    "action": "rollback",
                    "target_stage": state.pipeline_stage_at_start,
                    "reason": guard_reason,
                    "current_stage": live_stage,
                    "source": "supervisor_dynamic_plan_guard",
                    "diagnostic": "unfinished_same_plan_nodes",
                    "item_id": item.id,
                    "plan_id": item.plan_id,
                    "unfinished_item_ids": [
                        node.id for node in unfinished_plan_nodes
                    ],
                })
                stage_transition = {
                    "action": "hold",
                    "current_stage": state.pipeline_stage_at_start,
                    "target_stage": state.pipeline_stage_at_start,
                    "reason": guard_reason,
                    "source": "supervisor_dynamic_plan_guard",
                    "diagnostic": "unfinished_same_plan_nodes",
                }
                stage_action = "hold"
        state.stage_transition = stage_transition
        state.stage_action = stage_action
        state.planner_bounded_node = planner_bounded_node

    def _maybe_short_circuit_for_stage_transition(
        self, state: _MissionRunState,
    ) -> dict[str, Any] | None:
        """Handle stage-continues / stage-hold early returns.

        ``research_incomplete`` is project-level: it says the persisted final
        research target is not finished. It must NOT cancel a
        Manager-certified intermediate stage transition. A scope mission can
        legitimately end with project-level research still incomplete while
        the Manager advances ``scope -> solve`` (or rolls back to repair an
        earlier stage). In that case the same bounded item stays pending and
        continues automatically. Explicit failures, holds, budget/provider
        pauses, and infrastructure blocks do not enter this path.

        Also applies the ``planner_node_stage_completed`` override (a
        Planner-authored bounded DAG node closes once the Manager certifies
        ``advance`` for the current project stage, even if the Reviewer
        described the WHOLE project as ``research_incomplete``) when neither
        short-circuit fires.
        """
        item = state.item
        outcome = state.outcome
        stage_transition = state.stage_transition
        stage_action = state.stage_action
        success = state.success
        status = state.status

        project_incomplete_but_stage_progressed = (
            status == "research_incomplete"
            and stage_action in {"advance", "rollback"}
        )
        staged_item_continues = (
            (success or project_incomplete_but_stage_progressed)
            and not self.config.continuous
            and not state.planner_bounded_node
            and isinstance(stage_transition, dict)
            and bool(stage_transition)
            and stage_action in {"advance", "rollback"}
        )
        if staged_item_continues:
            self.memory.backlog.update(
                item.id,
                status="pending",
                started_ts=None,
                finished_ts=None,
                last_error="",
                consecutive_replans=0,
                replan_streak_tracked=True,
            )
            return {
                "success": True,
                "status": "stage_continues",
                "item_id": item.id,
                "stage_transition": stage_transition,
                "cost_usd": state.usd,
                "known_cost_usd": state.known_usd,
                "pricing_status": state.usage_summary.pricing_status,
            }

        bounded_stage_hold = (
            (success or status == "research_incomplete")
            and not self.config.continuous
            and not state.planner_bounded_node
            and isinstance(stage_transition, dict)
            and bool(stage_transition)
            and stage_action == "hold"
        )
        if bounded_stage_hold:
            hold_reason = str(
                stage_transition.get("reason")
                or "Manager held the current stage"
            )
            hold_outcome = mission_outcome_dimensions(
                status="stage_hold",
                success=False,
                review_status=str(
                    getattr(outcome, "final_review_status", "") or ""
                ),
                stage_transition=stage_transition,
                stop_kind=state.stop_kind,
                resumable=False,
            )
            self.memory.backlog.mark_failed(
                item.id,
                error=f"manager stage hold: {hold_reason}",
                outcome=hold_outcome,
            )
            self._update_no_progress_streak(
                kind="mission_failed",
                report={
                    "forward_progress": False,
                    "headline": "manager stage decision: hold",
                },
            )
            return {
                "success": False,
                "status": "stage_hold",
                "item_id": item.id,
                "stage_transition": stage_transition,
                "cost_usd": state.usd,
                "known_cost_usd": state.known_usd,
                "pricing_status": state.usage_summary.pricing_status,
            }

        # Planner-authored bounded DAG nodes are separate acceptance units: once
        # the Manager has certified ``advance`` for the current project stage,
        # that node is complete even if the Reviewer described the WHOLE project
        # as ``research_incomplete``. Close the node so its solve/review dependent
        # can unlock. A HOLD remains incomplete; a ROLLBACK is not silently
        # treated as node success.
        planner_node_stage_completed = (
            state.planner_bounded_node
            and status == "research_incomplete"
            and stage_action == "advance"
        )
        if planner_node_stage_completed:
            state.success = True
            state.status = "done"
            state.stop_reason = ""
        return None

    # ------------------------------------------------------------------
    # Phase: final status resolution against the backlog
    # ------------------------------------------------------------------

    def _count_consecutive_item_replans(self, item_id: str) -> int:
        """Trailing consecutive replan_requested missions journaled for one item.

        Walks the journal newest-first and counts ``mission_replan_requested``
        entries for ``item_id``, stopping at the first forward-progress marker
        (``mission_complete``) for that item. The current mission's own replan
        has not been journaled yet, so this is the count of PRIOR consecutive
        replans; the caller adds one for the in-flight outcome.
        """
        try:
            entries = self.memory.journal.tail(_REPLAN_STREAK_JOURNAL_WINDOW)
        except Exception:  # noqa: BLE001 - guard degrades to current behavior
            log.exception(
                "life supervisor: failed to read journal for replan streak"
            )
            return 0
        count = 0
        for entry in reversed(entries):
            if str(getattr(entry, "id", "") or "") != item_id:
                continue
            kind = str(getattr(entry, "kind", "") or "")
            if kind == "mission_complete":
                break
            if kind == "mission_replan_requested":
                count += 1
        return count

    def _finalize_mission_status(self, state: _MissionRunState) -> None:
        item = state.item
        outcome = state.outcome
        status = state.status
        success = state.success
        stage_transition = state.stage_transition
        stage_action = state.stage_action

        operator_question = str(
            getattr(outcome, "operator_question", "") or ""
        ).strip()
        research_pause = status in {
            "research_incomplete",
            "paused_no_breakthrough",
            "exhausted_current_methods",
            "infra_blocked",
        }
        replan_requested = status == "replan_requested"
        if replan_requested and operator_question:
            # A replacement plan cannot authorize a semantic boundary decision.
            # Route the question through the durable operator-answer path instead
            # of immediately asking Planner to redispatch the same mission.
            status = "blocked"
            replan_requested = False
        intentional_abort = status == "aborted" or state.stop_kind == "operator_abort"
        if intentional_abort:
            success = False
            status = "aborted"
        stage_reconciled_replan = (
            replan_requested and stage_action in {"advance", "rollback"}
        )
        err = state.exc_str or state.stop_reason or "unspecified failure"
        resumable = bool(
            research_pause or stop_kind_is_recoverable(state.stop_kind)
        )
        outcome_dimensions = mission_outcome_dimensions(
            status=status,
            success=success,
            review_status=str(
                getattr(outcome, "final_review_status", "") or ""
            ),
            stage_transition=stage_transition,
            stage_transition_skipped=(
                self._item_skips_stage_transition(item)
                or bool(getattr(outcome, "stage_transition_skipped", False))
            ),
            stop_kind=state.stop_kind,
            resumable=resumable,
        )

        # Update backlog row. A bounded research cycle that did not achieve its
        # persisted success target is resumable, not a success or terminal failure.
        if success:
            self.memory.backlog.mark_done(item.id, outcome=outcome_dimensions)
        elif status == "blocked" and operator_question:
            # Status and the authority-bearing question must reach disk in one
            # backlog transaction. Keep the row nonterminal so dependency
            # reconciliation cannot cascade-skip its downstream plan while the
            # operator is deciding; the answer transaction terminalizes it and
            # rewires those dependencies to the continuation atomically.
            self.memory.backlog.update(
                item.id,
                status="paused_operator",
                finished_ts=time.time(),
                last_error=err,
                outcome=outcome_dimensions,
                pending_question=operator_question,
            )
        elif stage_reconciled_replan:
            self.memory.backlog.mark_failed(
                item.id,
                error=(
                    f"manager {stage_action} to "
                    f"{stage_transition.get('target_stage') or 'another stage'} "
                    "after Reviewer identified an upstream stage defect"
                ),
                outcome=outcome_dimensions,
            )
        elif replan_requested:
            # Bounded convergence guard. A refuted node that keeps returning
            # replan_requested with no intervening forward progress
            # (mission_complete) must not be re-dispatched forever. Count the
            # consecutive replans already journaled for this item and add the
            # in-flight one; at/above the threshold, stop resetting to pending
            # and escalate to a terminal no-progress failure that the planner
            # quarantine (kind=mission_failed, terminal_status=no_progress)
            # recognizes, so the daemon idles/escalates instead of re-running.
            prior_replans = int(
                getattr(item, "consecutive_replans", 0) or 0
            )
            if not bool(getattr(item, "replan_streak_tracked", False)):
                prior_replans = max(
                    prior_replans,
                    self._count_consecutive_item_replans(item.id),
                )
            consecutive_replans = prior_replans + 1
            if consecutive_replans >= consecutive_replan_escalation_threshold():
                replan_requested = False
                success = False
                status = PLANNER_RECENT_FAILURE_STATUS
                err = (
                    f"no forward progress after {consecutive_replans} "
                    "consecutive replan_requested outcomes on this node"
                    + (f": {state.stop_reason}" if state.stop_reason else "")
                )
                outcome_dimensions = mission_outcome_dimensions(
                    status=status,
                    success=success,
                    review_status=str(
                        getattr(outcome, "final_review_status", "") or ""
                    ),
                    stage_transition=stage_transition,
                    stop_kind=state.stop_kind,
                    resumable=resumable,
                )
                self.memory.backlog.mark_failed(
                    item.id,
                    error=err,
                    outcome=outcome_dimensions,
                )
            else:
                self.memory.backlog.update(
                    item.id,
                    status="pending",
                    started_ts=None,
                    finished_ts=None,
                    last_error=state.stop_reason,
                    consecutive_replans=consecutive_replans,
                    replan_streak_tracked=True,
                )
        elif research_pause:
            self.memory.backlog.update(
                item.id,
                status=status,
                finished_ts=time.time(),
                last_error=state.stop_reason,
                outcome=outcome_dimensions,
            )
        elif intentional_abort:
            self.memory.backlog.update(
                item.id,
                status="aborted",
                finished_ts=time.time(),
                last_error=state.stop_reason,
                outcome=outcome_dimensions,
            )
        else:
            self.memory.backlog.mark_failed(
                item.id,
                error=err,
                outcome=outcome_dimensions,
            )

        # A "blocked" verdict means the REVIEWER stopped progress because it
        # needs the OPERATOR to make a call — not a bug/crash. This includes a
        # replan verdict carrying an operator question, normalized above so
        # Planner cannot proceed without authorization. Persist the question
        # onto the (now-terminal) item so it outlives this one event:
        # /status can list every currently-unanswered question across ALL
        # projects/restarts, not just whatever a cockpit happened to be tailing
        # live when it was asked (the old process-local state
        # ``blocked_question``, which was lost the moment that process exited).
        # The operator pause and question were persisted atomically above.
        if status == "blocked" and operator_question:
            self._emit({
                "type": EventType.LIFE_OPERATOR_QUESTION_PENDING,
                "item_id": item.id,
                "title": item.title,
                "question": operator_question,
                "agent_layer": "manager",
            })

        state.success = success
        state.status = status
        state.research_pause = research_pause
        state.replan_requested = replan_requested
        state.intentional_abort = intentional_abort
        state.stage_reconciled_replan = stage_reconciled_replan
        state.err = err
        state.resumable = resumable
        state.outcome_dimensions = outcome_dimensions

    # ------------------------------------------------------------------
    # Phase: journal event + return dict
    # ------------------------------------------------------------------

    def _emit_mission_outcome_and_build_result(
        self, state: _MissionRunState,
    ) -> dict[str, Any]:
        item = state.item
        outcome = state.outcome
        success = state.success
        status = state.status

        kind = (
            "mission_complete"
            if success
            else "mission_replan_requested"
            if state.replan_requested
            else "mission_aborted"
            if state.intentional_abort
            else "mission_failed"
        )
        final_submission_certified = bool(
            kind == "mission_complete"
            and state.item_scope == PLANNER_SCOPE_FINAL_SUBMISSION
            and getattr(outcome, "final_submission_certified", False)
        )
        final_submission_signature = (
            self._final_submission_signature()
            if final_submission_certified
            else ""
        )

        self._update_no_progress_streak(
            kind=kind,
            report=getattr(outcome, "final_planner_report", {}) or {},
        )

        cost_sink = state.cost_sink
        scientist_totals = cost_sink.scientist_totals()
        scientist_usage_by_model = cost_sink.scientist_usage_by_model_snapshot()
        self._capture_failure_experience(state)
        self._emit({
            "type": EventType.LIFE_MISSION_COMPLETED,
            "item_id": item.id,
            "title": item.title,
            "objective": item.objective,
            "scope": state.item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
            "success": success,
            "status": status,
            "outcome_class": mission_outcome_class(status=status, success=success),
            "outcome": state.outcome_dimensions,
            "rounds": state.rounds,
            "elapsed_seconds": state.elapsed,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "usage_record_count": state.usage_summary.call_count,
            "partial_usage_records": state.usage_summary.partial_calls,
            "unpriced_usage_records": state.usage_summary.unpriced_calls,
            "planner_task_signature": {
                "title": _normalize_planner_text(item.title),
                "objective": _normalize_planner_text(item.objective),
            }
            if kind == "mission_failed"
            else {},
            "terminal_status": status if kind == "mission_failed" else "",
            "resumable": state.resumable,
            "recoverable": bool(
                getattr(outcome, "recoverable", False)
                or stop_kind_is_recoverable(state.stop_kind)
            ),
            "stop_kind": state.stop_kind,
            "stop_reason": (
                state.stop_reason or state.err
                if kind in {"mission_failed", "mission_aborted"}
                else ""
            ),
            "failure_reason": state.err if kind == "mission_failed" else "",
            "agent_layer": "engineer",
            "engineer_model": self.engineer_model,
            "reviewer_model": self.reviewer_model,
            "scientist_cost_usd": cost_sink.scientist_usd(),
            "engineer_cost_usd": cost_sink.engineer_usd(),
            "reviewer_cost_usd": cost_sink.reviewer_usd(),
            # util (manager/classify) + copilot premium-request cost were folded
            # into total_usd() but never surfaced in the breakdown — emit them so
            # the cost is fully auditable. copilot_premium_requests is the raw
            # count (GitHub bills per premium request, flat $/req — NOT per token,
            # so a copilot mission's whole dollar cost is this count * rate).
            "util_cost_usd": cost_sink.util_usd(),
            "copilot_cost_usd": cost_sink.copilot_usd(),
            "copilot_premium_requests": cost_sink.copilot_premium_request_total(),
            "scientist_input_tokens": scientist_totals[0],
            "scientist_cached_input_tokens": scientist_totals[1],
            "scientist_output_tokens": scientist_totals[2],
            "scientist_reasoning_output_tokens": scientist_totals[3],
            "scientist_usage_by_model": {
                model: {
                    "input_tokens": values[0],
                    "cached_input_tokens": values[1],
                    "output_tokens": values[2],
                    "reasoning_output_tokens": values[3],
                }
                for model, values in scientist_usage_by_model.items()
            },
            "input_tokens": cost_sink.total_input_tokens(),
            "cached_input_tokens": cost_sink.total_cached_input_tokens(),
            "cache_write_tokens": cost_sink.total_cache_write_tokens(),
            "output_tokens": cost_sink.total_output_tokens(),
            "reasoning_output_tokens": cost_sink.total_reasoning_output_tokens(),
            "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
            "final_submission_certified": final_submission_certified,
            "final_submission_signature": final_submission_signature,
            "repair_capability": {
                "capability_id": str(state.repair_capability.get("capability_id") or ""),
                "authorization_id": str(
                    state.repair_capability.get("authorization_id") or ""
                ),
                "status": str((state.repair_settlement or {}).get("status") or ""),
                "accepted": bool((state.repair_settlement or {}).get("accepted", False)),
                "guard_errors": list(
                    (state.repair_settlement or {}).get("guard_errors") or []
                ),
            } if state.repair_capability is not None else None,
            "iteration": None,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "tags": list(item.tags),
            "execution_workdir": str(state.execution_workdir),
            "success": success,
            "status": status,
            "review_status": str(
                getattr(outcome, "final_review_status", "") or ""
            ),
            "stop_reason": state.stop_reason,
            "rounds": state.rounds,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "iteration": None,
            "auth_failure": state.auth_failure,
            "review_reason": str(
                getattr(outcome, "final_review_reason", "")
                or getattr(outcome, "reason", "")
                or ""
            ),
            "expected_plan_id": item.plan_id,
            "expected_plan_version": item.plan_version,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        }

    def _capture_failure_experience(self, state: _MissionRunState) -> None:
        """Persist one compact capsule without reading referenced artifacts."""
        if state.success or state.intentional_abort:
            return
        store = getattr(self.memory, "failure_experiences", None)
        if store is None:
            return
        try:
            from ..failure_experience import experience_from_settled_mission

            item = state.item
            outcome = state.outcome
            refs = [
                str(ref.get("path") or ref.get("ref") or "").strip()
                for ref in (getattr(item, "context_refs", []) or [])
                if isinstance(ref, dict)
            ]
            if state.context_packet_path is not None:
                refs.append(str(state.context_packet_path.parent / "latest.json"))
            experience = experience_from_settled_mission(
                mission_id=item.id,
                title=item.title,
                objective=item.objective,
                status=state.status,
                factual_outcome=state.stop_reason or state.err or state.status,
                final_message=str(getattr(outcome, "final_message", "") or ""),
                review_reason=str(
                    getattr(outcome, "final_review_reason", "")
                    or getattr(outcome, "reason", "")
                    or ""
                ),
                planner_report=getattr(outcome, "final_planner_report", {}) or {},
                stop_kind=str(state.stop_kind or ""),
                recoverable=state.resumable,
                concepts=list(item.tags),
                artifact_refs=[ref for ref in refs if ref],
                non_goals=list(getattr(item, "non_goals", []) or []),
            )
            store.append(experience)
        except (OSError, TypeError, ValueError):
            log.exception("life supervisor: failed to persist failure experience")


__all__ = ["MissionExecutionSettlementMixin"]
