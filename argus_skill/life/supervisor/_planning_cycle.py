"""One continuous-planner cycle: decide vertical, plan, dedupe, enqueue."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ._constants import MANAGER_RECONCILE_AFTER_IDLE_CYCLES, PLAN_RETRY
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

_LIVE_WAIT_OBSERVE_MARKERS = (
    "observe only",
    "observe the existing",
    "observe the current",
    "只观察",
    "观察现有",
)
_LIVE_WAIT_STOP_MARKERS = (
    "remains live, stop",
    "still running, stop",
    "stop without",
    "record current live status and stop",
    "仍在运行则停止",
)
_INDEPENDENT_WORK_MARKERS = (
    "independently implement",
    "independently continue",
    "regardless of",
    "in parallel",
    "while the task runs",
    "while the job runs",
    "同时",
    "并行",
    "无论",
)
_OPERATOR_WAIT_MARKERS = (
    "operator",
    "credential",
    "authorization",
    "approval",
    "human input",
    "人工",
    "凭据",
    "授权",
    "批准",
)


class PlanningCycleMixin(
    PlanningCycleIntakeMixin,
    PlanningCycleVerdictMixin,
    PlanningCycleCompletionMixin,
    PlanningCycleEnqueueMixin,
):
    def _waitable_subagent_jobs(self) -> list[Any]:
        try:
            from ...engineer.external_work import scan_external_work

            return [
                job
                for job in scan_external_work(self._project_workdir())
                if job.source == "subagent" and job.waitable
            ]
        except Exception:  # noqa: BLE001 - liveness discovery is fail-soft
            log.debug("failed to inspect waitable subagents", exc_info=True)
            return []

    @staticmethod
    def _text_references_live_subagents(text: str, jobs: list[Any]) -> bool:
        text = " ".join(str(text or "").split()).casefold()
        return any(job.work_id.casefold() in text for job in jobs)

    @staticmethod
    def _text_is_monitor_only(text: str) -> bool:
        text = " ".join(str(text or "").split()).casefold()
        if any(marker in text for marker in _OPERATOR_WAIT_MARKERS):
            return False
        if any(marker in text for marker in _INDEPENDENT_WORK_MARKERS):
            return False
        if any(marker in text for marker in _LIVE_WAIT_OBSERVE_MARKERS):
            return True
        has_status_probe = (
            "argus_skill.tools.subagent status" in text
            or "subagent status --task-id" in text
            or "check its status" in text
            or "检查其状态" in text
        )
        return has_status_probe and any(
            marker in text for marker in _LIVE_WAIT_STOP_MARKERS
        )

    @classmethod
    def _task_only_waits_for_live_subagents(
        cls,
        task: Any,
        jobs: list[Any],
    ) -> bool:
        reference_text = " ".join(
            str(value or "")
            for value in (
                getattr(task, "title", ""),
                getattr(task, "objective", ""),
                getattr(task, "acceptance_check", ""),
            )
        )
        monitor_text = " ".join(
            str(value or "")
            for value in (
                getattr(task, "objective", ""),
                getattr(task, "acceptance_check", ""),
            )
        )
        return cls._text_references_live_subagents(
            reference_text,
            jobs,
        ) and cls._text_is_monitor_only(monitor_text)

    def _live_subagent_event_wait_contract(
        self,
        jobs: list[Any],
    ) -> Any | None:
        if not jobs:
            return None
        from ...planner import WaitingContract

        rows = sorted(
            (
                job.work_id,
                job.run_id or f"started:{job.started_at:.6f}",
            )
            for job in jobs
        )
        token = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
        job_ids = ", ".join(row[0] for row in rows)
        observed_revision = self._planner_waiting_observed_revision(
            wake_on=["subagent_state"],
            watched_paths=[],
        )
        revalidated_rows = sorted(
            (
                job.work_id,
                job.run_id or f"started:{job.started_at:.6f}",
            )
            for job in self._waitable_subagent_jobs()
        )
        if rows != revalidated_rows:
            return None
        return WaitingContract(
            blocker_fingerprint=f"live-subagents:{token[:24]}",
            recheck_condition=f"durable task state changes: {job_ids}",
            recheck_token=token,
            wait_mode="event",
            wake_on=("subagent_state",),
            observed_revision=observed_revision,
        )

    def _normalize_live_subagent_wait(self, verdict: Any) -> Any:
        jobs = self._waitable_subagent_jobs()
        if not jobs:
            return verdict
        source = ""
        waiting = bool(getattr(verdict, "waiting", False))
        existing_contract = getattr(verdict, "waiting_contract", None)
        if waiting:
            waiting_text = " ".join(
                str(value or "")
                for value in (
                    getattr(verdict, "waiting_reason", ""),
                    getattr(verdict, "reason", ""),
                    getattr(existing_contract, "blocker_fingerprint", ""),
                    getattr(existing_contract, "recheck_condition", ""),
                    getattr(existing_contract, "recheck_token", ""),
                )
            ).casefold()
            references_live_job = self._text_references_live_subagents(
                waiting_text,
                jobs,
            )
            monitor_only_wait = self._text_is_monitor_only(waiting_text)
            if references_live_job and monitor_only_wait and existing_contract is None:
                source = "missing_wait_contract"
            elif existing_contract is not None:
                wake_on = set(getattr(existing_contract, "wake_on", ()) or ())
                existing_wait_mode = str(
                    getattr(existing_contract, "wait_mode", "poll") or "poll"
                ).strip().lower()
                subagent_event_contract = (
                    existing_wait_mode == "event"
                    and bool(
                        {"subagent_state", "subagent_terminal"}.intersection(
                            wake_on
                        )
                    )
                )
                if (
                    references_live_job
                    and (monitor_only_wait or subagent_event_contract)
                    and not bool(
                        getattr(existing_contract, "operator_action_required", False)
                    )
                    and not bool(
                        getattr(
                            existing_contract,
                            "stage_reconciliation_required",
                            False,
                        )
                    )
                ):
                    if existing_wait_mode != "event":
                        source = "poll_wait_contract"
                    elif not str(
                        getattr(existing_contract, "observed_revision", "") or ""
                    ):
                        source = "unvalidated_event_wait_contract"
        else:
            tasks = list(getattr(verdict, "new_tasks", []) or [])
            if tasks and all(
                self._task_only_waits_for_live_subagents(task, jobs)
                for task in tasks
            ):
                source = "status_only_task"
        if not source:
            return verdict
        contract = self._live_subagent_event_wait_contract(jobs)
        if contract is None:
            return verdict
        reason = (
            "healthy durable work is already self-watched, so this cycle's "
            "status-probe tasks were dropped rather than run. Waiting is not "
            "the only move left: a mission that does not need this job's result "
            "— a baseline to reproduce, analysis written against the agreed "
            "schema, a section the paper already owes — would be scheduled "
            "normally and run beside it. Only status probes are suppressed here."
        )
        self._emit(
            {
                "type": "life.planner.external_poll_suppressed",
                "cycle": self._planning_cycles,
                "source": source,
                "work_ids": [job.work_id for job in jobs],
                "recheck_token": contract.recheck_token,
                "suppressed_task_titles": [
                    str(getattr(task, "title", "") or "")
                    for task in list(getattr(verdict, "new_tasks", []) or [])
                ],
            }
        )
        from dataclasses import replace

        return replace(
            verdict,
            project_done=False,
            reason=reason,
            new_tasks=[],
            waiting=True,
            waiting_reason=reason,
            waiting_contract=contract,
        )

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
        if str(getattr(contract, "wait_mode", "") or "").strip().lower() == "event":
            return None
        watched = self._waitable_subagent_jobs()
        if not watched:
            return None
        root = self._artifact_root()
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
            hypothesis=(
                "A distinct current-stage deliverable can advance without polling "
                "or changing the supervised background jobs."
            ),
            goal_contribution=(
                "Use otherwise idle wall time on a prerequisite or deliverable that "
                "shortens the path to the standing objective."
            ),
            expected_regressions="None; do not touch the in-flight jobs or their state.",
            decision_rule=(
                "Stop or revise if every useful current-stage action depends on the "
                "background result."
            ),
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
        if decision.source == "manager_llm":
            outcome = dict(item.outcome)
            outcome["stage_certification"] = {
                "advance": "certified",
                "complete": "certified",
                "hold": "not_certified",
                "rollback": "revoked",
            }.get(decision.action, "not_assessed")
            self.memory.backlog.update(item.id, outcome=outcome)
        if decision.action not in {"advance", "complete", "rollback"}:
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
            # Uncontracted waits still require Manager review. Use stage plus a
            # digest of the Planner's reason as a stable deduplication key.
            reason = " ".join(
                str(
                    getattr(verdict, "waiting_reason", "")
                    or getattr(verdict, "reason", "")
                    or ""
                ).split()
            )
            if not reason:
                return ""
            blocker_fingerprint = "uncontracted:" + hashlib.sha256(
                reason.encode("utf-8")
            ).hexdigest()[:16]
            recheck_token = "uncontracted"
            uncontracted = True
        else:
            uncontracted = False

        # Manager is the sole stage authority, but it is not the operator and
        # cannot expand the operator's scope.  Never invoke wait reconciliation
        # when fresh operator input is the declared (or parser-inferred) gate.
        if bool(getattr(contract, "operator_action_required", False)):
            self._planner_waits_since_reconciliation = 0
            return ""

        explicitly_requested = bool(
            getattr(contract, "stage_reconciliation_required", False)
        )
        wake_on = set(getattr(contract, "wake_on", ()) or ())
        if (
            str(getattr(contract, "wait_mode", "") or "").strip().lower()
            == "event"
            and not explicitly_requested
            and str(getattr(contract, "blocker_fingerprint", "") or "").startswith(
                "live-subagents:"
            )
            and bool(wake_on)
            and wake_on <= {"subagent_state", "subagent_terminal"}
        ):
            self._planner_waits_since_reconciliation = 0
            return ""

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
        if not same_contract and not uncontracted:
            contract_state = self._persist_planner_waiting_contract(contract)
            if contract_state is None:
                self._emit_status(
                    "failed to persist Planner wait before Manager reconciliation"
                )
                return ""
        elif uncontracted:
            # Nothing to persist: there is no contract, so there is also no
            # durable slot for the Manager's resolution to be handed back
            # through. Treating that as a failure is what kept the review from
            # happening at all. In-memory key deduplication above still bounds
            # how often an unchanged wait is re-adjudicated.
            contract_state = None

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
            and not bool(getattr(decision, "resolves_wait", False))
            and (uncontracted or explicitly_requested)
        ):
            persisted = self._persist_manager_planner_feedback(
                stage=stage,
                reason=decision.reason,
                diagnostic="manager_hold_requires_stage_repair",
            )
            if not persisted:
                self._emit_status(
                    "failed to persist Manager HOLD repair; retry later"
                )
                return ""
            self._deactivate_planner_waiting_contract()
            self._clear_planner_wait_resolution()
            self._last_planner_wait_reconciliation_key = None
            self._planner_waits_since_reconciliation = 0
            self._reset_idle_backoff()
            self._emit({
                "type": "life.manager.feedback.persisted",
                "stage": stage,
                "reason": decision.reason,
                "diagnostic": "manager_hold_requires_stage_repair",
            })
            self._emit_status(
                f"Manager HOLD converted to one bounded {stage} repair"
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

    def _resolve_vertical_once(self) -> dict[str, Any]:
        """Let Manager select the vertical once for the next mission."""
        if getattr(self, "_vertical_resolved", False):
            return {}
        self._vertical_resolved = True

        from ...skills import vertical_select as _vsel

        artifact_root = self._artifact_root()
        if not self.config.continuous_objective:
            persisted = _vsel._persisted_vertical(artifact_root)
            if persisted is None:
                return {}
            self._emit({
                "type": "life.vertical.resolved",
                "vertical": persisted,
                "profile_hint": "persisted",
                "agent_layer": "planner",
            })
            return {"vertical": persisted}

        mgr = self._bound_manager()
        from ...manager.directive import active_manager_directive_message

        directive = active_manager_directive_message(artifact_root)
        selection_objective = "\n\n".join(
            part
            for part in (
                self.config.continuous_objective,
                directive,
            )
            if str(part or "").strip()
        )
        decision = mgr.decide_vertical(selection_objective)
        from ...skills.vertical_select import _persisted_vertical

        prior_vertical = _persisted_vertical(artifact_root)
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
            "type": "life.vertical.resolved",
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
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and ``None`` when
        the planner fails and should be retried later.

        This orchestrates the planning-cycle lifecycle phases in order: intake
        gating, preflight short-circuits, reviewed-stage reconciliation,
        planner invocation, verdict normalization, waiting handling,
        project_done normalization, the no-new-tasks rejection, and finally
        backlog dedupe/enqueue/commit. Each phase mutates a shared
        ``_PlanCycleState`` scratch object and returns ``None`` to continue the
        cycle, or a non-``None`` result that the caller should return
        immediately.
        """
        state = _PlanCycleState(revision_request)
        for phase in (
            self._pc_intake_gate,
            self._pc_preflight_shortcircuits,
            self._pc_reconcile_reviewed_stage,
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

    def _pc_reconcile_reviewed_stage(
        self,
        state: _PlanCycleState,
    ) -> bool | None | str:
        if state.revision_request is not None:
            return None
        action = self._reconcile_reviewed_stage_empty_plan(None)
        return PLAN_RETRY if action in {"advance", "complete", "rollback"} else None


__all__ = [
    "PlanningCycleMixin",
    "_render_revision_request",
    "_research_project_done_issue",
    "_revision_reason",
]
