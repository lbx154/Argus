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
import subprocess
import time
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...core.event_catalog import EventType
from ...core.mission_view import operator_objective, say
from ...core.runner_errors import is_execution_host_startup_error
from ...core.stop_kinds import stop_kind_is_recoverable
from ..memory import BacklogItem
from ..mission_outcome import (
    mission_outcome_class,
    mission_outcome_dimensions,
    review_keeps_mission_resumable,
)

if TYPE_CHECKING:
    from ._config import _MemoryView, _MissionRunner

from ._constants import (
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
)
from ._mission_execution_helpers import _MissionRunState
from .pending_notify import ask_operator

log = logging.getLogger(__name__)


def outcome_manuscript_binding(outcome: object) -> dict[str, Any] | None:
    """The manuscript the final Reviewer read, from whichever outcome shape we got.

    The daemon's ``_Outcome`` carries it as ``manuscript_snapshot`` (its
    ``rounds`` is a count); in-process outcomes carry a list of round records
    whose last review holds it.
    """
    direct = getattr(outcome, "manuscript_snapshot", None)
    if isinstance(direct, dict) and direct.get("facts"):
        return dict(direct)
    rounds = getattr(outcome, "rounds", None) or []
    if isinstance(rounds, (list, tuple)) and rounds:
        final_review = getattr(rounds[-1], "review", None)
        candidate = getattr(final_review, "manuscript_snapshot", None)
        if isinstance(candidate, dict) and candidate.get("facts"):
            return dict(candidate)
    return None


