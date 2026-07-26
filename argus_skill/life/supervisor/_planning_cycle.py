"""One continuous-planner cycle: decide vertical, plan, dedupe, enqueue."""

from __future__ import annotations

import logging
import time
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ._constants import MANAGER_RECONCILE_AFTER_IDLE_CYCLES
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
):
    def _independent_overlap_task(self, verdict: Any) -> Any | None:
        """Turn a live-job wait into one useful, non-conflicting mission."""
        if not bool(getattr(verdict, "waiting", False)):
            return None
        if bool(getattr(verdict, "project_done", False)):
            return None
        if list(getattr(verdict, "new_tasks", []) or []):
            return None
        contract = getattr(verdict, "waiting_contract", None)
        if bool(getattr(contract, "operator_action_required", False)):
            return None
        root = self._artifact_root()
        try:
            from ...engineer.external_work import scan_external_work

            watched = [
                job
                for job in scan_external_work(root)
                if job.source == "subagent" and job.waitable
            ]
        except Exception:  # noqa: BLE001 - overlap is a throughput optimization
            return None
        if not watched:
            return None
        title = "Advance independent work while background job runs"
        try:
            if any(
                item.status in {"pending", "running"} and item.title == title
                for item in self.memory.backlog.all()
            ):
                return None
        except Exception:  # noqa: BLE001
            return None
        from ...planner import TaskSpec
        from ...skills.stage_machine import current_stage

        stage = current_stage(root)
        job_ids = ", ".join(job.work_id for job in watched[:4])
        objective = (
            f"Bounded overlap mission while current_stage remains `{stage}` and "
            f"healthy self-watched background job(s) `{job_ids}` continue. Do not "
            "poll, restart, stop, or duplicate those jobs. Produce one concrete "
            "current-stage deliverable that does not depend on their terminal "
            "result: platform/evaluator repair, data or provenance preparation, "
            "analysis code/scaffolding, claim-evidence organization, or manuscript "
            "prose with explicit placeholders as applicable. Inspect the current "
            "stage and existing artifacts first; preserve result-dependent claims "
            "as placeholders. Do not edit Manager-owned stage state."
        )
        return TaskSpec(
            title=title,
            objective=objective,
            impact_score=5,
            impact_area="throughput",
            evidence=f"live self-watched jobs: {job_ids}",
            scope="bounded",
            stage_closing=False,
        )

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

    def _reconcile_open_ended_terminal_stage(self, verdict: Any) -> bool:
        return self._reconcile_open_ended_terminal_stage_action(verdict) == "rollback"

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

        from ...manager import Manager

        manager = Manager(
            project_root=root,
            runner=self.planner_runner,
            skill_store=self.skill_store,
        )
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
            if decision.action == "hold" and decision.source == "manager_llm":
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

        stage = current_stage(self._artifact_root()).strip().lower()
        if not stage:
            return None
        items = sorted(
            self.memory.backlog.all(),
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
            if (
                item.status != "done"
                or self._item_skips_stage_transition(item)
                or str(outcome.get("stage_certification") or "")
                != "not_assessed"
            ):
                continue
            handoff_root = handoff_base / "handoffs" / item.id
            try:
                mission = json.loads(
                    (handoff_root / "mission.json").read_text(encoding="utf-8")
                )
            except (OSError, TypeError, ValueError):
                continue
            if (
                not isinstance(mission, dict)
                or str(mission.get("mission_id") or "") != item.id
                or str(mission.get("stage") or "").strip().lower() != stage
            ):
                continue
            for handoff_path in sorted(
                handoff_root.glob("round-[0-9][0-9][0-9][0-9].json"),
                reverse=True,
            ):
                try:
                    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                except (OSError, TypeError, ValueError):
                    continue
                review = handoff.get("review") if isinstance(handoff, dict) else None
                if (
                    handoff.get("kind") != "round_reviewed_handoff"
                    or str(handoff.get("mission_id") or "") != item.id
                    or handoff.get("producer_role") != "reviewer"
                    or not isinstance(review, dict)
                    or str(review.get("status") or "").strip().lower() != "done"
                    or not str(review.get("reason") or "").strip()
                ):
                    continue
                return (
                    item,
                    SimpleNamespace(
                        status="done",
                        reason=str(review["reason"]).strip(),
                        next_action=str(review.get("next_action") or "").strip(),
                        operator_question=str(
                            review.get("operator_question") or ""
                        ).strip(),
                        review_source="reviewer",
                    ),
                    str(mission.get("scope") or ""),
                )
        return None

    def _reconcile_reviewed_stage_empty_plan(self, verdict: Any) -> str:
        """Replay real current-stage review evidence to the Manager after upgrade."""
        if not getattr(self.config, "open_ended", False):
            return ""
        recovered = self._latest_unassessed_review_for_current_stage()
        if recovered is None:
            return ""
        item, review, mission_scope = recovered
        root = self._artifact_root()

        from ...manager import Manager

        decision = Manager(
            project_root=root,
            runner=self.planner_runner,
            skill_store=self.skill_store,
        ).decide_stage_transition(
            review=review,
            planner_verdict=verdict,
            project_root=root,
            on_event=getattr(self.sink, "handle_event", None),
            open_ended=True,
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
        if decision.source == "manager_llm":
            outcome = dict(item.outcome)
            outcome["stage_certification"] = {
                "advance": "certified",
                "complete": "certified",
                "hold": "not_certified",
                "rollback": "revoked",
            }.get(decision.action, "not_assessed")
            self.memory.backlog.update(item.id, outcome=outcome)
        if decision.action not in {"advance", "rollback"}:
            return ""
        self._emit_status(
            f"manager reconciled reviewed stage to {decision.target_stage}"
        )
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        return decision.action

    def _reconcile_open_ended_planner_waiting(self, verdict: Any) -> str:
        """Let the Manager repair a stage/Planner mutual wait.

        Every new non-operator wait gets one immediate liveness review. If the
        Manager confirms that the blocker remains external, unchanged waits are
        reviewed again only at the bounded cadence below. The Manager alone
        decides HOLD versus ROLLBACK.
        """
        if not getattr(self.config, "open_ended", False):
            return ""
        if not bool(getattr(verdict, "waiting", False)):
            return ""
        if bool(getattr(verdict, "project_done", False)):
            return ""
        if list(getattr(verdict, "new_tasks", []) or []):
            return ""

        contract = getattr(verdict, "waiting_contract", None)
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        if not blocker_fingerprint or not recheck_token:
            return ""

        # Manager is the sole stage authority, but it is not the operator and
        # cannot expand the operator's scope.  Never invoke wait reconciliation
        # when fresh operator input is the declared (or parser-inferred) gate.
        if bool(getattr(contract, "operator_action_required", False)):
            self._planner_waits_since_reconciliation = 0
            return ""

        explicitly_requested = bool(
            getattr(contract, "stage_reconciliation_required", False)
        )

        root = self._artifact_root()
        from ...skills.stage_machine import current_stage

        stage = current_stage(root)
        key = (
            stage,
            blocker_fingerprint,
            recheck_token,
            explicitly_requested,
        )
        last_key = getattr(
            self,
            "_last_planner_wait_reconciliation_key",
            None,
        )
        key_changed = key != last_key
        waits_since_reconciliation = (
            1
            if key_changed
            else int(
                getattr(self, "_planner_waits_since_reconciliation", 0) or 0
            ) + 1
        )
        self._last_planner_wait_reconciliation_key = key
        self._planner_waits_since_reconciliation = waits_since_reconciliation
        contract_state = self._load_planner_waiting_contract_state()
        same_contract = (
            contract_state is not None
            and contract_state.get("blocker_fingerprint") == blocker_fingerprint
            and contract_state.get("recheck_token") == recheck_token
        )
        existing_resolution = (
            contract_state.get("manager_resolution")
            if same_contract and contract_state is not None
            else None
        )
        if isinstance(existing_resolution, dict):
            resolution_retries = int(
                contract_state.get("resolution_retry_count") or 0
            ) + 1
            contract_state["resolution_retry_count"] = resolution_retries
            contract_state["updated_at"] = time.time()
            self._write_planner_waiting_contract_state(contract_state)
            if resolution_retries < MANAGER_RECONCILE_AFTER_IDLE_CYCLES:
                self._enter_idle_backoff()
                self._emit_status(
                    "Planner repeated a resolved wait; backing off before retry"
                )
                return ""
            contract_state["manager_resolution"] = None
            contract_state["resolution_retry_count"] = 0
            contract_state["updated_at"] = time.time()
            self._write_planner_waiting_contract_state(contract_state)
            self._planner_waits_since_reconciliation = 0
            self._emit_status(
                "Planner repeatedly ignored a Manager wait resolution; "
                "re-adjudicating"
            )
        if not (
            key_changed
            or waits_since_reconciliation >= MANAGER_RECONCILE_AFTER_IDLE_CYCLES
        ):
            return ""

        # Immediate reconciliation happens before the ordinary waiting-record
        # path. Persist a freshly returned contract first so an authoritative
        # Manager resolution has durable state to update and the next Planner
        # call receives that resolution instead of repeating the same wait.
        if not same_contract:
            contract_state = self._persist_planner_waiting_contract(contract)
            if contract_state is None:
                self._emit_status(
                    "failed to persist Planner wait before Manager reconciliation"
                )
                return ""

        from ...manager import Manager

        manager = Manager(
            project_root=root,
            runner=self.planner_runner,
            skill_store=self.skill_store,
        )
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
            "trigger": "planner_waiting_reconciliation",
            "resolves_wait": bool(getattr(decision, "resolves_wait", False)),
        })

        if decision.source == "manager_llm" or decision.action == "rollback":
            self._planner_waits_since_reconciliation = 0
        else:
            # Backend/failsafe HOLDs are not authoritative. Retry next wait.
            self._planner_waits_since_reconciliation = (
                MANAGER_RECONCILE_AFTER_IDLE_CYCLES
            )

        if decision.diagnostic == "planner_wait_advance_rejected":
            persisted = self._persist_manager_planner_feedback(
                stage=stage,
                reason=decision.reason,
                diagnostic=decision.diagnostic,
            )
            if not persisted:
                self._emit_status(
                    "failed to persist Manager feedback for Planner; retry later"
                )
                return False
            self._deactivate_planner_waiting_contract()
            self._last_planner_wait_reconciliation_key = None
            self._planner_waits_since_reconciliation = 0
            self._last_open_ended_project_done_signature = ""
            self._reset_idle_backoff()
            self._emit({
                "type": "life.manager.feedback.persisted",
                "stage": stage,
                "reason": decision.reason,
                "diagnostic": decision.diagnostic,
            })
            self._emit_status(
                f"Manager rejection returned to Planner for {stage} replanning"
            )
            return "hold"

        if (
            decision.action == "hold"
            and decision.source == "manager_llm"
            and bool(getattr(decision, "resolves_wait", False))
        ):
            self._resolve_planner_waiting_contract(
                manager_reason=decision.reason,
                target_stage=decision.target_stage,
            )
            self._last_planner_wait_reconciliation_key = None
            self._planner_waits_since_reconciliation = 0
            self._reset_idle_backoff()
            self._emit_status(
                "manager resolved planner wait while holding "
                f"current stage {decision.target_stage}"
            )
            return "hold"

        if decision.action != "rollback":
            return ""

        self._deactivate_planner_waiting_contract()
        self._clear_planner_wait_resolution()
        self._last_planner_wait_reconciliation_key = None
        self._planner_waits_since_reconciliation = 0
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        self._emit_status(
            "manager resolved planner wait by reopening "
            f"{decision.target_stage}"
        )
        return "rollback"

    def _resolve_vertical_once(self) -> None:
        """DECIDE + persist the active vertical exactly once per mission, BEFORE
        any gate/stage read (``resolve_vertical``) runs.

        Precedence:
        * An already-persisted vertical is TRUSTED as-is (sticky across daemon
          restarts; a chosen per-task vertical stays chosen).
        * Otherwise, when a continuous objective is set, the MANAGER AGENT
          decides it (``Manager.divide`` — one grounded call, no keyword
          classifier) and persists it (autonomously authoring a new data domain
          when no built-in fits).

        FAIL-HARD: an undecidable vertical, a missing backend, or a corrupt
        ``PIPELINE_STATE.json`` PROPAGATES — a mission that cannot determine its
        vertical must fail loudly, not silently run the research pipeline.
        """
        if getattr(self, "_vertical_resolved", False):
            return
        # Flip the guard immediately so this decision runs exactly once.
        self._vertical_resolved = True

        from ...skills import vertical_select as _vsel

        artifact_root = self._artifact_root()
        persisted = _vsel._persisted_vertical(artifact_root)
        if persisted is not None:
            from ...core.research_contract import resolve_research_target_level
            from ...verticals._base import (
                load_vertical,
                vertical_research_target_levels,
            )

            supported_levels = vertical_research_target_levels(
                load_vertical(persisted, project_root=artifact_root)
            )
            target_missing = (
                bool(supported_levels)
                and resolve_research_target_level(artifact_root) is None
            )
            if target_missing and self.config.continuous_objective:
                # Legacy research campaigns predate the target field. At this clean
                # planning boundary, ask the Manager once and persist its judgment;
                # never infer the target from objective keywords.
                from ...manager import Manager

                mgr = Manager(project_root=artifact_root, runner=self.planner_runner)
                target = mgr._decide_research_target(
                    self.config.continuous_objective,
                    root_task_id=None,
                    supported_levels=supported_levels,
                )
                _vsel.persist_vertical(
                    artifact_root,
                    persisted,
                    research_target_level=target,
                )
            self._emit({
                "type": "life.vertical.resolved",
                "vertical": persisted,
                "profile_hint": (
                    "persisted-target-restored" if target_missing else "persisted"
                ),
                "agent_layer": "planner",
            })
            return

        if not self.config.continuous_objective:
            return

        from ...manager import Manager

        mgr = Manager(project_root=artifact_root, runner=self.planner_runner)
        division = mgr.divide(self.config.continuous_objective)
        self._emit({
            "type": "life.vertical.resolved",
            "vertical": division.vertical,
            "profile_hint": "manager",
            "agent_layer": "planner",
        })
        log.info(
            "life supervisor: resolved vertical = %s (manager-decided)",
            division.vertical,
        )

    def _plan_next_work(
        self,
        *,
        revision_request: dict[str, Any] | None = None,
    ) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and ``None`` when
        the planner fails and should be retried later.

        This orchestrates the planning-cycle lifecycle phases in order: intake
        gating, preflight short-circuits, planner invocation, verdict
        normalization, waiting handling, project_done normalization, the
        no-new-tasks rejection, and finally backlog dedupe/enqueue/commit. Each
        phase mutates a shared ``_PlanCycleState`` scratch object and returns
        ``None`` to continue the cycle, or a non-``None`` result that the
        caller should return immediately.
        """
        state = _PlanCycleState(revision_request)
        for phase in (
            self._pc_intake_gate,
            self._pc_preflight_shortcircuits,
            self._pc_invoke_planner,
            self._pc_normalize_verdict,
            self._pc_handle_waiting,
            self._pc_normalize_project_done,
            self._pc_reject_if_no_tasks,
            self._pc_build_dedupe_index,
            self._pc_build_pending_items,
            self._pc_commit_pending_items,
        ):
            result = phase(state)
            if result is not None:
                return result
        return self._pc_emit_final_verdict(state)


__all__ = [
    "PlanningCycleMixin",
    "_render_revision_request",
    "_research_project_done_issue",
    "_revision_reason",
]
