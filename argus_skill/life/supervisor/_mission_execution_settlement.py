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
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.stop_kinds import stop_kind_is_recoverable
from ..memory import BacklogItem
from ..mission_outcome import (
    mission_outcome_class,
    mission_outcome_dimensions,
    review_keeps_mission_resumable,
)
from ._constants import (
    _REPLAN_STREAK_JOURNAL_WINDOW,
    PLANNER_RECENT_FAILURE_STATUS,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    consecutive_replan_escalation_threshold,
)
from ._helpers import _normalize_planner_text
from ._mission_execution_helpers import _MissionRunState
from .pending_notify import notify_pending_question

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
            from ...skills.stage_machine import current_stage

            policy_root = state.vertical_root
            live_stage = current_stage(policy_root) or state.pipeline_stage_at_start
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
                        policy_root,
                        target_stage=state.pipeline_stage_at_start,
                        reason=guard_reason,
                        rolled_back_by="supervisor_dynamic_plan_guard",
                        evidence_root=state.execution_workdir,
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

        review_status = str(
            getattr(outcome, "final_review_status", "") or ""
        ).strip().lower()
        if (
            self._item_is_stage_closing(item)
            and review_status == "done"
            and state.pipeline_stage_at_start
        ):
            try:
                from ...core.stage_certificate import record_stage_review

                record_stage_review(
                    state_root=self.memory.root,
                    project_root=state.vertical_root,
                    stage=state.pipeline_stage_at_start,
                    item=item,
                    manager_action=stage_action or "hold",
                    manager_reason=(
                        str(stage_transition.get("reason") or "")
                        if isinstance(stage_transition, dict)
                        else ""
                    ),
                )
            except Exception:  # noqa: BLE001 - certificate is observability/control aid
                log.exception("life supervisor: failed to record stage review certificate")

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

    def _maybe_requeue_chartered_shortfall(
        self,
        state: _MissionRunState,
    ) -> dict[str, Any] | None:
        """Intercept a trusted, vertical-declared shortfall before ``done``.

        The hard iteration budget is the backlog item's persisted
        ``iteration_max_cycles`` (six by default, matching the existing
        planner/operator cycle knob). ``iteration_cycles_done`` is checked
        against that ceiling and ``iteration_cost_usd`` accumulates the actual
        cost of every cycle that bought another attempt. The host-global daily
        dollar cap remains the monetary admission guard, so this layer does not
        invent a second, conflicting price limit.

        Domain policy is deliberately absent here. The active vertical decides
        whether the charter fell short, writes the replacement objective, and
        reports any measurement-integrity blockers through the core contract.
        """
        item = state.item
        outcome = state.outcome
        if (
            not state.success
            or not bool(getattr(item, "iterate", False))
            or bool(getattr(outcome, "chat_mode", False))
        ):
            return None

        try:
            from ...skills.vertical_select import resolve_vertical
            from ...verticals._base import (
                load_vertical,
                vertical_iteration_assessment,
            )

            vertical = load_vertical(
                resolve_vertical(state.vertical_root),
                project_root=state.vertical_root,
            )
            assessment = vertical_iteration_assessment(
                vertical,
                stage=state.pipeline_stage_at_start,
                scope=state.item_scope,
                project_root=Path(
                    state.execution_workdir or self._project_workdir()
                ),
                state_root=Path(state.vertical_root),
                mission=item,
                outcome=outcome,
            )
        except Exception as exc:  # noqa: BLE001 - settle, but expose the failed guard
            log.exception("life supervisor: vertical iteration assessment failed")
            return {
                "requeued": False,
                "status": "assessment_blocked",
                "cycles_done": int(item.iteration_cycles_done),
                "cycles_max": max(0, int(item.iteration_max_cycles)),
                "cost_so_far_usd": float(item.iteration_cost_usd),
                "stop_reason": (
                    "iteration assessment failed; settled without re-arming: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        if assessment is None:
            return None

        cycles_done = max(0, int(item.iteration_cycles_done))
        cycles_max = max(0, int(item.iteration_max_cycles))
        cost_so_far = round(float(item.iteration_cost_usd), 6)
        base = {
            "requeued": False,
            "cycles_done": cycles_done,
            "cycles_max": cycles_max,
            "cost_so_far_usd": cost_so_far,
            "shortfall": assessment.shortfall,
        }
        if assessment.blocking_issues:
            issues = list(assessment.blocking_issues)
            return {
                **base,
                "status": "measurement_blocked",
                "blocking_issues": issues,
                "stop_reason": (
                    "iteration stopped because the current measurement is not "
                    "trusted: " + "; ".join(issues)
                ),
            }
        if cycles_done >= cycles_max:
            return {
                **base,
                "status": "budget_exhausted",
                "stop_reason": (
                    f"iteration budget ran out: cycle ceiling {cycles_max} reached "
                    f"after ${cost_so_far:.6f} of accumulated iteration cost"
                ),
            }
        if not assessment.objective.strip():
            return {
                **base,
                "status": "assessment_blocked",
                "stop_reason": (
                    "iteration stopped because the vertical identified a shortfall "
                    "without a safe next-cycle objective"
                ),
            }

        requeued = self.memory.backlog.requeue_for_iteration(
            item.id,
            new_objective=assessment.objective,
            cost_delta_usd=state.usd,
        )
        if requeued is None:
            return {
                **base,
                "status": "requeue_failed",
                "stop_reason": "iteration stopped because the backlog item could not be re-armed",
            }
        iteration = {
            **base,
            "requeued": True,
            "status": "requeued",
            "cycles_done": requeued.iteration_cycles_done,
            "cost_so_far_usd": requeued.iteration_cost_usd,
            "new_objective": requeued.objective,
        }
        self._emit({
            "type": "life.iteration.continued",
            "item_id": item.id,
            **iteration,
        })
        return iteration

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
        if operator_question:
            from ...core.autonomy import (
                assess_operator_intervention,
                technical_continuation,
            )

            planner_report = dict(
                getattr(outcome, "final_planner_report", {}) or {}
            )
            intervention = assess_operator_intervention(
                question=operator_question,
                reason=str(getattr(outcome, "final_review_reason", "") or ""),
                next_action=str(getattr(outcome, "final_review_next_action", "") or ""),
                planner_report=planner_report,
            )
            if not intervention.required:
                continuation = technical_continuation(
                    question=operator_question,
                    reason=str(
                        getattr(outcome, "final_review_reason", "") or ""
                    ),
                    next_action=str(
                        getattr(outcome, "final_review_next_action", "") or ""
                    ),
                )
                planner_report.update({
                    "forward_progress": False,
                    "plan_signal": "reconsider",
                    "challenge": str(
                        planner_report.get("challenge")
                        or getattr(outcome, "final_review_reason", "")
                        or operator_question
                    ),
                    "alternative": continuation,
                    "authority_impact": "technical",
                    "auto_continued": True,
                })
                outcome.final_planner_report = planner_report
                outcome.operator_question = ""
                self._emit({
                    "type": EventType.LIFE_MANAGER_PLAN_CHALLENGE_DECIDED,
                    "item_id": item.id,
                    "manager_action": "replace",
                    "manager_reason": intervention.reason,
                    "challenge": operator_question,
                    "alternative": continuation,
                    "authority_impact": "technical",
                    "source": "pragmatic_autonomy_policy",
                    "text": (
                        "Argus kept a reversible technical choice inside the team "
                        "instead of interrupting the operator."
                    ),
                })
                operator_question = ""
                if status in {"blocked", "replan_requested"}:
                    status = "replan_requested"
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
        iteration = state.iteration
        iteration_requeued = state.iteration_requeued
        if iteration and not iteration_requeued:
            state.stop_reason = str(iteration.get("stop_reason") or state.stop_reason)
        err = state.exc_str or state.stop_reason or "unspecified failure"
        final_review_status = str(getattr(outcome, "final_review_status", "") or "")
        resumable = bool(
            research_pause
            or stop_kind_is_recoverable(state.stop_kind)
            # A stall the Reviewer answered with ``continue`` is unfinished
            # work, not a dead task. Without this the mission is journaled with
            # ``terminal_status=no_progress`` and ``resumable=False``, which
            # quarantines its own signature out of the next planning cycle.
            or review_keeps_mission_resumable(
                status=status,
                success=success,
                review_status=final_review_status,
                stop_kind=state.stop_kind,
            )
        )
        outcome_dimensions = mission_outcome_dimensions(
            status=status,
            success=success,
            review_status=final_review_status,
            stage_transition=stage_transition,
            stage_transition_skipped=(
                self._item_skips_stage_transition(item)
                or bool(getattr(outcome, "stage_transition_skipped", False))
            ),
            stage_transition_deferred=bool(
                getattr(outcome, "stage_transition_deferred", False)
            ),
            stop_kind=state.stop_kind,
            resumable=resumable,
        )
        if iteration is not None:
            outcome_dimensions["iteration"] = dict(iteration)

        manager_decision = getattr(item, "manager_decision", {}) or {}
        learned_candidate = bool(
            isinstance(manager_decision, dict)
            and manager_decision.get("learned_vertical_status") == "candidate"
        )
        if learned_candidate and not iteration_requeued:
            from ...verticals._data_domain import (
                promote_data_domain,
                record_data_domain_failure,
            )

            vertical = str(manager_decision.get("vertical") or "")
            review_status = str(
                getattr(outcome, "final_review_status", "") or ""
            ).strip().lower()
            review_source = str(
                getattr(outcome, "final_review_source", "") or ""
            ).strip().lower()
            if success and review_status == "done" and review_source == "reviewer":
                try:
                    promoted = promote_data_domain(
                        self.memory.root,
                        self._budget_global_root(),
                        vertical,
                        review_reason=str(
                            getattr(outcome, "final_review_reason", "") or ""
                        ),
                    )
                except OSError as exc:
                    log.exception(
                        "life supervisor: learned vertical promotion failed"
                    )
                    self._emit({
                        "type": "life.learned_vertical.promotion_failed",
                        "vertical": vertical,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    promoted = False
                if promoted:
                    manager_decision = dict(manager_decision)
                    manager_decision["learned_vertical_status"] = "formal"
                    self.memory.backlog.update(
                        item.id,
                        manager_decision=manager_decision,
                    )
                    self._emit({
                        "type": "life.learned_vertical.promoted",
                        "vertical": vertical,
                    })
            elif not success:
                try:
                    record_data_domain_failure(
                        self.memory.root,
                        vertical,
                        reason=state.stop_reason or status,
                    )
                except OSError:
                    log.exception(
                        "life supervisor: learned vertical failure note could not be saved"
                    )

        forbid_operator_parking = False
        if status == "blocked" and operator_question:
            from ...manager.directive import active_operator_question_policy

            forbid_operator_parking = (
                active_operator_question_policy(self.memory.root) == "forbid"
            )
            if forbid_operator_parking:
                operator_question = ""
                outcome.operator_question = ""
                outcome.operator_options = []

        # Update backlog row. A bounded research cycle that did not achieve its
        # persisted success target is resumable, not a success or terminal failure.
        if success and iteration_requeued:
            # ``requeue_for_iteration`` already performed the only backlog
            # transition allowed here: running -> pending on the same item.
            pass
        elif success:
            self.memory.backlog.mark_done(item.id, outcome=outcome_dimensions)
            if "runtime_failure_canary" in state.item_tags:
                try:
                    from ..runtime_failure_circuit import clear_runtime_failure_circuit

                    circuit_cleared = clear_runtime_failure_circuit(
                        self.memory.root,
                        reason=f"reviewed runtime canary passed in mission {item.id}",
                    )
                    self._emit({
                        "type": EventType.LIFE_RUNTIME_FAILURE_CANARY_PASSED,
                        "item_id": item.id,
                        "circuit_cleared": circuit_cleared,
                    })
                except Exception:  # noqa: BLE001 - canary result remains successful
                    log.exception("failed to clear runtime circuit after canary")
        elif status == "blocked" and operator_question:
            from ...core.operator_decision import build_operator_decision

            decision_root = self.memory.root
            evidence = list(getattr(item, "context_refs", None) or [])
            decision_card = build_operator_decision(
                item_id=item.id,
                title=item.title,
                reason=str(getattr(outcome, "final_review_reason", "") or err),
                question=operator_question,
                options=list(getattr(outcome, "operator_options", []) or []),
                evidence=evidence,
                project_id=decision_root.name,
            )
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
                operator_decision=decision_card,
            )
            # The mission is now parked on the operator. Nobody watches a
            # long-running daemon's portal, so tell them where they already
            # are — otherwise the wait lasts until someone happens to look.
            item.pending_question = operator_question
            item.operator_decision = decision_card
            notify_pending_question(self.memory.root, item)
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
        elif replan_requested and bool(
            (getattr(outcome, "final_planner_report", None) or {}).get(
                "forward_progress"
            )
        ):
            self.memory.backlog.mark_failed(
                item.id,
                error=state.stop_reason or "completed work requires a replacement plan",
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
            if forbid_operator_parking:
                self.memory.backlog.update(
                    item.id,
                    status="failed",
                    finished_ts=time.time(),
                    last_error=err,
                    outcome=outcome_dimensions,
                    pending_question="",
                    operator_decision={},
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
        state.iteration = iteration
        state.iteration_requeued = iteration_requeued

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

        self._evolve_runtime_skills_after_mission(
            success=bool(success and not state.iteration_requeued),
            usage_mission_id=state.usage_attempt_id,
            mission_objective=str(
                item.original_objective or item.objective or item.title or ""
            ),
            mission_result=(
                f"status={status}; stop_kind={state.stop_kind or 'none'}; "
                f"reason={state.stop_reason or 'none'}"
            ),
        )
        state.usage_summary = state.cost_sink.usage_summary()
        state.usd = state.usage_summary.cost_usd
        state.known_usd = state.usage_summary.known_cost_usd

        kind = (
            "mission_iterated"
            if state.iteration_requeued
            else "mission_complete"
            if success
            else "mission_replan_requested"
            if state.replan_requested
            else "mission_aborted"
            if state.intentional_abort
            else "mission_failed"
        )
        final_submission_certified = bool(
            kind == "mission_complete"
            and state.iteration is None
            and state.item_scope == PLANNER_SCOPE_FINAL_SUBMISSION
            and getattr(outcome, "final_submission_certified", False)
        )
        final_submission_signature = (
            self._final_submission_signature()
            if final_submission_certified
            else ""
        )
        try:
            remaining_work = any(
                row.id != item.id
                and row.status
                in {
                    "pending",
                    "running",
                    "paused",
                    "paused_budget",
                    "paused_provider_cooldown",
                    "paused_provider_fence",
                    "paused_operator",
                }
                for row in self.memory.backlog.all()
            )
        except Exception:  # noqa: BLE001 - completion presentation fails closed
            remaining_work = True
        overall_complete = bool(
            success
            and state.iteration is None
            and (
                final_submission_certified
                or (not self.config.continuous and not remaining_work)
            )
        )
        campaign_continues = bool(success and not overall_complete)

        self._update_no_progress_streak(
            kind=kind,
            report=getattr(outcome, "final_planner_report", {}) or {},
        )

        planner_report = dict(
            getattr(outcome, "final_planner_report", {}) or {}
        )
        plan_challenge = dict(getattr(outcome, "plan_challenge", {}) or {})
        mission_summary = " ".join(
            str(
                getattr(outcome, "summary", "")
                or getattr(outcome, "final_message", "")
                or getattr(outcome, "final_review_reason", "")
                or getattr(outcome, "reason", "")
                or planner_report.get("summary")
                or ""
            ).split()
        )[:1200]
        # A completed mission needs one durable, operator-facing receipt rather
        # than three loosely related hints (event, chat text, and sidebar).
        # Resolve targets only from the final Reviewer evidence, vertical
        # contract, or Manager-owned live view; never scan or guess workspace
        # files at settlement time.
        delivery: dict[str, Any] | None = None
        frontier = getattr(outcome, "final_frontier_report", {}) or {}
        reviewer_artifacts = (
            list(frontier.get("artifacts") or [])
            if isinstance(frontier, dict)
            else []
        )
        try:
            from ..delivery import build_delivery_receipt

            delivery = build_delivery_receipt(
                item_id=item.id,
                title=item.title,
                summary=mission_summary,
                success=bool(success),
                overall_complete=overall_complete,
                status=status,
                review_status=str(
                    getattr(outcome, "final_review_status", "") or ""
                ),
                final_submission_certified=final_submission_certified,
                workspace=(state.execution_workdir or self._project_workdir()),
                state_root=self.memory.root,
                stage=state.pipeline_stage_at_start,
                reviewer_artifacts=reviewer_artifacts,
            )
        except Exception:  # noqa: BLE001 - delivery presentation never owns settlement
            log.debug("mission delivery receipt could not be built", exc_info=True)
        try:
            from ...core.metrics import metrics_root_for_project, record_metric

            forward_progress = planner_report.get("forward_progress")
            if not isinstance(forward_progress, bool) and success and status == "done":
                forward_progress = True
            record_metric(
                metrics_root_for_project(self.memory.root),
                "goal.mission",
                labels={"status": status},
                fields={
                    "project_id": self.memory.root.name,
                    "item_id": item.id,
                    "accepted": bool(success),
                    "forward_progress": (
                        forward_progress
                        if isinstance(forward_progress, bool)
                        else None
                    ),
                    "replan_requested": bool(state.replan_requested),
                    "plan_signal": str(planner_report.get("plan_signal") or ""),
                    "elapsed_seconds": float(state.elapsed or 0.0),
                },
            )
        except Exception:  # noqa: BLE001 - metrics never own settlement
            log.debug("goal mission metric skipped", exc_info=True)
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
            "summary": mission_summary,
            "execution_workdir": str(state.execution_workdir),
            "delivery_candidates": [
                str(candidate)
                for candidate in reviewer_artifacts
                if str(candidate).strip()
            ][:12],
            "outcome_class": mission_outcome_class(status=status, success=success),
            "outcome": state.outcome_dimensions,
            "planner_report": planner_report,
            "plan_challenge": plan_challenge,
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
            "operator_question": str(
                getattr(outcome, "operator_question", "") or ""
            ).strip(),
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
            "overall_complete": overall_complete,
            "campaign_continues": campaign_continues,
            "delivery": delivery,
            "delivery_id": str((delivery or {}).get("delivery_id") or ""),
            "research_result": getattr(outcome, "research_result", None),
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
            "iteration": state.iteration,
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
            "iteration": state.iteration,
            "auth_failure": state.auth_failure,
            "review_reason": str(
                getattr(outcome, "final_review_reason", "")
                or getattr(outcome, "reason", "")
                or ""
            ),
            "summary": mission_summary,
            "overall_complete": overall_complete,
            "campaign_continues": campaign_continues,
            "delivery": delivery,
            "planner_report": planner_report,
            "plan_challenge": plan_challenge,
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
