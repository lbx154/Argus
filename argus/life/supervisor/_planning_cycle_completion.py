"""Planning-cycle phase: handoff handling and project_done normalization.

Covers a Planner turn that adds no work and names who acts next
(Manager-feedback-unresolved check, the Manager handoff, plan-revision
rollback, the recorded handoff), followed by the full-paper-gate
override, the research-target completion gate, revision/project_done
rejection, open-ended vs. plain ``project_done`` handling, and the
degenerate no-new-tasks rejection. All of this runs after the planner has
returned a non-error verdict but before any backlog dedupe/enqueue.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.mission_view import say
from ...core.planner_verdict import PlannerVerdictStatus
from ...core.workspace_facts import fact_text
from ..memory import BacklogItem
from ._constants import (
    COMPLETION_REJECTION_CIRCUIT_THRESHOLD,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
    PROJECT_REPORT_MODEL_ATTEMPTS,
)
from ._planner_failure_backoff import MANAGER_HANDOFF_UNANSWERED
from ._planning_cycle_helpers import (
    _PlanCycleState,
    _research_project_done_issue,
    _staged_goal_completion_issue,
    clear_completion_rejection_circuit,
    completion_rejection_circuit_path,
    goal_gate_task_title,
    pause_completion_rejection_circuit,
    record_completion_rejection,
)

log = logging.getLogger(__name__)


class PlanningCycleCompletionMixin:
    """Handoff handling + project_done normalization + no-tasks rejection."""

    def _bounded_completion_issue(
        self,
        *,
        certificate_verified: bool = False,
    ) -> str:
        """Return why a finite campaign cannot complete authoritatively."""
        backlog = getattr(self.memory, "backlog", None)
        active = getattr(backlog, "active", None)
        live_items = list(active()) if callable(active) else []
        if live_items:
            preview = ", ".join(
                f"{item.id}:{item.status}" for item in live_items[:5]
            )
            return f"live backlog remains ({preview})"
        artifact_root = self._artifact_root()
        from ...core.external_completion_gate import external_completion_gate_issue
        from ...skills.vertical_select import (
            resolve_vertical,
            resolve_workflow_mode,
            vertical_completion_certificate_status,
        )

        vertical = resolve_vertical(artifact_root)
        if not certificate_verified:
            certificate = vertical_completion_certificate_status(
                artifact_root,
                vertical,
            )
            if not certificate.get("ok"):
                return str(
                    certificate.get("reason")
                    or "completion certificate is not current"
                )
        external_issue = external_completion_gate_issue(artifact_root)
        if external_issue:
            return external_issue
        research_issue = (
            _research_project_done_issue(
                artifact_root,
                self.memory.journal.all(),
                current_signature=self._final_submission_signature(),
                evidence_root=self._project_workdir(),
            )
            if resolve_workflow_mode(artifact_root) != "direct"
            else ""
        )
        return research_issue

    def _pc_is_certified_operator_wait(self, state: _PlanCycleState) -> bool:
        """An open-ended Planner hands a certified increment to the operator."""
        verdict = state.verdict
        if (
            not state.planner_invoked
            or state.revision_request is not None
            or not self.config.open_ended
            or verdict.error
            or verdict.handoff != "operator"
            or verdict.project_done
            or verdict.new_tasks
            or verdict.retire_tasks
            or verdict.advance_to_stage
        ):
            return False
        return bool(
            not any(
                item.status not in {"done", "failed", "aborted", "skipped", "superseded"}
                for item in self.memory.backlog.active()
            )
            and self._effective_final_certification_gate(self._artifact_root())
            and self._journal_has_final_certification()
            and self._manager_final_stage_is_completed()
        )

    def _completion_rejection_circuit_file(self) -> Path:
        root = (
            getattr(self.config, "project_state_dir", None)
            or getattr(getattr(self, "memory", None), "root", None)
            or self._artifact_root()
        )
        return completion_rejection_circuit_path(Path(str(root)))

    def _completion_rejection_stop_loss(
        self,
        *,
        stage: str,
        reason: str,
        diagnostic: str,
    ) -> str | None:
        """Stop the completion loop once one reason has turned it back 3 times.

        Every completion attempt is a paid Planner call. When the standing
        requirement and the Planner cannot satisfy each other, the exchange
        repeats verbatim — one live project produced 58 identical turn-backs
        (missing_publishable_reviewer_certification) in 48 hours. Count the
        consecutive same-reason turn-backs durably; at the threshold, tell the
        operator in plain language and stop attempting completion until the
        backlog changes or the operator replies (the intake phase lifts the
        pause — the same wake conditions the feedback hold already uses).
        Returns ``PLAN_TERMINAL_IDLE`` when paused, else ``None``.
        """
        record = record_completion_rejection(
            self._completion_rejection_circuit_file(),
            diagnostic=diagnostic,
            reason=reason,
        )
        count = int(record.get("consecutive_rejections") or 0)
        if count < COMPLETION_REJECTION_CIRCUIT_THRESHOLD:
            return None
        try:
            backlog_signature = self._backlog_planning_signature()
        except Exception:  # noqa: BLE001 - the pause must still engage
            backlog_signature = ""
        pause_completion_rejection_circuit(
            self._completion_rejection_circuit_file(),
            backlog_signature=backlog_signature,
            operator_context_revision=getattr(self, "_planning_operator_context_revision", 0),
        )
        message = say("wrap_up_paused", self._operator_chinese(), count=count, reason=reason)
        try:
            from ...core.operator_messages import publish_operator_message

            publish_operator_message(
                self.memory.root,
                text=message,
                message_id=(
                    "completion-rejection-circuit-"
                    f"{record.get('reason_key')}-{count}"
                ),
                event_fields={
                    "completion_rejection_circuit": True,
                    "stage": stage,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the pause must still engage
            self._emit({
                "type": "life.planner.completion_circuit.notify_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
        self._emit({
            "type": "life.planner.completion_circuit_opened",
            "stage": stage,
            "diagnostic": diagnostic,
            "reason": reason,
            "consecutive_rejections": count,
            "threshold": COMPLETION_REJECTION_CIRCUIT_THRESHOLD,
            "operator_alert": True,
            "text": message,
        })
        self._emit_status(
            f"the same completion requirement has turned the Planner back "
            f"{count} times in a row; pausing completion attempts until the "
            "backlog changes or the operator replies"
        )
        self._enter_idle_backoff()
        return PLAN_TERMINAL_IDLE

    def _manager_project_report_root(self) -> Path:
        """Use the same project conversation as mission-result notifications."""
        return Path(
            getattr(getattr(self.memory, "project", None), "root", None)
            or getattr(self.config, "project_state_dir", None)
            or self.memory.root
        )

    def _manager_project_completion_context(self) -> dict[str, Any]:
        """Separate historical stage decisions from current workspace evidence."""
        from ...core.autonomy import read_autonomous_assumptions
        from ...core.manuscript_snapshot import manuscript_review_status
        from ...core.pipeline_state import read_pipeline_state
        from ...core.stage_certificate import all_stage_reviews
        from ..delivery import (
            MAX_DELIVERY_TARGETS,
            _safe_existing_path,
            _vertical_primary_targets,
        )

        pipeline = read_pipeline_state(self._artifact_root())
        stages = pipeline.get("stages")
        stage_history = pipeline.get("stage_history")
        rollback_history = pipeline.get("rollback_history")
        workdir = Path(self._project_workdir()).resolve()
        report_root = self._manager_project_report_root().resolve()
        project_roots = {workdir, report_root, Path(self._artifact_root()).resolve()}
        reviews = {}
        # Legacy receipts live globally and are keyed only by stage. Never
        # import another project's receipt, or assume an unowned one is ours.
        for root in dict.fromkeys((Path(self.memory.root).resolve(), report_root)):
            for stage, review in all_stage_reviews(root).items():
                owner = str(review.get("project_root") or "").strip()
                if owner:
                    if Path(owner).resolve() not in project_roots:
                        continue
                elif root != report_root:
                    continue
                reviews[stage] = {
                    **review,
                    "reporting_use": "historical_stage_progression_only",
                    "manuscript_freshness": manuscript_review_status(review, workdir),
                }

        targets = _vertical_primary_targets(
            workdir, Path(self._artifact_root()), str(pipeline.get("current_stage") or ""),
        )
        terminal_delivery = self._build_terminal_project_delivery("Project completion report")
        delivered_paths = [row["path"] for row in (terminal_delivery or {}).get("targets", [])]
        paths = list(dict.fromkeys([
            *delivered_paths,
            "paper/main.tex", "paper/main.pdf", "paper/REVIEW.md", "REVIEW.md",
            *(target["path"] for target in targets[:MAX_DELIVERY_TARGETS]),
        ]))
        evidence = []
        for relative in paths:
            safe = _safe_existing_path(workdir, relative)
            if safe is None:
                continue
            path = workdir / safe
            try:
                stat = path.stat()
                row: dict[str, Any] = {
                    "path": safe, "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns,
                }
                # Only named deliverables, never a recursive workspace/log scan.
                with path.open("rb") as handle:
                    raw = handle.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    row["content_status"] = "too_large_for_inline_evidence; inspect read-only"
                if path.suffix.lower() in {".tex", ".md", ".txt", ".json"}:
                    row["excerpt"] = raw[:12000].decode("utf-8", errors="replace")
                    row["excerpt_truncated"] = len(raw) > 12000
                if path.name == "REVIEW.md":
                    row["reporting_use"] = (
                        "current_review_file; its contents alone do not prove "
                        "certification or binding to the current artifacts"
                    )
                evidence.append(row)
            except OSError:
                evidence.append({"path": safe, "content_status": "unreadable"})
        return {
            "project_workdir": str(workdir),
            "project_state_dir": str(report_root),
            "current_stage": str(pipeline.get("current_stage") or ""),
            "stages": dict(stages) if isinstance(stages, dict) else {},
            "stage_history": (
                [dict(row) for row in stage_history if isinstance(row, dict)]
                if isinstance(stage_history, list)
                else []
            ),
            "rollback_history": (
                [dict(row) for row in rollback_history if isinstance(row, dict)]
                if isinstance(rollback_history, list)
                else []
            ),
            "stage_reviews": reviews,
            "stage_ledger_reporting_use": (
                "Historical progression, not evidence of final artifact contents. "
                "Do not reuse old titles, page counts, or numerical results as final."
            ),
            "current_artifact_evidence": evidence,
            # Who accepted the final work and what they verified, in their words.
            "final_delivery_review": {
                key: (terminal_delivery or {}).get(key, "")
                for key in ("review_source", "review_status", "summary")
            },
            # Decisions settled without an operator: the report must name them.
            "autonomous_assumptions": [
                {key: row.get(key) for key in ("conflict", "reading", "source")}
                for row in read_autonomous_assumptions(self._project_state_root())
            ],
            "current_final_certification": {
                "certified": (
                    self._journal_has_final_certification()
                    if self._effective_final_certification_gate(self._artifact_root())
                    else self._manager_final_stage_is_completed()
                ),
                "scope": (
                    "final_submission"
                    if self._effective_final_certification_gate(self._artifact_root())
                    else "vertical_completion"
                ),
                "project_state_signature": self._final_submission_signature(),
            },
        }

    def _manager_publish_project_report(
        self, completion_reason: str, *, certified_increment: bool = False,
        delivery: dict[str, Any] | None = None,
    ) -> str:
        """Have Manager summarize the completed full stage ledger to the operator.

        The same completion, with the same evidence, is reported once: its
        facts and message id are recorded before publishing, so a retry
        reuses the id and finds an already published report. The Manager's
        text is recorded too, so a failed publish never buys a second report.
        A failure alerts the operator once and backs off like an idle cycle.
        """
        stage = str(self._current_pipeline_stage() or "")
        report_root = self._manager_project_report_root()
        context = self._manager_project_completion_context()
        if certified_increment:
            context.update(
                completion_scope="certified_increment",
                standing_objective_active=True,
                final_submission_signature=self._final_submission_signature(),
            )
        facts = fact_text({
            "project_conversation": str(report_root.resolve()),
            "objective": self.config.continuous_objective,
            "reason": completion_reason,
            "context": context,
        })
        record_path = report_root / "manager-project-report.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if not (isinstance(record, dict) and record.get("facts") == facts and record.get("message_id")):
            record = {"facts": facts, "message_id": f"manager-project-report-{uuid.uuid4().hex}"}
            report_root.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        message_id = str(record["message_id"])
        from ...core.transcript import read_turns

        if any(
            turn.get("message_id") == message_id
            for turn in read_turns(report_root)
        ):
            return "reported"

        report = str(record.get("report") or "")
        # A backend that keeps failing gets PROJECT_REPORT_MODEL_ATTEMPTS tries
        # at one completion; then the operator gets the stage summary Argus can
        # state without the model.
        fallback = not report and int(record.get("failures") or 0) >= PROJECT_REPORT_MODEL_ATTEMPTS
        if fallback:
            from ...manager import fallback_project_report

            report = fallback_project_report(context, self.config.continuous_objective)
            self._emit_status(
                "Manager could not write the project completion report after "
                f"{PROJECT_REPORT_MODEL_ATTEMPTS} attempts; sending the stage summary instead"
            )
        elif not report:
            try:
                report = str(self._bound_manager().report_project_completion(
                    completion_context=context,
                    continuous_objective=self.config.continuous_objective,
                    completion_reason=completion_reason,
                    on_event=getattr(self.sink, "handle_event", None),
                ) or "").strip()
            except Exception as exc:  # noqa: BLE001 - retain the outbox for retry
                return self._project_report_failed(record_path, record, f"{type(exc).__name__}: {exc}")
            if not report:
                return self._project_report_failed(
                    record_path, record, "Manager could not produce the project completion report",
                )
            record["report"] = report
            try:
                record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            except OSError:
                log.warning("project report text not recorded; a retry asks the Manager again")
        try:
            from ...core.operator_messages import publish_operator_message

            published = publish_operator_message(
                report_root,
                text=str(report).strip(),
                message_id=message_id,
                event_fields={
                    "manager_project_report": True,
                    "current_stage": stage,
                    # The project's delivery card rides on its one completion message.
                    **({"delivery": delivery, "delivery_id": str(delivery.get("delivery_id") or "")}
                       if isinstance(delivery, dict) else {}),
                },
            )
            if not published and not any(
                turn.get("message_id") == message_id
                for turn in read_turns(report_root)
            ):
                return self._project_report_failed(
                    record_path, record, "Manager project report could not be published",
                )
        except Exception as exc:  # noqa: BLE001 - notification cannot own the verdict
            return self._project_report_failed(record_path, record, f"{type(exc).__name__}: {exc}")
        self._emit({
            "type": "life.manager.project_report",
            "stage": stage,
            "report": str(report).strip(),
            "stage_count": len(context["stages"]),
            "transition_count": len(context["stage_history"]),
            "rollback_count": len(context["rollback_history"]),
            "message_id": message_id,
            **({"fallback": True} if fallback else {}),
        })
        self._clear_manager_planner_feedback()
        return "reported"

    def _project_report_failed(self, record_path: Path, record: dict[str, Any], error: str) -> str:
        """Record a failed completion report, alert once, and back off.

        The outbox keeps the completion, so the report is retried; but only
        after an idle backoff (doubling, capped), never at the daemon's poll
        rate, and the operator hears about it once per report, not per retry.
        """
        self._emit({"type": "life.manager.project_report.failed", "error": error})
        first = not record.get("failures") and getattr(self, "_project_report_alerted", "") != record["message_id"]
        record["failures"] = int(record.get("failures") or 0) + 1
        record["error"] = error
        try:
            record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        except OSError:
            log.warning("project report failure not recorded", exc_info=True)
        if first:
            self._project_report_alerted = record["message_id"]
            self._emit({
                "type": EventType.OPERATOR_ALERT,
                "operator_alert": True,
                "reason": "project_report_failed",
                **self._operator_words("project_report_pending"),
            })
        self._enter_idle_backoff()
        return PLAN_ERROR

    def _manager_final_stage_is_completed(self) -> bool:
        """Whether Manager has already persisted the terminal stage completion."""
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_has_current_completion_certificate,
        )

        root = self._artifact_root()
        return vertical_has_current_completion_certificate(
            root,
            resolve_vertical(root),
        )

    def _pc_complete_terminal_empty_plan(self, state: _PlanCycleState) -> Any:
        verdict = state.verdict
        terminal_signature = self._open_ended_terminal_idle_signature()
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.COMPLETED,
            completion_kind="terminal_stage_hold",
            resume_outcome=PLAN_TERMINAL_IDLE,
            terminal_signature=terminal_signature,
            cycle=self._planning_cycles,
            project_done=False,
            reason=verdict.reason,
            task_count=0,
            enqueued_tasks=0,
            skipped_duplicate_tasks=0,
            enqueued_titles=[],
            skipped_duplicate_titles=[],
            open_ended_objective=True,
        )
        if not delivered:
            return PLAN_RETRY
        state.completion_accepted = True
        self._enter_idle_backoff()
        self._emit_status("planner: terminal stage certified and Manager held; idling")
        return PLAN_TERMINAL_IDLE

    def _pc_handle_handoff(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version

        if not verdict.handoff:
            return None
        if verdict.new_tasks:
            # The tasks run. A stage handed to the Manager is put to it first:
            # a stage it moves leaves these tasks planned against the old one,
            # so plan again under the new stage. A question goes on the card.
            if revision_request is None and self._reconcile_planner_handoff(verdict) in {"hold", "rollback"}:
                return PLAN_RETRY
            if verdict.handoff == "operator":
                self._park_planner_question(
                    state, verdict.operator_question or verdict.reason, verdict.reason,
                    options=verdict.operator_options,
                )
            return None
        feedback = self._load_manager_planner_feedback()
        if feedback is not None:
            if str(feedback.get("diagnostic") or "") == PLANNER_TASKS_FILTERED_DIAGNOSTIC:
                # Filtered-task feedback says every proposed task duplicated
                # live backlog work. Adding nothing answers it: nothing new is
                # schedulable until the live work moves.
                self._clear_manager_planner_feedback()
            else:
                if self._record_manager_feedback_no_progress(
                    "planner handed off instead of revision tasks"
                ):
                    self._emit_status(
                        "planner ignored unresolved Manager feedback; retry later"
                    )
                self._enter_idle_backoff()
                return PLAN_ERROR
        if self._pc_is_certified_operator_wait(state):
            state.certified_operator_wait = True
            state.verdict = replace(verdict, handoff="", project_done=True)
            return self._pc_normalize_project_done(state)
        if revision_request is not None:
            reconciliation_result = self._reconcile_planner_handoff(verdict)
            if reconciliation_result == "rollback":
                superseding_plan_id = f"manager-rollback-{BacklogItem.new_id()}"
                try:
                    result = self.memory.backlog.supersede_active_plan(
                        expected_plan_id=expected_plan_id,
                        expected_version=expected_plan_version,
                        supersede_item_ids=[item.id for item in state.revision_active_items],
                        superseded_by_plan_id=superseding_plan_id,
                        reason=verdict.reason,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit(
                        {
                            "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                            "reason": (
                                "Manager rolled back stage but active plan could "
                                f"not be retired: {type(exc).__name__}: {exc}"
                            ),
                            "expected_plan_id": expected_plan_id,
                            "expected_plan_version": expected_plan_version,
                        }
                    )
                    return PLAN_ERROR
                for item_id in result.superseded_ids:
                    self._emit(
                        {
                            "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                            "item_id": item_id,
                            "plan_id": expected_plan_id,
                            "plan_version": expected_plan_version,
                            "superseded_by_plan_id": superseding_plan_id,
                            "reason": verdict.reason,
                        }
                    )
                self._emit(
                    {
                        "type": "life.plan.revision.rolled_back",
                        "old_plan_id": expected_plan_id,
                        "old_plan_version": expected_plan_version,
                        "superseded_item_ids": list(result.superseded_ids),
                        "reason": verdict.reason,
                    }
                )
                return PLAN_RETRY
            if reconciliation_result == "unanswered":
                return self._pc_handoff_unanswered(state)
            if reconciliation_result:
                return PLAN_RETRY
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "replacement planner handed off instead of planning",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                }
            )
        if revision_request is None:
            reconciliation_result = self._reconcile_planner_handoff(verdict)
            if reconciliation_result == "unanswered":
                return self._pc_handoff_unanswered(state)
            if reconciliation_result:
                return PLAN_RETRY
        return self._record_planner_handoff(verdict, state)

    def _pc_handoff_unanswered(self, state: _PlanCycleState) -> str:
        """The Manager's backend left the Planner's handoff unanswered.

        Argus launched that backend, so the turn failed: the Planner's failure
        streak paces the retry, alerts, and stops it with the operator told.
        """
        state.verdict = replace(
            state.verdict, reason=MANAGER_HANDOFF_UNANSWERED, error=MANAGER_HANDOFF_UNANSWERED,
        )
        self._emit({
            "type": EventType.LIFE_PLANNER_ERROR,
            "cycle": self._planning_cycles,
            "error": MANAGER_HANDOFF_UNANSWERED,
        })
        self._enter_pause_backoff()
        return PLAN_ERROR

    def _pc_normalize_project_done(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version
        planner_declared_done = bool(verdict.project_done)

        def reject_completion(reason: str, diagnostic: str) -> str:
            stage = str(self._current_pipeline_stage() or "")
            if not self._persist_manager_planner_feedback(
                stage=stage,
                reason=reason,
                diagnostic=diagnostic,
            ):
                self._emit_status(
                    "failed to persist completion rejection; retry later"
                )
                return PLAN_ERROR
            self._emit({
                "type": "life.planner.completion_rejected",
                "stage": stage,
                "reason": reason,
                "diagnostic": diagnostic,
            })
            paused = self._completion_rejection_stop_loss(
                stage=stage,
                reason=reason,
                diagnostic=diagnostic,
            )
            if paused is not None:
                return paused
            self._reset_idle_backoff()
            self._emit_status(
                "planner completion rejected; returning the invariant to Planner"
            )
            state.verdict = replace(
                verdict,
                project_done=False,
                reason=reason,
                new_tasks=[],
            )
            return PLAN_RETRY

        if (
            verdict.project_done
            and self._effective_final_certification_gate(self._artifact_root())
            and not self._journal_has_final_certification()
        ):
            return reject_completion(
                "final submission requires an independent certification",
                "final_certification_missing",
            )

        if verdict.project_done:
            research_done_issue = _research_project_done_issue(
                self._artifact_root(),
                self.memory.journal.all(),
                current_signature=self._final_submission_signature(),
                evidence_root=self._project_workdir(),
            )
            if research_done_issue:
                return reject_completion(
                    "Research project completion held: "
                    f"{research_done_issue}. A completed report or bounded cycle "
                    "does not satisfy the persisted research target.",
                    "research_target_incomplete",
                )

        if verdict.project_done:
            from ...core.external_completion_gate import external_completion_gate_issue

            external_gate_issue = external_completion_gate_issue(self._artifact_root())
            if external_gate_issue:
                return reject_completion(
                    f"Project completion held: {external_gate_issue}. The external "
                    "controller alone decides it.",
                    "external_completion_gate_held",
                )

        staged_goal_candidate = bool(
            verdict.project_done
            or (not planner_declared_done and not verdict.handoff and not verdict.new_tasks)
        )
        if staged_goal_candidate and revision_request is None:
            goal_gate_issue = _staged_goal_completion_issue(self._artifact_root())
            if goal_gate_issue:
                return reject_completion(
                    f"{goal_gate_task_title(self._artifact_root())}: "
                    f"{goal_gate_issue}. "
                    "Planner project_done cannot replace stage certification.",
                    "staged_goal_gate_incomplete",
                )

        if revision_request is not None and verdict.project_done:
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "replacement planner cannot declare project_done",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                }
            )
            return PLAN_ERROR

        certified_increment = bool(
            verdict.project_done
            and self.config.open_ended
            and (
                state.certified_operator_wait
                or (
                    not state.had_operator_messages
                    and not state.has_unhandled_operator_input
                )
            )
            and not verdict.new_tasks
            and not verdict.retire_tasks
            and not any(
                item.status not in {"done", "failed", "aborted", "skipped", "superseded"}
                for item in self.memory.backlog.active()
            )
            and self._effective_final_certification_gate(self._artifact_root())
            and self._journal_has_final_certification()
        )
        if verdict.project_done and self.config.open_ended and not certified_increment:
            # A standing campaign cannot be completed by one Planner increment.
            # The Planner class repairs this once before returning, but keep the
            # supervisor guard for injected/fake/custom planners. Stay alive and
            # ask again after backoff instead of recording project_done and then
            # idling until the daemon exits.
            self._enter_pause_backoff()
            self._emit({
                "type": "life.planner.continuation_required",
                "cycle": self._planning_cycles,
                "reason": verdict.reason,
            })
            self._emit_status(
                "planner completed one increment, but the standing objective "
                "remains active; requesting the next delegated task"
            )
            return PLAN_RETRY

        if verdict.project_done:
            if not self._manager_final_stage_is_completed():
                stage = str(self._current_pipeline_stage() or "")
                reason = (
                    f"Project completion cannot be recorded yet: final stage "
                    f"{stage!r} has not been completed by Manager."
                )
                if not self._persist_manager_planner_feedback(
                    stage=stage,
                    reason=reason,
                    diagnostic="manager_final_stage_not_completed",
                ):
                    return PLAN_ERROR
                paused = self._completion_rejection_stop_loss(
                    stage=stage,
                    reason=reason,
                    diagnostic="manager_final_stage_not_completed",
                )
                if paused is not None:
                    return paused
                self._emit_status(reason)
                return PLAN_RETRY
            if not self.config.open_ended:
                bounded_issue = self._bounded_completion_issue(
                    certificate_verified=True,
                )
                if bounded_issue:
                    return reject_completion(
                        f"Bounded project completion held: {bounded_issue}.",
                        "bounded_completion_invariant_failed",
                    )
            if certified_increment:
                reason = (
                    "Independent final certification is current: this increment "
                    "is complete, not the standing campaign. The objective remains "
                    "active until new operator input or changed project state."
                )
                if state.certified_operator_wait and verdict.operator_question:
                    reason += f" The Planner asks: {verdict.operator_question}"
                delivered = self._emit_planner_verdict(
                    status=PlannerVerdictStatus.COMPLETED,
                    completion_kind="certified_increment",
                    resume_outcome=PLAN_TERMINAL_IDLE,
                    terminal_signature=self._open_ended_terminal_idle_signature(),
                    cycle=self._planning_cycles,
                    project_done=False,
                    reason=reason,
                    task_count=0,
                    enqueued_tasks=0,
                    skipped_duplicate_tasks=0,
                    enqueued_titles=[],
                    skipped_duplicate_titles=[],
                    open_ended_objective=True,
                    **(
                        {
                            "handled_operator_context_revision": state.operator_context_revision,
                            "settled_operator_instructions": sorted(
                                state.operator_settlements()
                            ),
                        }
                        if state.certified_operator_wait else {}
                    ),
                )
                if not delivered:
                    return PLAN_RETRY
                state.completion_accepted = True
                clear_completion_rejection_circuit(
                    self._completion_rejection_circuit_file()
                )
                self._enter_idle_backoff()
                self._emit_status(f"planner: {reason}")
                return PLAN_TERMINAL_IDLE
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=False,
                terminal_signature=self._open_ended_terminal_idle_signature(),
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=len(verdict.new_tasks),
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
            )
            if not delivered:
                return PLAN_RETRY
            state.completion_accepted = True
            # The requirement was satisfied for real: forget the turn-back
            # history so a future campaign starts with a clean count.
            clear_completion_rejection_circuit(
                self._completion_rejection_circuit_file()
            )
            self._emit_status(f"planner: project done — {verdict.reason}")
            return False

        state.verdict = verdict
        return None

    def _pc_reject_if_no_tasks(self, state: _PlanCycleState) -> Any | None:
        verdict = state.verdict
        revision_request = state.revision_request
        if verdict.new_tasks or verdict.retire_tasks:
            return None
        if revision_request is not None:
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "planner produced no replacement tasks",
                    "expected_plan_id": state.expected_plan_id,
                    "expected_plan_version": state.expected_plan_version,
                }
            )
        else:
            reconciliation = self._reconcile_open_ended_terminal_stage_action(verdict)
            if reconciliation == "rollback":
                return PLAN_RETRY
            if reconciliation == "hold":
                return self._pc_complete_terminal_empty_plan(state)
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            }
        )
        self._emit_status("planner error: produced no tasks; retry later")
        # No tasks, no handoff, not done: a degenerate no-work cycle.
        # Back off so repeated empty plans cannot spin the daemon.
        self._enter_idle_backoff()
        return PLAN_ERROR


__all__ = ["PlanningCycleCompletionMixin"]