class MissionExecutionSettlementMixin:
    """Repair settlement, stage guard, final status, and journal emission."""

    _confirm_mission_completion_receipt: Callable[[dict[str, Any]], bool]

    if TYPE_CHECKING:
        # Provided by LifeSupervisor (_core.py); declared so the type checker
        # knows the mixin's calls resolve there. Same shapes as the sibling
        # mixins, so the composed class sees one definition.
        memory: _MemoryView
        runner: _MissionRunner
        _emit: Callable[[dict[str, Any]], bool]

        def _project_workdir(self) -> Path: ...

        def _budget_global_root(self) -> Path: ...

        def _operator_chinese(self) -> bool: ...

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
                    "restricted validator repair was declined"
                    + (": " + "; ".join(guard_errors) if guard_errors else "")
                )
        elif state.repair_capability is not None and state.repair_store is not None:
            repair_identity = state.repair_identity
            assert repair_identity is not None, "claimed repair requires its campaign identity"
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
                    identity=repair_identity,
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
                    "restricted validator repair was declined"
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
                    for sibling in self.memory.backlog.active()
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
            assert policy_root is not None, "stage settlement requires prepared vertical root"
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
            review_manuscript_binding = outcome_manuscript_binding(outcome)
            try:
                from ...core.stage_certificate import record_stage_review

                record_stage_review(
                    state_root=self.memory.root,
                    project_root=Path(state.execution_workdir or self._project_workdir()),
                    stage=state.pipeline_stage_at_start,
                    item=item,
                    manager_action=stage_action or "hold",
                    manager_reason=(
                        str(stage_transition.get("reason") or "")
                        if isinstance(stage_transition, dict)
                        else ""
                    ),
                    manuscript_binding=review_manuscript_binding,
                    contract_root=state.vertical_root,
                    venue_review=getattr(outcome, "venue_review", None),
                    venue_review_snapshot=getattr(outcome, "venue_review_snapshot", None),
                )
            except Exception:  # noqa: BLE001 - certificate is observability/control aid
                log.exception("life supervisor: failed to record stage review certificate")

    def _settle_stage_transition(
        self, state: _MissionRunState,
    ) -> None:
        """Classify stage continuation before durable settlement and publication.

        A Manager-certified intermediate stage transition keeps the same
        bounded item pending so it continues automatically. Failures and
        parks do not enter this path.
        """
        outcome = state.outcome
        stage_transition = state.stage_transition
        stage_action = state.stage_action
        success = state.success

        staged_item_continues = (
            success
            and not self.config.continuous
            and not state.planner_bounded_node
            and isinstance(stage_transition, dict)
            and bool(stage_transition)
            and stage_action in {"advance", "rollback"}
        )
        if staged_item_continues:
            state.success = True
            state.status = "stage_continues"
            state.stop_reason = str(stage_transition.get("reason") or "")

        bounded_stage_hold = (
            success
            and not self.config.continuous
            and not state.planner_bounded_node
            and isinstance(stage_transition, dict)
            and bool(stage_transition)
            and stage_action == "hold"
        )
        if bounded_stage_hold:
            state.success = False
            state.stop_reason = str(
                stage_transition.get("reason")
                or "Manager held the current stage"
            )
            # Only an explicit model decision means "stay and keep working".
            # Parser/backend failures also return HOLD and must not busy-loop.
            if str(getattr(outcome, "operator_question", "") or "").strip():
                state.status = "blocked"
            elif stage_transition.get("diagnostic") == "intentional_hold":
                state.status = "stage_continues"
            else:
                state.status = "stage_hold"

    # ------------------------------------------------------------------
    # Phase: final status resolution against the backlog
    # ------------------------------------------------------------------

    def _maybe_requeue_chartered_shortfall(
        self,
        state: _MissionRunState,
    ) -> dict[str, Any] | None:
        """Intercept a trusted, vertical-declared shortfall before ``done``.

        A positive persisted ``iteration_max_cycles`` is an explicit iteration
        budget. Zero leaves the task open-ended; the host-global daily dollar
        cap remains the monetary admission guard.

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
            from ...verticals._base import load_vertical_contract

            vertical_root = state.vertical_root
            assert vertical_root is not None, "iteration requires prepared vertical root"
            contract = load_vertical_contract(
                resolve_vertical(vertical_root),
                project_root=vertical_root,
            )
            assessment = contract.assess_iteration(
                stage=state.pipeline_stage_at_start,
                scope=state.item_scope,
                project_root=Path(
                    state.execution_workdir or self._project_workdir()
                ),
                state_root=vertical_root,
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
        if cycles_max > 0 and cycles_done >= cycles_max:
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

    def _finalize_mission_status(self, state: _MissionRunState) -> None:
        """Resolve final status and persist the backlog outcome/operator question.

        This consumes the already-settled repair, stage, and iteration decisions.
        The resulting shared fields feed publication; local classification flags
        do not escape this phase. The mission completion event is emitted later.
        """
        item = state.item
        outcome = state.outcome
        status = state.status
        success = state.success
        stage_transition = state.stage_transition
        stage_action = state.stage_action

        operator_question = str(
            getattr(outcome, "operator_question", "") or ""
        ).strip()
        # A park the loop's own pause path left here: the Manager stopped the
        # run, or its host could not start.
        parked = status == "parked"
        replan_requested = status == "replan_requested"
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
            parked
            or stop_kind_is_recoverable(state.stop_kind)
            # A stall the Reviewer answered with ``continue`` is unfinished
            # work, not a dead task: it stays resumable.
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
            park_reason=state.park_reason,
        )
        if iteration is not None:
            outcome_dimensions["iteration"] = dict(iteration)
        if state.park_reason == "infra" and is_execution_host_startup_error(state.stop_reason):
            # The shortcircuit owns this structured runner diagnostic; it stays
            # with the park until an explicit resume re-arms the same metered
            # attempt path.
            outcome_dimensions["execution_host_failure"] = state.stop_reason
            outcome_dimensions["review_status"] = "not_assessed"

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

        if (
            success
            and not iteration_requeued
            and final_review_status.strip().lower() == "done"
            and str(
                getattr(outcome, "final_review_source", "") or ""
            ).strip().lower() == "reviewer"
        ):
            self._share_reviewed_wiki_pages(state, manager_decision)

        maintenance_reviewed = bool(
            "framework_maintenance" in state.item_tags
            and success
            and status != "stage_continues"
            and not iteration_requeued
            and final_review_status.strip().lower() == "done"
            and str(
                getattr(outcome, "final_review_source", "") or ""
            ).strip().lower() == "reviewer"
        )
        maintenance_candidate = ""
        if maintenance_reviewed:
            try:
                maintenance_candidate = self._freeze_reviewed_maintenance_change(
                    state
                )
            except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
                maintenance_reviewed = False
                success = False
                status = "error"
                resumable = False
                err = f"maintenance change could not be frozen: {exc}"
                state.stop_reason = err
                outcome_dimensions = mission_outcome_dimensions(
                    status=status,
                    success=False,
                    review_status=final_review_status,
                    stage_transition=stage_transition,
                    stop_kind=state.stop_kind,
                    resumable=False,
                )

        def capture_state() -> None:
            state.success = success
            state.status = status
            state.replan_requested = replan_requested
            state.intentional_abort = intentional_abort
            state.err = err
            state.resumable = resumable
            state.outcome_dimensions = outcome_dimensions
            state.iteration = iteration
            state.iteration_requeued = iteration_requeued

        def commit(**updates: Any) -> None:
            from ..mission_delivery import prepare_mission_delivery

            capture_state()
            event, result = self._build_mission_completion(state)
            try:
                experience = self._build_settled_experience(state)
                capsule = experience.to_jsonable() if experience is not None else None
            except (TypeError, ValueError):
                log.exception("settled observation could not be frozen; completion remains independent")
                capsule = {"capture_error": "settled evidence could not be frozen"}
            record = prepare_mission_delivery(
                item=item, event=event, result=result,
                settled_experience=capsule,
            )
            settled = self.memory.backlog.update(item.id, _mission_delivery=record, **updates)
            if settled is None:
                raise RuntimeError("mission disappeared before completion commit")
            state.completion_delivery = record

        def fail(*, error: str, outcome: dict[str, Any]) -> None:
            commit(status="failed", finished_ts=time.time(), last_error=error, outcome=outcome)

        def parked_outcome(reason: str, *, succeeded: bool) -> dict[str, Any]:
            """Settle the run as parked for ``reason``; the outcome the park records."""
            nonlocal status, resumable
            status, state.park_reason, resumable = "parked", reason, True
            return mission_outcome_dimensions(
                status=status, success=succeeded, review_status=final_review_status,
                stage_transition=stage_transition, stop_kind=state.stop_kind,
                resumable=True, park_reason=reason,
            )

        from ...manager.supervision import HANDED_TO_MANAGER

        # Update backlog row. A bounded research cycle that did not achieve its
        # persisted success target is resumable, not a success or terminal failure.
        if status == "stage_continues":
            outcome_dimensions["stage_continuation"] = dict(stage_transition)
            commit(
                status="pending", started_ts=None, finished_ts=None,
                last_error=state.stop_reason, outcome=outcome_dimensions,
            )
        elif success and iteration_requeued:
            # ``requeue_for_iteration`` already performed the only backlog
            # transition allowed here: running -> pending on the same item.
            commit(outcome=outcome_dimensions)
        elif maintenance_reviewed:
            outcome_dimensions = parked_outcome("operator_question", succeeded=True)
            ask_operator(
                self.memory.root, item,
                say("ask_adopt_reviewed_change", self._operator_chinese(), title=item.title),
                reason=str(
                    getattr(outcome, "final_review_reason", "")
                    or "The Reviewer read the maintenance change and it holds."
                ),
                layer="manager",
                save=partial(commit, finished_ts=time.time(), last_error="", outcome=outcome_dimensions),
                emit=self._emit,
                options=[
                    {
                        "id": "adopt",
                        "label": "Adopt reviewed change",
                        "description": (
                            "Publish the reviewed commit to origin/main and switch "
                            "the runtime at the next task boundary."
                        ),
                    },
                    {
                        "id": "decline",
                        "label": "Decline deployment",
                        "description": (
                            "Keep the current runtime and discard the reviewed change."
                        ),
                    },
                ],
                evidence=list(item.context_refs),
                title=f"Adopt reviewed change: {item.title}",
                decision_kind="framework_deployment",
                reviewed_candidate=maintenance_candidate,
            )
        elif success:
            commit(status="done", finished_ts=time.time(), outcome=outcome_dimensions)
        elif status == "blocked" and operator_question:
            # Status and the authority-bearing question reach disk in one
            # backlog transaction. The row stays nonterminal so dependency
            # reconciliation cannot cascade-skip its downstream plan while the
            # operator is deciding; their answer resumes this same row.
            ask_operator(
                self.memory.root, item, operator_question,
                reason=str(getattr(outcome, "final_review_reason", "") or err), layer="manager",
                save=partial(commit, finished_ts=time.time(), last_error=err, outcome=outcome_dimensions),
                emit=self._emit,
                options=list(getattr(outcome, "operator_options", []) or []),
                evidence=list(getattr(item, "context_refs", None) or []),
            )
        elif status == "blocked" and str(
            getattr(outcome, "final_review_source", "") or ""
        ) in HANDED_TO_MANAGER:
            # Handed to the Manager, who holds it now: it parks where a Manager
            # stop leaves work, for the Manager to put to the operator, the
            # Planner to retire or the operator to resume.
            outcome_dimensions = parked_outcome("research_open", succeeded=False)
            commit(
                park_reason=state.park_reason,
                finished_ts=time.time(),
                last_error=err,
                outcome=outcome_dimensions,
            )
        elif stage_reconciled_replan:
            fail(
                error=(
                    f"manager {stage_action} to "
                    f"{stage_transition.get('target_stage') or 'another stage'} "
                    "after Reviewer identified an upstream stage defect"
                ),
                outcome=outcome_dimensions,
            )
        elif replan_requested:
            commit(
                status="pending",
                started_ts=None,
                finished_ts=None,
                last_error=state.stop_reason,
            )
        elif parked:
            commit(
                park_reason=state.park_reason,
                finished_ts=time.time(),
                last_error=state.stop_reason,
                outcome=outcome_dimensions,
            )
        elif intentional_abort:
            commit(
                status="aborted",
                finished_ts=time.time(),
                last_error=state.stop_reason,
                outcome=outcome_dimensions,
            )
        else:
            fail(error=err, outcome=outcome_dimensions)

        # A canary that ran without a stop reached the model service, which
        # ends a provider outage; a failure in Argus's own code needs its
        # reviewed pass.
        answered = not state.exc_str and state.stop_kind is None
        if "runtime_failure_canary" in state.item_tags and (success or answered):
            circuit_cleared = self._close_runtime_failure_circuit(
                reason=(
                    f"runtime canary {'passed review' if success else 'reached the model service'}"
                    f" in mission {item.id}"
                ),
                canary_item_id=item.id,
                outage_only=not success,
            )
            if success or circuit_cleared:
                self._emit({
                    "type": EventType.LIFE_RUNTIME_FAILURE_CANARY_PASSED,
                    "item_id": item.id,
                    "circuit_cleared": circuit_cleared,
                })

        capture_state()

    # ------------------------------------------------------------------
    # Phase: journal event + return dict
    # ------------------------------------------------------------------

    def _build_mission_completion(
        self, state: _MissionRunState,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the completion event and durable return value before committing.

        This phase only reads evidence and usage. Models, notifications, metric
        writes, and learning run after the completion envelope is durable.
        """
        item = state.item
        outcome = state.outcome
        cost_sink = state.cost_sink
        assert cost_sink is not None, "outcome publication requires prepared cost sink"
        success = state.success
        status = state.status

        state.usage_summary, cost_breakdown = cost_sink.completion_usage()
        state.usd = state.usage_summary.cost_usd
        state.known_usd = state.usage_summary.known_cost_usd

        kind = (
            "mission_iterated"
            if state.iteration_requeued
            else "mission_complete"
            if success
            else "mission_replan_requested"
            if state.replan_requested or status == "stage_continues"
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
        final_submission_manuscript_snapshot: dict[str, str] | None = (
            outcome_manuscript_binding(outcome) if final_submission_certified else None
        )
        try:
            remaining_work = any(
                row.id != item.id
                and row.status in {"pending", "running", "parked"}
                for row in self.memory.backlog.active()
            )
        except Exception:  # noqa: BLE001 - completion presentation fails closed
            remaining_work = True
        overall_complete = bool(
            success
            and status != "stage_continues"
            and status != "parked"
            and state.iteration is None
            and (
                final_submission_certified
                or (
                    not self.config.open_ended
                    and "manager_direct" in state.item_tags
                    and state.stage_action == "complete"
                )
                or (not self.config.continuous and not remaining_work)
            )
        )
        campaign_continues = bool(status == "stage_continues" or (success and not overall_complete))

        plan_challenge = dict(getattr(outcome, "plan_challenge", {}) or {})
        # Work an independent Reviewer accepted is reported in the Reviewer's
        # words: the Engineer's handoff predates that review.
        accepted_review = (
            getattr(outcome, "final_review_reason", "")
            if success and getattr(outcome, "final_review_source", "") == "reviewer"
            else ""
        )
        raw_mission_summary = str(
            accepted_review
            or getattr(outcome, "summary", "")
            or getattr(outcome, "final_review_reason", "")
            or getattr(outcome, "final_message", "")
            or getattr(outcome, "reason", "")
            or ""
        )
        from ...core.role_handoff import strip_handoff

        raw_mission_summary = strip_handoff(raw_mission_summary)
        mission_summary = " ".join(raw_mission_summary.split())[:1200]
        residual_risk = (
            " ".join(str(getattr(outcome, "final_residual_risk", "") or "").split())[:600]
            if success else ""
        )
        if residual_risk:
            # Approved with one check left unverified on an accepted risk: the
            # summary every surface shows says so before anything else.
            mission_summary = f"Verified with residual risk: {residual_risk}. {mission_summary}"[:1200].strip()
        final_output = str(getattr(outcome, "final_output", "") or "").strip()
        # A completed mission needs one durable, operator-facing receipt rather
        # than three loosely related hints (event, chat text, and sidebar).
        # Resolve targets only from the accepted completion message or a
        # vertical contract; never scan or guess files. Files the Engineer
        # handoff names are reviewer-accepted evidence and must remain openable
        # in the UI.
        delivery: dict[str, Any] | None = None
        delivery_workspace = state.execution_workdir or self._project_workdir()
        reviewer_artifacts: list[str] = []
        try:
            from ..delivery import referenced_delivery_paths, reviewed_change_paths

            reviewer_artifacts.extend(referenced_delivery_paths(
                delivery_workspace,
                [
                    raw_mission_summary,
                    final_output,
                    getattr(outcome, "final_message", ""),
                ],
                limit=12,
            ))
            if success and not reviewer_artifacts:
                # A terse accepted direct handoff may omit every filename.
                # Recover only authored files actually inspected by its final
                # independent Reviewer, not arbitrary workspace contents.
                reviewer_artifacts.extend(reviewed_change_paths(
                    delivery_workspace, self.memory.root, item.id,
                ))
        except Exception:  # noqa: BLE001 - receipt construction remains fail-soft
            log.debug("completion file links could not be resolved", exc_info=True)
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
                review_source=str(
                    getattr(outcome, "final_review_source", "") or ""
                ),
                residual_risk=residual_risk,
                workspace=delivery_workspace,
                state_root=self._project_state_root(),
                stage=state.pipeline_stage_at_start,
                reviewer_artifacts=reviewer_artifacts,
                stage_package="stage_package" in item.tags,
            )
            if delivery is not None and final_submission_manuscript_snapshot is not None:
                delivery["manuscript_snapshot"] = final_submission_manuscript_snapshot
        except Exception:  # noqa: BLE001 - delivery presentation never owns settlement
            log.debug("mission delivery receipt could not be built", exc_info=True)
        event = {
            "type": EventType.LIFE_MISSION_COMPLETED,
            "item_id": item.id,
            "title": item.title,
            "objective": operator_objective(vars(item)),
            "scope": state.item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
            "success": success,
            "status": status,
            **({"park_reason": state.park_reason} if status == "parked" else {}),
            "summary": mission_summary,
            "residual_risk": residual_risk,
            "final_output": final_output,
            "execution_workdir": str(state.execution_workdir),
            "review_source": str(
                getattr(outcome, "final_review_source", "") or ""
            ),
            "delivery_candidates": [
                str(candidate)
                for candidate in reviewer_artifacts
                if str(candidate).strip()
            ][:12],
            "outcome_class": mission_outcome_class(status, success, state.park_reason),
            "outcome": state.outcome_dimensions,
            "plan_challenge": plan_challenge,
            "plan_revision_witness": (
                dict(state.plan_revision_witness) if state.replan_requested else {}
            ),
            "rounds": state.rounds,
            "elapsed_seconds": state.elapsed,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "usage_phase": "execution_completed",
            "usage_record_count": state.usage_summary.call_count,
            "partial_usage_records": state.usage_summary.partial_calls,
            "unpriced_usage_records": state.usage_summary.unpriced_calls,
            "terminal_status": status if kind == "mission_failed" else "",
            "resumable": state.resumable,
            "recoverable": bool(
                getattr(outcome, "recoverable", False)
                or stop_kind_is_recoverable(state.stop_kind)
            ),
            "stop_kind": state.stop_kind,
            "stop_reason": (
                state.stop_reason or state.err
                if kind in {"mission_failed", "mission_aborted"} or status == "stage_continues"
                else ""
            ),
            "failure_reason": state.err if kind == "mission_failed" else "",
            "operator_question": str(
                getattr(outcome, "operator_question", "") or ""
            ).strip(),
            "next_action": "" if success else str(
                getattr(outcome, "final_review_next_action", "") or ""
            ).strip()[:1200],
            "agent_layer": "engineer",
            "engineer_model": self.engineer_model,
            "reviewer_model": self.reviewer_model,
            **cost_breakdown,
            "copilot_premium_requests": state.usage_summary.premium_requests,
            "input_tokens": state.usage_summary.input_tokens,
            "cached_input_tokens": state.usage_summary.cached_input_tokens,
            "cache_write_tokens": state.usage_summary.cache_write_tokens,
            "output_tokens": state.usage_summary.output_tokens,
            "reasoning_output_tokens": state.usage_summary.reasoning_output_tokens,
            "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
            "final_submission_certified": final_submission_certified,
            "final_submission_signature": final_submission_signature,
            "manuscript_snapshot": final_submission_manuscript_snapshot,
            "venue_review": getattr(outcome, "venue_review", None),
            "venue_review_snapshot": getattr(outcome, "venue_review_snapshot", None),
            "overall_complete": overall_complete,
            "campaign_continues": campaign_continues,
            "stage_transition": state.stage_transition,
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
        }

        result = {
            "item_id": item.id,
            "title": item.title,
            "tags": list(item.tags),
            "execution_workdir": str(state.execution_workdir),
            "success": success,
            "status": status,
            "stage_transition": state.stage_transition,
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
            "plan_challenge": plan_challenge,
            "plan_revision_witness": (
                dict(state.plan_revision_witness) if state.replan_requested else {}
            ),
            "expected_plan_id": item.plan_id,
            "expected_plan_version": item.plan_version,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        }
        return event, result

    def _emit_mission_outcome_and_build_result(
        self, state: _MissionRunState,
    ) -> dict[str, Any]:
        """Deliver the committed completion before optional post-mission work."""
        from ..mission_delivery import drain_mission_deliveries

        record = state.completion_delivery
        assert record is not None, "mission completion must be committed before publication"
        result = dict(record["result"])
        if not drain_mission_deliveries(
            self.memory.backlog, self._emit, self._confirm_mission_completion_receipt,
        ):
            return result
        stop_event = getattr(getattr(self, "config", None), "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            return result
        item = state.item
        outcome = state.outcome
        success = state.success
        status = state.status
        planner_report = dict(getattr(outcome, "final_planner_report", {}) or {})
        try:
            from ...core.metrics import metrics_root_for_project, record_metric

            record_metric(
                metrics_root_for_project(self.memory.root),
                "goal.mission",
                labels={"status": status},
                fields={
                    "project_id": self.memory.root.name,
                    "item_id": item.id,
                    "accepted": bool(success),
                    "replan_requested": bool(state.replan_requested),
                    "plan_signal": str(planner_report.get("plan_signal") or ""),
                    "elapsed_seconds": float(state.elapsed or 0.0),
                },
            )
        except Exception:  # noqa: BLE001 - metrics never own settlement
            log.debug("goal mission metric skipped", exc_info=True)
        try:
            research_result = getattr(outcome, "research_result", None)
            reviewed_evidence = list(
                research_result.get("evidence") or []
                if isinstance(research_result, dict)
                else []
            )
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
                reviewer_source=str(getattr(outcome, "final_review_source", "") or ""),
                reviewer_reason=str(getattr(outcome, "final_review_reason", "") or ""),
                research_result=research_result,
                evidence_refs=tuple(
                    str(ref).strip() for ref in reviewed_evidence if str(ref).strip()
                ),
                source_campaign=str(state.execution_workdir or self._project_workdir()),
            )
        except Exception:
            log.exception("post-mission learning failed after durable completion")
        try:
            self._reflect_after_mission(state)
        except Exception:
            log.exception("reflection after the mission failed; the result stands")
        cost_sink = state.cost_sink
        assert cost_sink is not None, "mission completion requires a prepared cost sink"
        usage = cost_sink.usage_summary()
        result.update(
            cost_usd=usage.cost_usd, known_cost_usd=usage.known_cost_usd,
            pricing_status=usage.pricing_status,
        )
        # The call ledger remains authoritative for post-completion learning.
        # Keep the durable return receipt aligned with the caller's final totals.
        if result != record["result"]:
            try:
                self.memory.backlog.update(item.id, mission_result=result)
            except Exception:
                log.exception("post-mission usage receipt deferred; call ledger remains authoritative")
        return result

    def _reflect_after_mission(self, state: _MissionRunState) -> dict[str, Any]:
        """Look back once on the finished mission and keep what it taught.

        Runs after the completion is durable and after Skill propagation. The
        reflection module decides whether anything is worth writing; this hook
        only gathers the facts it needs and hands over the project's event
        sink. Nothing here changes the mission result.
        """
        from ..reflection import reflect_after_mission
        from ._evolution import _project_state_root

        item, outcome = state.item, state.outcome
        reviewed_fact = getattr(self, "_pending_reviewed_fact", None)
        self._pending_reviewed_fact = None
        review_status = str(getattr(outcome, "final_review_status", "") or "")
        final_report = getattr(outcome, "final_planner_report", {}) or {}
        workspace = Path(state.execution_workdir or self._project_workdir())
        manager_decision = getattr(item, "manager_decision", {}) or {}
        vertical = (
            str(manager_decision.get("vertical") or "").strip()
            if isinstance(manager_decision, dict)
            else ""
        )
        life_dir = _project_state_root(self.memory) or workspace
        if not vertical:
            from ...skills.vertical_select import resolve_project_vertical

            try:
                vertical = str(resolve_project_vertical(workspace, life_dir=life_dir) or "")
            except Exception:  # noqa: BLE001 - an undecided vertical keeps the lesson global
                vertical = ""
        return reflect_after_mission(
            runner=self.runner,
            workspace=workspace,
            life_dir=life_dir,
            global_root=self._budget_global_root(),
            vertical=vertical,
            project_id=life_dir.name or workspace.name,
            mission_id=str(item.id),
            title=str(item.title or ""),
            objective=str(item.original_objective or item.objective or ""),
            acceptance=str(getattr(item, "acceptance_check", "") or ""),
            review_status=review_status,
            review_reason=str(getattr(outcome, "final_review_reason", "") or ""),
            stop_reason=(
                f"status={state.status}; stop_kind={state.stop_kind or 'none'}; "
                f"reason={state.stop_reason or 'none'}"
            ),
            # The round text the Reviewer read is not kept on the outcome; the
            # engineer's own account of the run is the factual record we have.
            host_round_log="",
            run_reality=str(
                getattr(outcome, "summary", "") or getattr(outcome, "final_message", "") or ""
            ),
            emit=self._emit,
            elapsed_s=float(state.elapsed or 0.0),
            rounds=int(state.rounds or 0),
            reviewed_fact=reviewed_fact,
            # Only the Reviewer's own verdict carries its learning judgment.
            learning=(
                str(final_report.get("learning") or "")
                if isinstance(final_report, dict)
                and str(getattr(outcome, "final_review_source", "") or "").strip().lower() == "reviewer"
                else ""
            ),
            mission_accepted=bool(
                state.success and not state.iteration_requeued and not state.replan_requested
            ),
        )

    def _build_settled_experience(self, state: _MissionRunState) -> Any:
        """Freeze conservative evidence before the completion commit; no I/O."""
        if state.intentional_abort:
            return None
        from ..failure_experience import experience_from_settled_mission

        item, outcome = state.item, state.outcome
        refs = [
            str(ref.get("path") or ref.get("ref") or "").strip()
            for ref in (getattr(item, "context_refs", []) or [])
            if isinstance(ref, dict)
        ]
        if state.context_packet_path is not None:
            refs.append(str(state.context_packet_path.parent / "latest.json"))
        review_status = str(getattr(outcome, "final_review_status", "") or "").strip().lower()
        status = state.status
        success = bool(
            state.success and status in {"done", "success", "completed"}
            and state.iteration is None and not state.resumable
            and review_status in {"", "done"}
        )
        if state.iteration is not None:
            status = "iteration_" + str(state.iteration.get("status") or "incomplete")
        elif not success and status in {"done", "success", "completed"}:
            status = "review_" + review_status if review_status and review_status != "done" else "not_accepted"
        final_message = str(getattr(outcome, "final_message", "") or "")
        review_reason = str(getattr(outcome, "final_review_reason", "") or getattr(outcome, "reason", "") or "")
        factual_outcome = (
            state.stop_reason or final_message or review_reason or status
            if success else str((state.iteration or {}).get("stop_reason") or state.stop_reason or state.err or status)
        )
        return experience_from_settled_mission(
            mission_id=item.id,
            attempt_id=state.usage_attempt_id or str(getattr(item, "started_ts", None) or item.ts),
            created_at=float(getattr(item, "started_ts", None) or item.ts),
            title=item.title, objective=item.objective, status=status, success=success,
            reviewer_source=str(getattr(outcome, "final_review_source", "") or ""),
            factual_outcome=factual_outcome, final_message=final_message, review_reason=review_reason,
            stop_kind=str(state.stop_kind or ""),
            concepts=list(item.tags), artifact_refs=[ref for ref in refs if ref],
            non_goals=list(getattr(item, "non_goals", []) or []),
        )

    def _capture_failure_experience(self, state: _MissionRunState) -> None:
        """Compatibility helper; durable runtime capture uses its committed envelope."""
        store = getattr(self.memory, "failure_experiences", None)
        if store is None:
            return
        try:
            experience = MissionExecutionSettlementMixin._build_settled_experience(self, state)
            if experience is not None:
                store.record_settled(experience)
        except (OSError, TypeError, ValueError):
            log.exception("life supervisor: failed to persist settled experience")

    def _share_reviewed_wiki_pages(
        self,
        state: _MissionRunState,
        manager_decision: Any,
    ) -> None:
        """Copy audience-tagged project Wiki pages into the shared knowledge roots.

        Runs only after a mission the Reviewer accepted. Nothing here can fail
        the mission: every filesystem error is logged and swallowed.
        """
        from ...core import paths as core_paths
        from ...wiki.promote import promote_wiki_pages

        try:
            workspace = Path(state.execution_workdir or self._project_workdir())
            vertical = ""
            if isinstance(manager_decision, dict):
                vertical = str(manager_decision.get("vertical") or "").strip()
            if not vertical:
                from ...skills.vertical_select import resolve_project_vertical
                from ._evolution import _project_state_root

                try:
                    vertical = resolve_project_vertical(
                        workspace, life_dir=_project_state_root(self.memory) or workspace,
                    )
                except Exception:  # noqa: BLE001 - an undecided vertical only skips the vertical tier
                    vertical = ""
            promoted = promote_wiki_pages(
                workspace,
                vertical=vertical,
                shared_root=core_paths.shared_wiki_root(self._budget_global_root()),
            )
        except OSError:
            log.exception("life supervisor: shared knowledge could not be updated")
            return
        for target, pages in promoted.items():
            log.info(
                "life supervisor: %d wiki page(s) shared with the %s knowledge: %s",
                len(pages), target, ", ".join(pages),
            )


__all__ = ["MissionExecutionSettlementMixin"]
