"""One continuous-planner cycle: decide vertical, plan, dedupe, enqueue."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
)
from ._planner_failure_backoff import PlannerFailureBackoffMixin, planner_failure_kind
from ._planning_cycle_completion import PlanningCycleCompletionMixin
from ._planning_cycle_enqueue import PlanningCycleEnqueueMixin
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _render_revision_request,
    _research_project_done_issue,
    _revision_reason,
)
from ._planning_cycle_intake import PlanningCycleIntakeMixin
from ._planning_cycle_verdict import PlanningCycleVerdictMixin

log = logging.getLogger(__name__)



class PlanningCycleMixin(
    PlanningCycleIntakeMixin,
    PlanningCycleVerdictMixin,
    PlanningCycleCompletionMixin,
    PlanningCycleEnqueueMixin,
    PlannerFailureBackoffMixin,
):
    def _emit_planner_verdict(
        self,
        *,
        status: PlannerVerdictStatus,
        reason: str,
        completion_kind: str,
        resume_outcome: bool | str,
        terminal_signature: str = "",
        **details: Any,
    ) -> bool:
        raise NotImplementedError

    def _retry_pending_planner_verdict(self) -> tuple[bool, bool | str | None]:
        raise NotImplementedError

    def _reconcile_open_ended_terminal_stage_action(self, verdict: Any) -> str:
        """Ask the Manager to reopen a completed final stage when work remains.

        A Planner at a certified final stage cannot legally enqueue earlier-stage
        work and cannot write ``PIPELINE_STATE.json``. When it structurally
        returns ``project_done=False`` with no tasks in an open-ended campaign,
        give its advisory verdict to the Manager, which may roll back or hold.
        Returns ``"rollback"``, ``"hold"``, or ``""`` for no authoritative
        terminal reconciliation.
        """
        if not getattr(self.config, "open_ended", False):
            return ""
        if bool(getattr(verdict, "project_done", False)):
            return ""
        if list(getattr(verdict, "new_tasks", []) or []):
            return ""

        root = self._artifact_root()
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_has_current_completion_certificate,
        )

        vertical = resolve_vertical(root)
        if not vertical_has_current_completion_certificate(root, vertical):
            return ""

        manager = self._bound_manager()
        on_event = getattr(self.sink, "handle_event", None)
        decision = manager.decide_stage_transition(
            review=None,
            planner_verdict=verdict,
            project_root=root,
            on_event=on_event,
            open_ended=True,
            continuous_objective=self.config.continuous_objective,
        )
        self._emit({
            "type": EventType.LIFE_MANAGER_STAGE_DECISION,
            "action": decision.action,
            "target_stage": decision.target_stage,
            "reason": decision.reason,
            "current_stage": decision.current_stage,
            "source": decision.source,
            "diagnostic": decision.diagnostic,
            "trigger": "open_ended_terminal_stage_reconciliation",
        })
        if decision.action != "rollback":
            # `complete` and `hold` both mean there is no earlier-stage work to
            # enqueue. Idle instead of turning either decision into a Planner error.
            if decision.action in {"hold", "complete"} and decision.source == "manager_llm":
                return "hold"
            return ""

        self._emit_status(
            "manager reopened open-ended campaign at "
            f"{decision.target_stage}"
        )
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        return "rollback"

    def _latest_unassessed_review_for_current_stage(
        self,
    ) -> tuple[Any, Any, str] | None:
        """Recover a persisted Reviewer verdict skipped by an older stage hook."""
        import json
        from pathlib import Path
        from types import SimpleNamespace

        from ...skills.stage_machine import current_stage

        root = Path(self._artifact_root())
        candidate_root = Path(self._project_workdir())
        stage = current_stage(root).strip().lower()
        if not stage:
            return None
        items = sorted(
            self.memory.backlog.history(),
            key=lambda item: (float(item.finished_ts or 0), float(item.ts or 0)),
            reverse=True,
        )
        handoff_base = Path(
            getattr(self.memory, "project_root", None)
            or getattr(self.memory, "root", None)
            or self._artifact_root()
        )
        for item in items:
            outcome = item.outcome if isinstance(item.outcome, dict) else {}
            item_stage = self._item_pipeline_stage(item)
            if (
                (item_stage and item_stage != stage)
                or not str(outcome.get("review_status") or "").strip()
                or str(outcome.get("interruption_kind") or "none") != "none"
            ):
                continue
            if (
                item.status != "done"
                or self._item_skips_stage_transition(item)
                # ``deferred`` is a Planner node that held the stage instead of
                # closing it — exactly the reviewed evidence this replay exists
                # to recover. ``intentionally_skipped`` stays excluded: there
                # the stage writer was suppressed on purpose and the verdict is
                # not the campaign's to reuse.
                or str(outcome.get("stage_certification") or "")
                not in {"not_assessed", "deferred"}
            ):
                return None
            handoff_root = handoff_base / "handoffs" / item.id
            try:
                mission = json.loads(
                    (handoff_root / "mission.json").read_text(encoding="utf-8")
                )
            except (OSError, TypeError, ValueError):
                return None
            if (
                not isinstance(mission, dict)
                or str(mission.get("mission_id") or "") != item.id
                or str(mission.get("stage") or "").strip().lower() != stage
            ):
                return None
            # The item's outcome describes its latest run, which is the run
            # mission.json names. Rounds restart at one in every run, so an
            # earlier run's acceptance (possibly of an objective that a requeue
            # has since rewritten) must not be replayed, and only creation time
            # orders this run's reviews.
            from ..context_packet import mission_run

            current_run = mission_run(mission)
            run_handoffs: list[dict] = []
            for handoff_path in handoff_root.glob("round-*.json"):
                try:
                    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                except (OSError, TypeError, ValueError):
                    continue
                if isinstance(handoff, dict) and mission_run(handoff) == current_run:
                    run_handoffs.append(handoff)
            for handoff in sorted(
                run_handoffs,
                key=lambda payload: float(payload.get("created_at") or 0),
                reverse=True,
            ):
                review = handoff.get("review")
                if (
                    handoff.get("kind") != "round_reviewed_handoff"
                    or str(handoff.get("mission_id") or "") != item.id
                    or handoff.get("producer_role") != "reviewer"
                    or not isinstance(review, dict)
                    or str(review.get("status") or "").strip().lower() != "done"
                    or not str(review.get("reason") or "").strip()
                ):
                    continue
                mission_scope = str(mission.get("scope") or "").strip().lower()
                manuscript_binding = review.get("manuscript_snapshot")
                if (
                    mission_scope == "final_submission"
                    and not isinstance(manuscript_binding, dict)
                ):
                    from ...core.stage_certificate import latest_stage_review

                    stage_review = latest_stage_review(self.memory.root, stage)
                    if (
                        not isinstance(stage_review, dict)
                        or str(stage_review.get("task_id") or "") != item.id
                        or str(stage_review.get("review_status") or "") != "done"
                        or Path(
                            str(stage_review.get("project_root") or "")
                        ).resolve()
                        != candidate_root.resolve()
                    ):
                        continue
                    manuscript_binding = stage_review.get("manuscript_snapshot")
                    try:
                        reviewed_at = float(stage_review.get("recorded_at") or 0.0)
                        rendered_at = (candidate_root / "paper" / "main.pdf").stat().st_mtime
                    except (OSError, TypeError, ValueError):
                        continue
                    if reviewed_at <= 0.0 or rendered_at > reviewed_at:
                        continue
                if mission_scope == "final_submission":
                    if not isinstance(manuscript_binding, dict):
                        continue
                    try:
                        from ...core.manuscript_snapshot import (
                            manuscript_review_status,
                        )

                        if manuscript_review_status(
                            {"manuscript_snapshot": manuscript_binding},
                            candidate_root,
                        ).get("status") != "current":
                            continue
                    except Exception:  # noqa: BLE001 - exact binding fails closed
                        continue
                    reviewed_signature = str(
                        review.get("final_submission_signature") or ""
                    )
                    if (
                        reviewed_signature
                        and reviewed_signature != self._final_submission_signature()
                    ):
                        continue
                recovered_review = SimpleNamespace(
                    status="done",
                    reason=str(review["reason"]).strip(),
                    next_action=str(review.get("next_action") or "").strip(),
                    operator_question=str(
                        review.get("operator_question") or ""
                    ).strip(),
                    review_source="reviewer",
                    manuscript_snapshot=manuscript_binding,
                    venue_review=review.get("venue_review"),
                    venue_review_snapshot=review.get("venue_review_snapshot"),
                )
                if mission_scope == "final_submission":
                    from ...skills.vertical_select import resolve_vertical_if_decided

                    if resolve_vertical_if_decided(root) == "research":
                        from ...core.venue_review import current_venue_acceptance_issue

                        if current_venue_acceptance_issue(
                            recovered_review, state_root=root, artifact_root=candidate_root,
                        ):
                            continue
                return item, recovered_review, mission_scope
            return None
        return None

    def _reconcile_reviewed_stage_empty_plan(self, verdict: Any) -> str:
        """Replay real current-stage review evidence to the Manager.

        Gated on ``continuous``, not ``open_ended``. ``open_ended`` answers a
        different question — whether a Planner ``project_done`` should be
        honoured or ignored — and using it here made stage traversal a
        privilege of never-finishing campaigns. Reconcile before asking the
        Planner for more work; otherwise its required no-empty-task repair can
        invent another same-stage mission before the Manager sees accepted
        review evidence. The empty-plan path still calls this method for
        persisted campaigns created by older runtimes.
        """
        if not getattr(self.config, "continuous", False):
            return ""
        recovered = self._latest_unassessed_review_for_current_stage()
        if recovered is None:
            return ""
        item, review, mission_scope = recovered
        root = self._artifact_root()

        decision = self._bound_manager().decide_stage_transition(
            review=review,
            # This path replays previously unassessed Reviewer evidence. The
            # empty Planner verdict only triggered recovery; it is not new
            # stage evidence and must not force another semantic adjudication.
            planner_verdict=None,
            project_root=root,
            on_event=getattr(self.sink, "handle_event", None),
            # The real value, not a literal: a bounded campaign reaching this
            # path must not be described to the Manager as open-ended.
            open_ended=bool(getattr(self.config, "open_ended", False)),
            continuous_objective=self.config.continuous_objective,
            mission_scope=mission_scope,
        )
        self._emit({
            "type": EventType.LIFE_MANAGER_STAGE_DECISION,
            "action": decision.action,
            "target_stage": decision.target_stage,
            "reason": decision.reason,
            "current_stage": decision.current_stage,
            "source": decision.source,
            "diagnostic": decision.diagnostic,
            "trigger": "reviewed_stage_empty_plan_reconciliation",
            "recovered_item_id": item.id,
        })
        outcome = {**item.outcome, "stage_certification": {
            "advance": "certified",
            "complete": "certified",
            "hold": "not_certified",
            "rollback": "revoked",
        }.get(decision.action, "not_assessed")}
        if decision.action == "complete":
            # The Manager closed the objective on this task's acceptance: the
            # task's completion now certifies it, recorded as its settlement
            # would have recorded it, so the map, the banner and the delivery agree.
            final = mission_scope.strip().lower() == "final_submission"
            manuscript_binding = getattr(review, "manuscript_snapshot", None)
            if final and not isinstance(manuscript_binding, dict):
                return ""
            if not self._emit({
                "type": EventType.LIFE_MISSION_COMPLETED,
                "item_id": item.id,
                "title": item.title,
                "objective": item.objective,
                "scope": mission_scope,
                "independent_review_required": True,
                "success": True,
                "status": "done",
                "summary": review.reason,
                "review_source": "reviewer",
                "outcome": outcome,
                "overall_complete": final or not getattr(self.config, "open_ended", False),
                "certification_recovered": True,
                **({
                    "final_submission_certified": True,
                    "final_submission_signature": self._final_submission_signature(),
                    "manuscript_snapshot": dict(manuscript_binding),
                    "venue_review": getattr(review, "venue_review", None),
                    "venue_review_snapshot": getattr(review, "venue_review_snapshot", None),
                } if final else {}),
            }):
                return ""
        if decision.source in {"manager_llm", "stage_completion_gate_hold"}:
            self.memory.backlog.update(item.id, outcome=outcome)
        if decision.action not in {"advance", "complete", "rollback"}:
            return ""
        self._emit_status(
            f"manager reconciled reviewed stage to {decision.target_stage}"
        )
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        return decision.action

    def _reconcile_planner_handoff(self, verdict: Any) -> str:
        """Put a stage the Planner handed to the Manager (``→ manager``) to it.

        Each request is put to the Manager once per stage; the same request
        returned unchanged is recorded, not adjudicated again. The Manager alone
        decides HOLD versus ROLLBACK, and a HOLD's reason reaches the Planner
        as feedback for its next turn. ``"unanswered"`` means the Manager's
        backend gave no answer and the request is still open.
        """
        if getattr(verdict, "handoff", "") != "manager" or verdict.project_done:
            return ""
        verdict = replace(verdict, new_tasks=[])
        root = self._artifact_root()
        from ...skills.stage_machine import current_stage

        stage = current_stage(root)
        key = (stage, " ".join(str(verdict.reason or "").split()))
        if key == getattr(self, "_last_planner_handoff_key", None):
            return ""
        self._last_planner_handoff_key = key

        try:
            manager = self._bound_manager()
        except RuntimeError:
            # Without a Manager there is nobody above the Planner to climb to.
            return ""
        decision = manager.decide_stage_transition(
            review=None,
            planner_verdict=verdict,
            project_root=root,
            on_event=getattr(self.sink, "handle_event", None),
            open_ended=bool(self.config.open_ended),
            continuous_objective=self.config.continuous_objective,
        )
        self._emit({
            "type": EventType.LIFE_MANAGER_STAGE_DECISION,
            "action": decision.action,
            "target_stage": decision.target_stage,
            "reason": decision.reason,
            "current_stage": decision.current_stage,
            "source": decision.source,
            "diagnostic": decision.diagnostic,
            "trigger": "planner_handoff",
        })
        if decision.source == "failsafe_hold" or decision.diagnostic == "empty_output":
            # The Manager's backend gave no answer; the handoff stays open.
            self._last_planner_handoff_key = None
            return "unanswered"
        if decision.source != "manager_llm" and decision.action != "rollback":
            # Deterministic HOLDs are not authoritative; the next handoff asks again.
            self._last_planner_handoff_key = None

        # The Manager's HOLD answers the handoff. Unpersisted, the Planner's
        # inputs never move and it is never asked again.
        diagnostic = decision.diagnostic
        if decision.action == "hold" and decision.source == "manager_llm":
            if not self._persist_manager_planner_feedback(
                stage=stage, reason=decision.reason, diagnostic=diagnostic,
            ):
                self._emit_status("failed to persist Manager feedback for Planner; retry later")
                return ""
            self._last_planner_handoff_key = None
            self._reset_idle_backoff()
            self._emit({
                "type": "life.manager.feedback.persisted",
                "stage": stage,
                "reason": decision.reason,
                "diagnostic": diagnostic,
            })
            self._emit_status(f"Manager HOLD returned to Planner for {stage} replanning")
            return "hold"

        if decision.action != "rollback":
            return ""
        self._last_planner_handoff_key = None
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        self._emit_status(
            "manager answered the Planner's handoff by reopening "
            f"{decision.target_stage}"
        )
        return "rollback"

    def _resolve_vertical_once(self) -> dict[str, Any]:
        """Let Manager select the vertical once for the next mission."""
        if getattr(self, "_vertical_resolved", False):
            return {}
        self._vertical_resolved = True

        from ...skills import vertical_select as _vsel

        artifact_root = self._artifact_root()
        try:
            prior_vertical = _vsel._persisted_vertical(artifact_root)
        except _vsel.VerticalResolutionError:
            # Decide again on a later cycle, once the operator has repaired the
            # environment; the intake gate turns this raise into a held state.
            self._vertical_resolved = False
            raise
        if not self.config.continuous_objective:
            persisted = prior_vertical
            if persisted is None:
                return {}
            self._emit({
                "type": EventType.LIFE_VERTICAL_RESOLVED,
                "vertical": persisted,
                "profile_hint": "persisted",
                "agent_layer": "planner",
            })
            return {"vertical": persisted}

        mgr = self._bound_manager()
        from ...core.operator_context import (
            build_operator_context_block,
            operator_context_state_root,
        )

        directive, _operator_context_revision = build_operator_context_block(
            "manager", operator_context_state_root(self.memory), consume_once=False
        )
        selection_objective = "\n\n".join(
            part
            for part in (
                self.config.continuous_objective,
                directive,
            )
            if str(part or "").strip()
        )
        decision = mgr.decide_vertical(selection_objective)
        try:
            current_stage = str(mgr.current_stage() or "")
            selected_stages = list(mgr.plan_stages(decision.vertical))
        except Exception:  # noqa: BLE001 - commit remains the authority boundary
            current_stage = ""
            selected_stages = []
        reset_stage = bool(
            prior_vertical != decision.vertical
            or (
                current_stage
                and selected_stages
                and current_stage not in selected_stages
            )
        )
        division = mgr.commit_vertical_decision(
            self.config.continuous_objective,
            decision,
            ask_on_new_domain=False,
            force_stage_reset=reset_stage,
            _lock_held=True,
        )
        intent = {
            "vertical": str(getattr(division, "vertical", "") or ""),
            "domain": str(getattr(division, "domain", "") or ""),
            "kind": str(getattr(division, "kind", "") or ""),
            "workflow_mode": str(getattr(division, "workflow_mode", "") or ""),
            "learned_vertical_status": str(
                getattr(division, "learned_vertical_status", "") or ""
            ),
            "stages": list(getattr(division, "stages", ()) or ()),
        }
        intent = {key: value for key, value in intent.items() if value}
        try:
            intent["current_stage"] = str(mgr.current_stage() or "")
        except Exception:  # noqa: BLE001 - stage is prompt context only
            pass
        self._emit({
            "type": EventType.LIFE_VERTICAL_RESOLVED,
            "vertical": division.vertical,
            "profile_hint": "manager-per-mission",
            "agent_layer": "planner",
        })
        return intent

    def _plan_next_work(
        self,
        *,
        revision_request: dict[str, Any] | None = None,
    ) -> bool | None | str:
        try:
            return self._plan_next_work_with_delivery(revision_request=revision_request)
        finally:
            release = getattr(self, "_release_operator_inbox", None)
            if callable(release):
                release()

    def _plan_next_work_with_delivery(
        self,
        *,
        revision_request: dict[str, Any] | None = None,
    ) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and ``None`` when
        the planner fails and should be retried later.

        This orchestrates the planning-cycle lifecycle phases in order: intake
        gating, preflight short-circuits, reviewed-stage reconciliation,
        planner invocation, verdict normalization, handoff handling,
        project_done normalization, the no-new-tasks rejection, and finally
        backlog dedupe/enqueue/commit. Each phase mutates a shared
        ``_PlanCycleState`` scratch object and returns ``None`` to continue the
        cycle, or a non-``None`` result to return after applying task retirement.
        """
        state = _PlanCycleState(revision_request)
        for phase in (
            self._pc_intake_gate,
            self._pc_preflight_shortcircuits,
            self._pc_reconcile_reviewed_stage,
            self._pc_invoke_planner,
            self._pc_normalize_verdict,
            self._pc_handle_handoff,
            self._pc_normalize_project_done,
            self._pc_reject_if_no_tasks,
            self._pc_build_dedupe_index,
            self._pc_build_pending_items,
            self._pc_commit_pending_items,
        ):
            result = phase(state)
            if result is not None:
                break
        if state.verdict is not None and not state.verdict.error:
            self._pc_retire_tasks(state)
        if result is None:
            result = self._pc_emit_final_verdict(state)
        # Reading a prompt is not handling its input. Checkpoint only committed
        # work or an accepted completion; a turn without new work can use the
        # unchanged-input skip but must be reconsidered after restart.
        if (
            state.planner_invoked
            and state.verdict is not None
            and not state.verdict.error
            and (state.added_titles or state.completion_accepted)
        ):
            from ...core.operator_context import OperatorContextStore, operator_context_state_root

            try:
                if state.operator_context_revision > 0:
                    settled = OperatorContextStore(
                        operator_context_state_root(self.memory)
                    ).acknowledge(
                        "planner", state.operator_context_revision,
                        settle=state.operator_settlements(),
                    )
                    if settled:
                        log.info("Planner settled idle operator instructions %s", list(settled))
                settle = getattr(self, "_settle_operator_inbox", None)
                if callable(settle):
                    settle(state.inbox_delivery_messages)
            except (OSError, ValueError, RuntimeError):
                log.exception("failed to checkpoint handled Planner input")
                self._enter_pause_backoff()
                return PLAN_ERROR
        if state.planner_invoked:
            if state.verdict is not None and not planner_failure_kind(state.verdict):
                # The model service answered: an outage holding every call is over.
                self._close_runtime_failure_circuit(
                    reason=f"planner cycle {self._planning_cycles} reached the model service",
                    outage_only=True,
                )
        self._arm_unchanged_planner_skip(state)
        self._record_planner_turn_outcome(state, result)
        return result

    def _pc_reconcile_reviewed_stage(
        self,
        state: _PlanCycleState,
    ) -> bool | None | str:
        if state.revision_request is not None:
            return None
        action = self._reconcile_reviewed_stage_empty_plan(None)
        if action in {"advance", "complete", "rollback"}:
            return PLAN_RETRY
        if (
            not state.had_operator_messages
            and not state.has_unhandled_operator_input
            and not any(
                item.status not in {"done", "failed", "aborted", "skipped", "superseded"}
                for item in self.memory.backlog.active()
            )
            and self._effective_final_certification_gate(self._artifact_root())
            and self._journal_has_final_certification()
        ):
            from ...planner import PlannerVerdict

            state.verdict = PlannerVerdict(
                project_done=True,
                new_tasks=[],
                reason="independent final certification is current",
            )
            return self._pc_normalize_project_done(state)
        return None


__all__ = [
    "PlanningCycleMixin",
    "_render_revision_request",
    "_research_project_done_issue",
    "_revision_reason",
]
