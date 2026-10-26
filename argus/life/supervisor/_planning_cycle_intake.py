"""Planning-cycle phase: request intake, gating, and preflight short-circuits.

Covers everything that can decide "there is nothing to plan right now" before
the planner is ever invoked: draining operator messages, Manager→Planner
feedback exhaustion, dynamic-plan revision-request validation, retrying a
pending planner verdict, terminal-idle and unchanged-input outcomes, the wiki-collect
maintenance task, the missing-planner-runner error, the operator
external-blocker short circuit, and the bounded-vertical-reached-terminal-stage
fast path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ...core.mission_view import say
from ...core.planner_verdict import PlannerVerdictStatus
from ._constants import (
    MANAGER_FEEDBACK_REPLAN_LIMIT,
    PLAN_AWAITING,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _revision_reason,
    completion_rejection_circuit_path,
    load_completion_rejection_circuit,
    resume_completion_rejection_circuit,
)

if TYPE_CHECKING:
    from ..memory import BacklogItem
    from ._config import _MemoryView

_TERMINAL_TASK_STATUSES = {"done", "failed", "aborted", "skipped", "superseded"}


class PlanningCycleIntakeMixin:
    """Gate checks + preflight short-circuits run before planner invocation."""

    if TYPE_CHECKING:
        memory: _MemoryView

        def _emit_status(self, text: str) -> None: ...
        def _operator_chinese(self) -> bool: ...
        def _enter_idle_backoff(self) -> None: ...
        def _enter_pause_backoff(self) -> None: ...
        def _hold_on_unresolved_vertical(
            self, exc: Exception, item: BacklogItem | None = None,
        ) -> dict[str, Any]: ...
        def _reset_idle_backoff(self) -> None: ...

    def _emit_bounded_project_completion(self, reason: str) -> bool | str:
        """Record bounded completion, then deliver the Manager project report."""
        from ...core.autonomy import render_autonomous_assumptions

        assumptions = render_autonomous_assumptions(
            self._project_state_root(), chinese=self._operator_chinese(),
        )
        if assumptions:
            # Honest completion: a run that decided without an operator says
            # so in its completion summary, not only in the event log.
            reason = f"{reason}\n{assumptions}" if reason else assumptions
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

    def _enqueue_bounded_manager_direct(
        self,
        state: _PlanCycleState,
    ) -> bool | None:
        """Turn a finite Manager-direct objective into one reviewed mission."""
        if (
            state.revision_request is not None
            or bool(getattr(self.config, "open_ended", False))
            or self._effective_final_certification_gate(self._artifact_root())
        ):
            return None
        intent = state.manager_intent if isinstance(state.manager_intent, dict) else {}
        if str(intent.get("workflow_mode") or "").strip().lower() != "direct":
            return None
        if self._manager_final_stage_is_completed():
            return None
        objective = str(
            intent.get("execution_task")
            or self.config.continuous_objective
            or ""
        ).strip()
        if not objective:
            return None
        # The objective is handed out once. Paused work still owns it and must
        # resume; once work for it has ended (done, superseded or failed),
        # what is left is the Planner's to judge, not a reason to queue it again.
        if any(
            item.status not in _TERMINAL_TASK_STATUSES
            or (item.original_objective or item.objective).strip() == objective
            for item in self.memory.backlog.history()
        ):
            return None

        from ...core.operator_messages import brief_title
        from ..memory import BacklogItem

        title = brief_title(objective.replace("`", ""))
        stage = str(
            intent.get("current_stage") or intent.get("stage") or ""
        ).strip().lower()
        vertical = str(intent.get("vertical") or "").strip()
        from ...verticals._base import load_vertical_contract

        vertical_requires_review = load_vertical_contract(
            vertical,
            project_root=self._artifact_root(),
        ).requires_independent_review
        explicit_review_policy = intent.get("require_independent_review", True)
        requires_review = bool(
            explicit_review_policy is not False or vertical_requires_review
        )
        manager_decision = {**intent, "routed": True, "route_source": "manager"}
        item = BacklogItem.new(
            title=title,
            objective=objective,
            tags=[
                "manager",
                "manager_direct",
                "scope:bounded",
                "stage_closing",
                *(["review:required"] if requires_review else []),
                *(["review:waived"] if not requires_review else []),
                *([f"stage:{stage}"] if stage else []),
            ],
            iterate=False,
            iteration_max_cycles=1,
            original_objective=objective,
            manager_decision=manager_decision,
        )
        self.memory.backlog.add(item)
        if not requires_review:
            self._emit({
                "type": "life.review.waived",
                "item_id": item.id,
                "text": (
                    "independent review waived: Manager explicitly set "
                    "require_independent_review=false"
                ),
                "reason": str(intent.get("reason") or "Manager waiver"),
            })
        self._emit({
            "type": EventType.LIFE_PLANNER_TASK_ADDED,
            "item_id": item.id,
            "title": item.title,
            "objective": item.objective,
            "deps": [],
            "priority": item.priority,
            "source": "manager_direct",
        })
        self._emit_status("manager: direct bounded mission queued")
        return True

    def _bounded_completion_reason(self) -> str:
        """Return a deterministic completion reason for a finite campaign."""
        artifact_root = self._artifact_root()
        if (
            getattr(self.config, "open_ended", False)
            or self._effective_final_certification_gate(artifact_root)
            or self._bounded_completion_issue()
        ):
            return ""
        from ...apps._inbox_delivery import is_host_report, unique_operator_messages
        from ...core.operator_context import (
            OperatorContextStore,
            operator_context_state_root,
        )

        messages = self._drain_user_inbox()
        host_reports: list[str] = []
        while messages and all(is_host_report(message) for message in messages):
            # The current certificate already covers these system reports.
            # Settle their native receipts to expose operator work behind them.
            host_reports.extend(messages)
            self._settle_operator_inbox(messages)
            messages = self._drain_user_inbox()
        self._operator_guidance_carryover = unique_operator_messages(
            list(getattr(self, "_operator_guidance_carryover", None) or [])
            + host_reports + messages
        )
        context = OperatorContextStore(operator_context_state_root(self.memory))
        if (
            any(not is_host_report(message) for message in self._operator_guidance_carryover)
            or context.has_unhandled_input("planner")
        ):
            return ""
        # The operator reads this reason on the timeline and the Manager
        # reports from it: say what happened, not which certificate allowed it.
        return say("requested_work_done", self._operator_chinese())

    def _pc_intake_gate(self, state: _PlanCycleState) -> Any | None:
        """Drain operator input and reject/idle before touching the planner.

        Returns a non-``None`` result when ``_plan_next_work`` should return
        immediately; returns ``None`` to continue the cycle.
        """
        revision_request = state.revision_request
        from ...core.operator_context import (
            OperatorContextStore,
            build_settleable_planner_context_block,
            operator_context_state_root,
        )

        transient_messages = (
            self._take_operator_guidance_carryover() + self._drain_user_inbox()
            if revision_request is None
            else []
        )
        from ...apps._inbox_delivery import (
            is_host_report,
            operator_live_turn,
            unique_operator_messages,
        )

        transient_messages = unique_operator_messages(transient_messages)
        # A background-job report an older release queued is the host telling
        # itself a job settled, so it rides along with the next Planner turn
        # instead of forcing one. Counting it as operator input forced 63 of
        # 131 back-to-back Planner calls on 2026-10-15.
        operator_messages = [
            message for message in transient_messages if not is_host_report(message)
        ]
        state.had_operator_messages = bool(operator_messages)
        state.inbox_delivery_messages = list(transient_messages)
        # Draining appends fresh messages to the durable ledger. Re-render after
        # the drain so this same planning turn sees the complete standing block
        # as well as the legacy one-shot operator note below.
        operator_context, _revision, offered = build_settleable_planner_context_block(
            operator_context_state_root(self.memory),
            live_turn=operator_live_turn(transient_messages),
        )
        state.operator_context_revision = _revision
        state.settleable_operator_revisions = offered
        # Bind rejection holds to the input this cycle saw, not a newer ledger
        # revision that might arrive while Planner or Manager is running.
        self._planning_operator_context_revision = _revision
        state.has_unhandled_operator_input = (
            _revision > OperatorContextStore(operator_context_state_root(self.memory)).acknowledged_revision("planner")
        )
        state.fresh_operator_messages = list(dict.fromkeys(transient_messages))
        state.operator_messages = list(
            dict.fromkeys(
                ([operator_context] if operator_context else [])
            )
        )
        if operator_messages:
            self._clear_manager_planner_feedback()
            self._reset_idle_backoff()
        if operator_messages or state.has_unhandled_operator_input:
            self._last_open_ended_project_done_signature = ""
            # The inbox was already drained above. Do not let a durable idle
            # receipt restore the hold before Planner sees this new instruction.
            from ..planner_verdict_outbox import (
                clear_planner_verdict_outbox,
                load_planner_verdict_outbox,
            )

            record = load_planner_verdict_outbox(self.memory.root)
            if (
                record is not None
                and record["event"].get("completion_kind")
                in {"certified_increment", "project_completed"}
            ):
                if not self._emit({
                    "type": "life.planner.verdict.discarded",
                    "cycle": self._planning_cycles,
                    "reason": "new operator input superseded the prior completion decision",
                    "delivery_id": record["delivery_id"],
                    "prior_verdict": record["event"],
                    "prior_delivered": record.get("delivered", False),
                }):
                    return PLAN_RETRY
                clear_planner_verdict_outbox(self.memory.root)
        if revision_request is None:
            feedback = self._load_manager_planner_feedback()
            if feedback is not None:
                recorded_signature = str(
                    feedback.get("evidence_signature") or ""
                )
                # Filtered-task feedback is judged against the backlog's own
                # state; everything else against the project evidence tree.
                current_signature = self._manager_feedback_signature_for(
                    str(feedback.get("diagnostic") or "")
                )
                if (
                    (
                        state.has_unhandled_operator_input
                        and state.operator_context_revision
                        > int(feedback.get("operator_context_revision") or 0)
                    )
                    or (
                        recorded_signature
                        and current_signature
                        and recorded_signature != current_signature
                    )
                ):
                    self._clear_manager_planner_feedback()
                    self._reset_idle_backoff()
                    feedback = None
            feedback_attempts = int(
                (feedback or {}).get("attempts") or (1 if feedback else 0)
            )
            if feedback is not None and feedback_attempts >= MANAGER_FEEDBACK_REPLAN_LIMIT:
                self._enter_idle_backoff()
                self._emit({
                    "type": "life.manager.feedback.exhausted",
                    "stage": feedback.get("stage") or "",
                    "diagnostic": feedback.get("diagnostic") or "",
                    "reason": feedback.get("reason") or "",
                    "attempts": feedback_attempts,
                })
                self._emit_status(
                    "Manager→Planner feedback repeated without a commit; "
                    "entering terminal idle for operator/new-evidence wake-up"
                )
                return PLAN_TERMINAL_IDLE
            # Completion turn-back stop-loss: while it is paused, a fresh
            # Planner call can only reproduce the same exchange. An operator
            # reply or a real backlog change lifts the pause; the count itself
            # survives so an identical turn-back pauses again immediately.
            circuit_path = completion_rejection_circuit_path(
                Path(
                    str(
                        getattr(self.config, "project_state_dir", None)
                        or getattr(self.memory, "root", None)
                        or "."
                    )
                ),
            )
            circuit = load_completion_rejection_circuit(circuit_path)
            if circuit is not None and circuit.get("paused"):
                if state.had_operator_messages or (
                    state.has_unhandled_operator_input
                    and state.operator_context_revision
                    > int(circuit.get("operator_context_revision") or 0)
                ):
                    resume_completion_rejection_circuit(
                        circuit_path, reason="operator_reply"
                    )
                    self._reset_idle_backoff()
                elif (
                    self._backlog_planning_signature()
                    != str(circuit.get("pause_backlog_signature") or "")
                ):
                    resume_completion_rejection_circuit(
                        circuit_path, reason="backlog_changed"
                    )
                    self._reset_idle_backoff()
                else:
                    self._enter_idle_backoff()
                    self._emit({
                        "type": "life.planner.completion_circuit_holding",
                        "diagnostic": str(circuit.get("diagnostic") or ""),
                        "reason": str(circuit.get("reason") or ""),
                        "consecutive_rejections": int(
                            circuit.get("consecutive_rejections") or 0
                        ),
                    })
                    self._emit_status(
                        "completion attempts stay paused: the same reason "
                        "turned them back repeatedly and neither the backlog "
                        "nor the operator has spoken since"
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
                # A backlog item predating plan versioning has no plan to
                # compare-and-swap, so the atomic replacement this path exists
                # for is meaningless for it. Erroring out was the worst of both
                # worlds: the Reviewer's replan is discarded, the cycle backs
                # off, the item is claimed again, and the same mission reruns —
                # forever, because "unversioned" is not a condition that ever
                # resolves. Exactly one such item is live on this host.
                #
                # With nothing to replace, the honest degradation is an ordinary
                # planning cycle. There is no revision rejection to record:
                # version zero is valid for this legacy/direct item, but not for
                # the versioned plan-revision event family.
                state.revision_request = None
                revision_request = None
        if revision_request is not None:
            try:
                # A replan witness may already have terminalized its source;
                # the source id/version lives in the append-only archive.
                backlog_items = self.memory.backlog.history()
            except Exception as exc:  # noqa: BLE001
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"cannot inspect active plan: {type(exc).__name__}: {exc}",
                })
                return PLAN_ERROR
            state.revision_active_items = [
                item
                for item in backlog_items
                if item.plan_id == state.expected_plan_id
                and item.plan_version == state.expected_plan_version
                and item.status not in {"done", "failed", "skipped", "superseded"}
            ]
            requested_item_id = str(revision_request.get("item_id") or "")
            witness = revision_request.get("plan_revision_witness")
            if isinstance(witness, dict):
                try:
                    witness_version = int(witness.get("plan_version") or 0)
                except (TypeError, ValueError):
                    witness_version = 0
                witness_ids = [
                    str(item_id)
                    for item_id in (witness.get("active_item_ids") or [])
                    if str(item_id)
                ]
                witness_ids = list(dict.fromkeys(witness_ids))
                witness_matches_request = (
                    str(witness.get("plan_id") or "") == state.expected_plan_id
                    and witness_version == state.expected_plan_version
                    and str(witness.get("source_item_id") or "") == requested_item_id
                    and requested_item_id in witness_ids
                )
                if witness_matches_request:
                    by_id = {item.id: item for item in backlog_items}
                    witness_set = set(witness_ids)
                    current_active_ids = {
                        item.id for item in state.revision_active_items
                    }
                    missing_ids = [
                        item_id for item_id in witness_ids if item_id not in by_id
                    ]
                    plan_mismatches = [
                        item_id
                        for item_id in witness_ids
                        if item_id in by_id
                        and (
                            by_id[item_id].plan_id != state.expected_plan_id
                            or by_id[item_id].plan_version != state.expected_plan_version
                        )
                    ]
                    invalid_terminal = [
                        item.id
                        for item in (
                            by_id[item_id]
                            for item_id in witness_ids
                            if item_id in by_id
                        )
                        if item.status in {"done", "aborted", "skipped", "superseded"}
                        or (item.status == "failed" and item.id != requested_item_id)
                    ]
                    unexpected_active = sorted(current_active_ids - witness_set)
                    if not (
                        missing_ids
                        or plan_mismatches
                        or invalid_terminal
                        or unexpected_active
                    ):
                        state.revision_active_items = [
                            by_id[item_id] for item_id in witness_ids
                        ]
                        state.revision_witness_active_item_ids = witness_ids
            requested_item = next(
                (item for item in backlog_items if item.id == requested_item_id),
                None,
            )
            if (
                requested_item is None
                or requested_item.plan_id != state.expected_plan_id
                or requested_item.plan_version != state.expected_plan_version
            ):
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "plan revision conflict: active revision changed",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
                return PLAN_ERROR
            if (
                requested_item.status in _TERMINAL_TASK_STATUSES
                and not state.revision_active_items
            ):
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": (
                        "terminal replan trigger has no active same-plan siblings; "
                        "planning fresh work instead"
                    ),
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                    "item_id": requested_item.id,
                    "trigger_status": requested_item.status,
                })
                state.revision_request = None
                revision_request = None
            elif (
                requested_item.status not in _TERMINAL_TASK_STATUSES
                and requested_item.id not in {item.id for item in state.revision_active_items}
            ):
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "plan revision conflict: active revision changed",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                })
                return PLAN_ERROR
        if revision_request is not None:
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_PROPOSED,
                "expected_plan_id": state.expected_plan_id,
                "expected_plan_version": state.expected_plan_version,
                "active_item_ids": [item.id for item in state.revision_active_items],
                "trigger_item_id": requested_item_id,
                **(
                    {"terminal_trigger_status": requested_item.status}
                    if requested_item.status in _TERMINAL_TASK_STATUSES
                    else {}
                ),
                "reason": _revision_reason(revision_request),
            })

        if revision_request is None:
            retried, retry_outcome = self._retry_pending_planner_verdict()
            if retried:
                return retry_outcome
        terminal_idle = (
            None
            if (
                revision_request is not None
                or state.had_operator_messages
                or state.has_unhandled_operator_input
            )
            else self._maybe_idle_after_unchanged_open_ended_done()
        )
        if terminal_idle is not None:
            return terminal_idle

        if revision_request is None:
            if not state.had_operator_messages:
                unchanged_outcome = self._maybe_skip_unchanged_planner_cycle(
                    state
                )
                if unchanged_outcome is not None:
                    return unchanged_outcome
                failing_hold = self._maybe_hold_failing_planner()
                if failing_hold is not None:
                    return failing_hold

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

        runtime_block = self._runtime_failure_circuit_block()
        if runtime_block is not None:
            self._enter_pause_backoff()
            return PLAN_AWAITING

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
        # final certification requirement is active. A ``--bounded`` mission
        # (``final_certification_gate=False``) does not require the external benchmark
        # targets, so it must fall through to the planner and reach its own
        # ``project_done`` instead of waiting forever on artifacts it never
        # needs. Mirrors the gating in
        # ``_defer_project_done_for_operator_external_blocker``.
        short_circuit = None
        if (
            revision_request is None
            and self._effective_final_certification_gate(self._artifact_root())
        ):
            short_circuit = self._operator_external_blocker_short_circuit_decision(
                project_root=self._project_workdir(),
            )
        if short_circuit is not None:
            return self._record_planner_handoff(short_circuit, state)

        # The mission is now committing to real planning work — every idle /
        # blocked / no-runner / done short-circuit above has returned. Decide +
        # persist the vertical here (once, guarded), so the planner and its
        # downstream gate reads see a stable vertical. Placing it AFTER the
        # short-circuits means a blocked/idle cycle never triggers a Manager
        # decision (nor a wasted planner-runner call).
        if str((state.manager_intent or {}).get("vertical") or "").strip():
            self._vertical_resolved = True
            manager_intent = {}
        else:
            from ...skills.vertical_select import VerticalResolutionError

            try:
                manager_intent = self._resolve_vertical_once()
            except VerticalResolutionError as exc:
                # No route yet (no Manager ticket), or the persisted vertical
                # cannot be loaded in this runtime (typically a community
                # vertical without argus-verticals): hold with the reason and
                # decide again on a later cycle instead of failing the tick.
                self._vertical_resolved = False
                self._hold_on_unresolved_vertical(exc)
                self._enter_pause_backoff()
                return PLAN_ERROR
        if manager_intent:
            state.manager_intent = manager_intent

        reason = (
            ""
            if (
                revision_request is not None
                or state.had_operator_messages
                or state.has_unhandled_operator_input
            )
            else self._bounded_completion_reason()
        )
        if reason:
            return self._emit_bounded_project_completion(reason)
        from ...apps._inbox_delivery import is_host_report

        if any(
            not is_host_report(message)
            for message in getattr(self, "_operator_guidance_carryover", None) or []
        ):
            return PLAN_RETRY
        direct = self._enqueue_bounded_manager_direct(state)
        if direct is not None:
            return direct
        return None


__all__ = ["PlanningCycleIntakeMixin"]
