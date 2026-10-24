"""argus.manager._stage_ops — mixin for stage-transition authority.

``_StageDecisionMixin`` carries the Manager's sole stage-transition authority
(``decide_stage_transition``) and the ``current_stage`` reader.

``decide_stage_transition`` is decomposed into focused private helpers grouped
by phase — gather context, run the model, parse the decision, apply to disk —
so that no method exceeds 350 lines while the full decision logic is preserved
byte-for-byte.

"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import asdict
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..core.handoff import HandOff, HandOffActions
from ..core.role_actions import role_actions
from ._helpers import (
    _manager_model,
    _manager_reasoning_effort,
    gateway_run_exec,
    log,
)
from .stage_decider import stage_check

_log = logging.getLogger(__name__)


_MISSING_REVIEW_FIELD = object()


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class _StageDecisionMixin:
    """Mixin: decide_stage_transition (sole stage authority) + current_stage."""

    # ------------------------------------------------------------------
    # Private helpers — each covers one logical phase of decide_stage_transition
    # ------------------------------------------------------------------

    def _stage_operator_context(self) -> str:
        """What the operator has said, with the answers they gave since the objective was written."""
        from ..core.operator_context import build_operator_context_block
        from ..life.memory import Backlog, operator_answers
        from .control_state import CampaignControlStore

        root = Path(self.manager_session_root)
        context, _revision = build_operator_context_block("manager", root, consume_once=False)
        try:
            backlog = Backlog(root / "backlog.jsonl")
            answers = operator_answers(
                backlog, CampaignControlStore(root).campaign_identity().objective,
            ) if backlog.path.is_file() else []
        except Exception:  # noqa: BLE001 - context only; the decision stands without it
            answers = []
        if not answers:
            return context
        return "\n\n".join(part for part in (
            context,
            "## The operator's answers since the objective\n"
            "Wherever the objective says otherwise, these are the later word.\n"
            + "".join(f"- {answer}\n" for answer in answers),
        ) if part)

    def _stage_context_facts(self, root: Path) -> dict[str, Any]:
        """Bind a verdict to the pipeline and campaign that supplied its evidence."""
        from ..core.pipeline_state import read_pipeline_state
        from .control_state import CampaignControlStore

        control = CampaignControlStore(self.manager_session_root, project_root=root)
        head = control.read_head()
        return {
            "pipeline": read_pipeline_state(root),
            "campaign": asdict(control.campaign_identity()),
            "control_head": asdict(head) if head is not None else None,
            "operator_context": self._stage_operator_context(),
        }

    def _commit_stage_decision_if_current(
        self,
        decision: Any,
        cur: str,
        root: Path,
        *,
        expected_context: dict[str, Any],
        source: str = "manager_llm",
    ) -> "StageTransition":  # noqa: F821
        """Compare and commit under the same lock used by intent replacements."""
        from ..core.pipeline_state import read_pipeline_state
        from ._core import StageTransition

        with self.pipeline_lock():
            if self._stage_context_facts(root) != expected_context:
                current = str(read_pipeline_state(root).get("current_stage") or cur)
                return StageTransition(
                    "hold",
                    current,
                    "project stage, campaign or operator context changed while deciding; recheck "
                    "the current evidence",
                    current_stage=current,
                    source="stale_stage_context_hold",
                    diagnostic="stage_context_changed",
                )
            return self._apply_stage_decision_to_disk(decision, cur, root, source=source)

    @staticmethod
    def _is_clean_reviewer_acceptance(review: Any) -> bool:
        """Whether ``review`` can be committed without another semantic vote.

        An accepting verdict's note is its record, not a request for more work.
        Missing or malformed control fields are deliberately not normalized here:
        ``hand_off`` normally supplies every one, so an incomplete object
        is ambiguous evidence and must fall through to the Manager model.
        """

        def field(name: str) -> Any:
            return getattr(review, name, _MISSING_REVIEW_FIELD)

        status = field("status")
        reason = field("reason")
        operator_question = field("operator_question")
        operator_options = field("operator_options")
        review_source = field("review_source")
        backend_unavailable = field("backend_unavailable")
        backend_fatal_error = field("backend_fatal_error")
        session_signal = field("session_signal")

        return (
            isinstance(status, str)
            and status.strip().lower() == "done"
            and isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(operator_question, str)
            and not operator_question.strip()
            and isinstance(operator_options, (list, tuple))
            and not operator_options
            and isinstance(review_source, str)
            and review_source.strip().lower() == "reviewer"
            and isinstance(backend_unavailable, bool)
            and not backend_unavailable
            and isinstance(backend_fatal_error, str)
            and not backend_fatal_error.strip()
            and isinstance(session_signal, dict)
            and not session_signal
        )

    def _gather_stage_context(
        self,
        root: Path,
    ) -> "tuple[str, list[str], Any] | StageTransition":  # noqa: F821
        """Phase 1: fetch current stage, stage order, and checklist contract.

        Returns either a 3-tuple ``(cur, order, checklist_contract)`` on success,
        or a ``StageTransition(hold, ...)`` when the required checklist is not loaded
        (short-circuit before any model call).
        """
        from ..skills.stage_machine import (
            active_vertical_checklist_defs as _vertical_defs,
        )
        from ..skills.stage_machine import current_stage as _current_stage
        from ..skills.stage_machine import (
            resolve_stage_checklist_contract as _resolve_checklist_contract,
        )
        from ._core import StageTransition

        cur = _current_stage(root)

        try:
            raw_order, _items = _vertical_defs(root)
            order = [str(s).strip().lower() for s in raw_order]
        except Exception:  # noqa: BLE001
            log.debug("manager stage-order lookup failed", exc_info=True)
            order = []
        checklist_contract = _resolve_checklist_contract(
            cur,
            project_root=root,
        )
        checklist_state = checklist_contract.state.value
        if (
            not checklist_contract.checklist_optional
            and checklist_state != "loaded"
        ):
            return StageTransition(
                "hold",
                cur,
                f"required checklist is {checklist_state or 'not_loaded'}",
                current_stage=cur,
                source="checklist_configuration_hold",
                diagnostic=f"required_checklist_{checklist_state or 'not_loaded'}",
            )
        return cur, order, checklist_contract

    def _build_stage_run_exec(
        self,
        run_exec: Any,
        on_event: Any,
        *,
        run_label: str = "manager-stage",
    ) -> "tuple[Any, StageTransition | None]":  # noqa: F821
        """Phase 2: build the LLM caller (with cost metering) if not supplied.

        Returns ``(wrapped_run_exec, None)`` on success or
        ``(None, StageTransition(hold, ...))`` when no backend is available.
        ``run_label`` names the call in the event stream and the cost ledger.
        """
        from ._core import StageTransition

        if run_exec is not None:
            return run_exec, None
        if self.runner is None:
            return None, StageTransition(
                "hold", "", "no manager backend", current_stage="",
                source="no_runner_hold",
            )
        from ..core.models import RunnerOptions

        _backend = self.runner or self._session
        # A sandboxed backend reads only what it is given: the Skill libraries
        # a Manager prompt names are read where they lie.
        skill_dirs = [str(root) for root in self.mission.libraries().library_roots]

        def _run_exec(prompt: str, actions: HandOffActions | None = None) -> Any:  # noqa: ANN401
            options = RunnerOptions(
                model=_manager_model(),
                reasoning_effort=_manager_reasoning_effort(),
                working_dir=str(self.execution_workdir),
                dangerous_yolo=False,
                sandbox_mode="read-only",
                skip_git_repo_check=True,
                add_dirs=skill_dirs or None,
            )
            with role_actions(_backend, options, actions) if actions else nullcontext(options) as bound:
                return gateway_run_exec(_backend, prompt=prompt, options=bound, run_label=run_label)

        # F3: meter each manager-stage codex turn so its tokens fold into
        # the per-mission cost sink + the daily cap — they were previously
        # invisible. Fail-soft.
        from ..core.cost_events import metered_run_exec
        try:
            _mmodel = _manager_model()
        except Exception:  # noqa: BLE001
            _mmodel = ""
        _run_exec = metered_run_exec(
            _run_exec, on_event, layer="manager", model=_mmodel,
            run_label=run_label,
        )
        return _run_exec, None

    def run_turn(
        self,
        prompt: str,
        *,
        on_event: Any = None,
        run_label: str = "manager-prose",
        root_task_id: str | None = None,
    ) -> str:
        """Run one read-only Manager turn and return its whole reply.

        The turn runs read-only in the project directory so the model can open
        files, is metered like any other Manager call, and returns an empty
        string when no backend is available or the model wrote nothing.
        """
        run_exec, hold = self._build_stage_run_exec(None, on_event, run_label=run_label)
        if hold is not None or run_exec is None:
            return ""
        return self._run_stage_model(run_exec, prompt, root_task_id)

    def write_prose(
        self,
        prompt: str,
        *,
        on_event: Any = None,
        run_label: str = "manager-prose",
        root_task_id: str | None = None,
    ) -> str:
        """The prose of one Manager turn (:meth:`run_turn`): a letter to the operator."""
        return self.run_turn(
            prompt, on_event=on_event, run_label=run_label, root_task_id=root_task_id,
        ).strip()

    def _run_stage_model(self, run_exec: Any, prompt: str, root_task_id: str | None) -> str:
        """Run one Manager turn in the task's usage scope; its reply, or '' when it wrote none."""
        with self._task_usage_scope(root_task_id):
            return run_exec(prompt).last_agent_message or ""

    def _finalize_stage_decision(
        self,
        handoff: HandOff | None,
        cur: str,
        order: list[str],
        review: Any,
        open_ended: bool,
        mission_scope: str,
        planner_handoff: bool,
        checklist_contract: Any,
        root: Path,
    ) -> Any:
        """Phase 4: hold when nothing was handed off, then put the decision to the gates.

        Returns a ``StageDecision`` (action, target_stage, reason, diagnostic).
        """
        from ..core.external_completion_gate import external_completion_gate_issue
        from ..core.research_contract import resolve_research_target_level
        from ..skills.vertical_select import (
            resolve_vertical,
            resolve_workflow_mode,
        )
        from .stage_decider import (
            StageDecision,
            completion_trigger_reason,
            external_completion_gate_rework_decision,
            external_completion_gate_stage_guard_decision,
            final_stage_completion_blockers,
            final_stage_completion_decision,
        )

        decision = handoff.value if handoff else StageDecision(
            "hold", cur, "manager held: no stage decision was handed off", "handoff_missing",
        )

        _completion_vertical = resolve_vertical(root)
        _research_target_level = resolve_research_target_level(root)
        # Direct mode is the operator's explicit one-package deliverable. It may
        # complete without traversing unrelated later stages, including an
        # Idea-only Research request. Paper production is routed as staged.
        _allow_early_completion = (
            not open_ended
            and resolve_workflow_mode(root) == "direct"
        )
        _completion_blockers = [
            blocker
            for blocker in (external_completion_gate_issue(self.execution_workdir),)
            if blocker
        ]
        if (
            _research_target_level in {"publishable", "doctoral"}
            and not _allow_early_completion
        ):
            from ..verticals._base import load_vertical_contract

            # Final completion must re-run the active provider's own strongest
            # stage validator, but Manager must not import a named vertical to
            # do it. The contract keeps this path domain-blind and also lets
            # project-local/plugin research providers enforce equivalent gates.
            _completion_blockers.extend(
                load_vertical_contract(_completion_vertical, project_root=root).completion_issues(
                    stage=order[-1],
                    project_root=self.execution_workdir,
                    state_root=root,
                )
            )
        _completion_blocker = "; ".join(_completion_blockers)
        if decision.action == "complete":
            final_decision = final_stage_completion_decision(
                review,
                current_stage=cur,
                stage_order=order,
                vertical=_completion_vertical,
                mission_scope=mission_scope,
                project_root=root,
                research_target_level=_research_target_level,
                checklist_contract=checklist_contract,
                completion_blocker=_completion_blocker,
                trigger_diagnostic=decision.diagnostic,
                trigger_reason=completion_trigger_reason(
                    decision.action,
                    decision.reason,
                ),
                allow_early_completion=_allow_early_completion,
            )
            if final_decision is not None:
                decision = final_decision
            else:
                # Report which of the checks refused, so the Planner repairs
                # what is actually missing.
                detail = "; ".join(final_stage_completion_blockers(
                    review,
                    current_stage=cur,
                    stage_order=order,
                    vertical=_completion_vertical,
                    mission_scope=mission_scope,
                    project_root=root,
                    research_target_level=_research_target_level,
                    checklist_contract=checklist_contract,
                    completion_blocker=_completion_blocker,
                    allow_early_completion=_allow_early_completion,
                ))
                decision = StageDecision(
                    "hold",
                    cur,
                    (
                        f"Manager completion rejected: {detail}"
                        if detail
                        else "Manager completion rejected by the project "
                        "completion contract"
                    ),
                    "manager_completion_rejected",
                )
        rework_decision = external_completion_gate_rework_decision(
            review,
            current_stage=cur,
            stage_order=order,
            project_root=self.execution_workdir,
        )
        if rework_decision is not None:
            decision = rework_decision
        decision = external_completion_gate_stage_guard_decision(
            review,
            decision,
            current_stage=cur,
            stage_order=order,
            project_root=self.execution_workdir,
        )

        if decision.action == "rollback":
            from ..skills.stage_machine import stage_rollback_error

            if error := stage_rollback_error(root):
                decision = StageDecision("hold", cur, str(error), error.diagnostic)

        if (
            planner_handoff
            and (not open_ended or resolve_workflow_mode(root) != "direct")
            and decision.action in {"advance", "complete"}
        ):
            decision = StageDecision(
                "hold",
                cur,
                "a Planner handoff cannot advance without reviewer evidence",
                "planner_handoff_advance_rejected",
            )
        return decision

    def _apply_stage_decision_to_disk(
        self,
        decision: Any,
        cur: str,
        root: Path,
        *,
        source: str = "manager_llm",
    ) -> "StageTransition":  # noqa: F821
        """Phase 5: write the chosen action to ``PIPELINE_STATE.json`` and return a
        ``StageTransition`` describing what happened."""
        from ..skills.stage_machine import StageCompletionError, StageRollbackError
        from ..skills.stage_machine import (
            advance_stage as _advance,
        )
        from ..skills.stage_machine import (
            complete_final_stage as _complete,
        )
        from ..skills.stage_machine import (
            rollback_stage as _rollback,
        )
        from ._core import StageTransition

        if decision.action == "advance":
            try:
                _advance(root, target_stage=decision.target_stage,
                         reason=decision.reason, advanced_by="manager",
                         evidence_root=self.execution_workdir)
            except StageCompletionError as exc:
                return StageTransition(
                    "hold", cur, str(exc), current_stage=cur,
                    source="stage_completion_gate_hold",
                    diagnostic="stage_completion_gate_failed",
                )
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal advance target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("advance", decision.target_stage, decision.reason,
                                   cur, source, decision.diagnostic)

        if decision.action == "complete":
            from ..skills.vertical_select import resolve_workflow_mode

            try:
                # ``allow_early_completion`` is re-derived here rather than
                # threaded from the decision: this is a backstop at the
                # primitive, not the authority. ``final_stage_completion_decision``
                # already applied the stricter test (it also requires the mission
                # not be open-ended); a decision that reached this line has
                # passed it. What this argument stops is the path that never
                # went through the decider at all — see ``complete_final_stage``.
                _complete(root, reason=decision.reason, completed_by="manager",
                          evidence_root=self.execution_workdir,
                          allow_early_completion=(
                              resolve_workflow_mode(root) == "direct"
                          ))
            except StageCompletionError as exc:
                return StageTransition(
                    "hold", cur, str(exc), current_stage=cur,
                    source="stage_completion_gate_hold",
                    diagnostic="stage_completion_gate_failed",
                )
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal final-stage completion", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("complete", decision.target_stage, decision.reason,
                                   cur, source, decision.diagnostic)

        if decision.action == "rollback":
            try:
                _rollback(root, target_stage=decision.target_stage,
                          reason=decision.reason, rolled_back_by="manager",
                          evidence_root=self.execution_workdir)
            except StageRollbackError as exc:
                return StageTransition(
                    "hold", cur, str(exc), current_stage=cur,
                    source="illegal_target_hold", diagnostic=exc.diagnostic,
                )
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("rollback", decision.target_stage, decision.reason,
                                   cur, source, decision.diagnostic)

        return StageTransition("hold", cur, decision.reason or "manager held",
                               cur, source, decision.diagnostic)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def decide_stage_transition(
        self,
        *,
        review: Any = None,
        planner_verdict: Any = None,
        project_root: Path | str | None = None,
        run_exec: Any = None,
        on_event: Any = None,
        root_task_id: str | None = None,
        open_ended: bool = False,
        continuous_objective: str = "",
        mission_scope: str = "",
        stage_closing: bool = False,
    ) -> "StageTransition":  # noqa: F821
        """Independently decide advance / hold / rollback / complete for the stage,
        then WRITE it. The Manager is the SOLE writer of
        ``current_stage`` — the reviewer/planner only ADVISE (via ``review`` /
        ``planner_verdict``); the engineer never edits stage state.

        THICK: the Manager makes its own LLM judgment from the reviewer's
        structured feedback + the current-stage checklist, parses a strict JSON
        verdict, and on advance/rollback calls
        :func:`stage_machine.advance_stage` / ``rollback_stage``.

        Fail-safe — writes NOTHING and returns a HOLD when: ``review is None``
        (no feedback → never advance), there is no backend, the LLM/parse errors,
        or the model picks an illegal target. A HOLD simply leaves the stage put;
        the mission/planner loop continues, so the daemon never deadlocks.
        """
        from ._core import StageTransition

        root = Path(project_root) if project_root is not None else self.project_root

        # --- Phase 1: Gather context (may return early on config hold) ---
        with self.pipeline_lock():
            ctx = self._gather_stage_context(root)
            if isinstance(ctx, StageTransition):
                return ctx
            expected_context = self._stage_context_facts(root)
        cur, order, checklist_contract = ctx

        if (
            getattr(review, "engineer_aborted_before_review", False) is True
            and getattr(review, "backend_stop_kind", None) == "operator_abort"
        ):
            return StageTransition(
                "hold",
                cur,
                "engineer was operator-aborted before review",
                current_stage=cur,
                source="operator_abort_hold",
                diagnostic="engineer_aborted_before_review",
            )

        # --- Phase 2: Compute reconciliation flags ---
        # An open-ended final-stage checkpoint may need a new solve cycle after
        # the Planner confirms the operator's objective is still unresolved.
        # The Manager remains the sole rollback authority; the Planner only
        # supplies the advisory reason.
        open_ended_terminal_reconciliation = bool(
            open_ended
            and planner_verdict is not None
            and order
            and cur == order[-1]
        )
        planner_handoff = bool(
            review is None
            and planner_verdict is not None
            and getattr(planner_verdict, "handoff", "") == "manager"
            and not bool(getattr(planner_verdict, "project_done", False))
            and not list(getattr(planner_verdict, "new_tasks", []) or [])
        )

        # --- Phase 4: Handle no-review (early hold or build synthetic review) ---
        # No reviewer feedback normally means no stage transition. Structured
        # open-ended terminal reconciliation and a Planner handoff are the exceptions.
        if review is None:
            if not (
                open_ended_terminal_reconciliation
                or planner_handoff
            ):
                return StageTransition(
                    "hold", cur, "no reviewer feedback", current_stage=cur,
                    source="no_review_hold",
                )
            planner_reason = str(
                getattr(planner_verdict, "summary", "") or planner_verdict
            )
            if planner_handoff:
                review = SimpleNamespace(
                    status="blocked",
                    reason=(
                        "The Planner reports no dispatchable current-stage work "
                        "and requests a stage-authority decision. "
                        f"Planner advisory: {planner_reason}"
                    ),
                )
            else:
                review = SimpleNamespace(
                    status="done",
                    reason=(
                        "The final-stage checkpoint is reviewer-certified, but the "
                        "open-ended campaign objective remains unresolved. "
                        f"Planner advisory: {planner_reason}"
                    ),
                )

        if cur == "experiment":
            from ..skills.vertical_select import resolve_vertical, resolve_workflow_mode

            if (
                resolve_vertical(root) == "research"
                and resolve_workflow_mode(root) == "staged"
                and (
                    open_ended
                    or bool(continuous_objective.strip())
                    or planner_verdict is not None
                )
                and (
                    getattr(review, "status", "") != "done"
                    or getattr(planner_verdict, "advance_to_stage", "") != "paper"
                )
            ):
                return StageTransition(
                    "hold",
                    cur,
                    "Keep Experiment open for Planner's post-result scale assessment. "
                    "After Reviewer accepts the current experiment, Planner must "
                    "compare its training and evaluation coverage with the operator "
                    "objective, expand under the benchmark policy if insufficient, or "
                    "request ADVANCE_TO_STAGE=paper with the adequacy rationale.",
                    current_stage=cur,
                    source="planner_scale_assessment_hold",
                    diagnostic="experiment_scale_assessment_required",
                )

        from ..core.venue_review import current_venue_acceptance_issue, requires_venue_review
        from ..skills.vertical_select import resolve_vertical

        if requires_venue_review(
            vertical=resolve_vertical(root), stage=cur, scope=mission_scope,
            artifact_root=self.execution_workdir,
        ) and str(getattr(review, "status", "")) == "done":
            acceptance_issue = current_venue_acceptance_issue(
                review, state_root=root, artifact_root=self.execution_workdir,
            )
            if acceptance_issue:
                return StageTransition(
                    "hold", cur, acceptance_issue, current_stage=cur,
                    source="venue_acceptance_hold", diagnostic="final_venue_acceptance_required",
                )

        manuscript_binding = getattr(review, "manuscript_snapshot", None)
        if isinstance(manuscript_binding, dict):
            try:
                from ..core.manuscript_snapshot import (
                    manuscript_review_status,
                )

                freshness = manuscript_review_status(
                    {"manuscript_snapshot": manuscript_binding},
                    self.execution_workdir,
                )
            except Exception:  # noqa: BLE001 - a bound review fails closed
                freshness = {
                    "status": "unbound",
                    "message": "unbound (reviewed manuscript cannot be read)",
                }
            if freshness.get("status") != "current":
                return StageTransition(
                    "hold",
                    cur,
                    str(freshness.get("message") or "stale manuscript review"),
                    current_stage=cur,
                    source="stale_manuscript_review_hold",
                    diagnostic="reviewed_manuscript_version_mismatch",
                )

        # A parsed, conflict-free Reviewer acceptance is already the semantic
        # judgment for a stage. Manager still performs the exact stage-machine
        # preflight and remains the sole writer; only its duplicate model vote is
        # skipped. Every ambiguous signal continues through the model path below.
        next_stage = (
            order[order.index(cur) + 1]
            if cur in order and order.index(cur) + 1 < len(order)
            else ""
        )
        terminal_stage = bool(order and cur == order[-1])
        external_gate_issue = ""
        if next_stage or terminal_stage:
            from ..core.external_completion_gate import external_completion_gate_issue

            external_gate_issue = external_completion_gate_issue(
                self.execution_workdir
            )
        deterministic_candidate = bool(
            stage_closing
            and (next_stage or terminal_stage)
            and self._is_clean_reviewer_acceptance(review)
            and planner_verdict is None
            and not external_gate_issue
        )
        if deterministic_candidate:
            try:
                from ..skills.stage_machine import _ensure_stage_completion
                from ..skills.vertical_select import (
                    resolve_vertical,
                    resolve_workflow_mode,
                )
                from .stage_decider import StageDecision

                allow_early_completion = (
                    not open_ended
                    and resolve_workflow_mode(root) == "direct"
                )
                if not (allow_early_completion and not terminal_stage):
                    _ensure_stage_completion(
                        root,
                        cur,
                        evidence_root=self.execution_workdir,
                    )
                if terminal_stage or allow_early_completion:
                    from ..core.research_contract import resolve_research_target_level
                    from .stage_decider import final_stage_completion_decision

                    decision = final_stage_completion_decision(
                        review,
                        current_stage=cur,
                        stage_order=order,
                        vertical=resolve_vertical(root),
                        mission_scope=mission_scope,
                        project_root=root,
                        research_target_level=resolve_research_target_level(root),
                        checklist_contract=checklist_contract,
                        trigger_diagnostic="deterministic_reviewer_done",
                        # Direct mode gives the Reviewer no stage checklist
                        # (see roles/prompts/reviewer.py), so the recorded
                        # reason must not claim one was certified.
                        trigger_reason=(
                            "Reviewer accepted the direct task (direct workflow "
                            "carries no stage checklist) and deterministic "
                            "completion checks passed"
                            if allow_early_completion
                            else "Reviewer certified the current-stage checklist and "
                            "deterministic completion checks passed"
                        ),
                        allow_early_completion=allow_early_completion,
                    )
                else:
                    decision = StageDecision(
                        "advance",
                        next_stage,
                        "Reviewer certified the current-stage checklist and "
                        "deterministic completion checks passed",
                        "deterministic_reviewer_done",
                    )
                if decision is not None:
                    return self._commit_stage_decision_if_current(
                        decision,
                        cur,
                        root,
                        expected_context=expected_context,
                        source="manager_deterministic",
                    )
            except Exception:  # noqa: BLE001 - ambiguity retains Manager semantics
                log.debug(
                    "deterministic stage advance preflight failed; using Manager",
                    exc_info=True,
                )

        # --- Phase 5: Build the LLM caller ---
        run_exec, hold = self._build_stage_run_exec(run_exec, on_event)
        if hold is not None:
            return StageTransition(
                hold.action, cur, hold.reason, current_stage=cur,
                source=hold.source,
            )

        # --- Phase 6–7: Run model, parse, finalize (wrapped in fail-safe) ---
        try:
            cur_idx = order.index(cur) if cur in order else -1
            later_stages = order[cur_idx + 1 :] if 0 <= cur_idx < len(order) - 1 else []
            earlier = order[:cur_idx] if cur_idx > 0 else []
            from ..roles.prompts import resolve_role_prompt
            from ..roles.prompts.manager import (
                assemble_manager_prompt,
                build_stage_decision_prompt,
                stage_decision_request,
            )
            from ..skills.stage_machine import stage_rollback_error
            from ..skills.vertical_select import resolve_workflow_mode

            allow_direct_completion = (
                not open_ended and resolve_workflow_mode(root) == "direct"
            )

            prompt_context = resolve_role_prompt(
                stage_decision_request(root, stage=cur)
            )
            prompt = assemble_manager_prompt(
                build_stage_decision_prompt(
                    current_stage=cur,
                    next_stage=next_stage,
                    later_stages=later_stages,
                    earlier_stages=earlier,
                    checklist_md=prompt_context.stage_checklist,
                    review=review,
                    planner_verdict=planner_verdict,
                    open_ended=open_ended,
                    continuous_objective=continuous_objective,
                    allow_rollback=stage_rollback_error(root) is None,
                    allow_early_completion=allow_direct_completion,
                ),
                role_banner=prompt_context.role_banner,
                role_skill_block=self.mission.libraries().block,
                role_context=prompt_context.role_context,
            )
            from ..core.operator_context import append_operator_context

            prompt = append_operator_context(prompt, self._stage_operator_context())

            actions = HandOffActions("stage", check=partial(
                stage_check, current_stage=cur, stage_order=order, allow_done=allow_direct_completion,
            ))
            with self._task_usage_scope(root_task_id):
                run_exec(prompt, actions)
            decision = self._finalize_stage_decision(
                actions.handoff,
                cur=cur,
                order=order,
                review=review,
                open_ended=open_ended,
                mission_scope=mission_scope,
                planner_handoff=planner_handoff,
                checklist_contract=checklist_contract,
                root=root,
            )
        except Exception:  # noqa: BLE001 — any failure → safe HOLD, write nothing
            log.debug("manager stage decision failed", exc_info=True)
            return StageTransition(
                "hold", cur, "manager decision error", current_stage=cur,
                source="failsafe_hold", diagnostic="exception",
            )

        # --- Phase 8: Apply decision to disk ---
        transition = self._commit_stage_decision_if_current(
            decision, cur, root, expected_context=expected_context
        )
        if transition.diagnostic == "manager_question":
            self._put_stage_question(root, cur, order, transition.reason, on_event, root_task_id)
        return transition

    def _put_stage_question(
        self, root: Path, cur: str, order: list[str], question: str, on_event: Any, root_task_id: str | None,
    ) -> None:
        """Put the stage call's question to the Manager session now; the stage holds meanwhile.

        The task the question came from is kept in PIPELINE_STATE, so the
        ruling's choices are that task's even when it ends before the ruling.
        The question waits there too, for the Manager's next turn, when no
        web server owns its session (CLI-only use).
        """
        from hashlib import sha256

        from ..core.pipeline_state import read_pipeline_state, write_pipeline_state
        from ..core.team_reports import push_to_manager
        from ..life.memory import LifeMemory

        running = [item.id for item in LifeMemory.open(root).backlog.active() if item.status == "running"]
        state = read_pipeline_state(root)
        at = order.index(cur) if cur in order else len(order)
        text = "\n\n".join((
            f"Stage question at `{cur}` (later stages: {', '.join(order[at + 1:]) or 'none'}; "
            f"earlier: {', '.join(order[:at]) or 'none'}):",
            question,
            "The team holds this work: change no project files in this turn.",
            "The operator asked to take part in stage decisions."
            if state.get("operator_decides_stages")
            # The operator's acceptance stays theirs (OperatorContext): a
            # Manager told it could read their request for them picked the
            # reading, did the work and said it was done before they
            # answered the same question (k1-split-cents, 2026-10-24).
            else "The operator did not ask to take part in stage decisions: the stage move is yours. "
            "When the question is which reading of their requirement to deliver, and the readings "
            "deliver different behavior, it stays theirs: ask them (to=operator, the readings as "
            "options); the task waits on their answer and resumes with it.",
        ))
        with self.pipeline_lock():
            write_pipeline_state(root, {
                **read_pipeline_state(root), "stage_question": text,
                "stage_question_task": running[0] if running else root_task_id or "",
            })
        message_id = f"stage-question-{sha256(text.encode()).hexdigest()[:12]}"
        if not push_to_manager(root, message_id=message_id, text=text) and callable(on_event):
            on_event({"type": "life.status", "text": "stage question waits for the Manager's next turn"})

    def stage_ruling(self, payload: dict[str, Any]) -> Any:
        """The Manager session's ruling on a stage question, checked against the stage as it is now."""
        root = self.project_root
        context = self._gather_stage_context(root)
        if not isinstance(context, tuple):
            raise ValueError(f"The stage cannot move now: {context.reason}.")
        cur, order, _contract = context
        return stage_check(
            {"to": "team", **payload["stage"], "summary": payload["summary"]},
            current_stage=cur, stage_order=order, allow_done=False,
        )

    def commit_stage_ruling(self, payload: dict[str, Any]) -> str:
        """Carry out the Manager session's stage ruling; what the team is told of it, choices included.

        The choices are recorded for the task whose stage question this rules
        on; the caller puts the returned text in the team's inbox.
        """
        from ..core.autonomy import record_assumptions
        from ..core.pipeline_state import read_pipeline_state, write_pipeline_state
        from ..skills.stage_machine import current_stage

        root = self.project_root
        with self.pipeline_lock():
            decision = self.stage_ruling(payload)
            task_id = str(read_pipeline_state(root).get("stage_question_task") or "")
            transition = self._apply_stage_decision_to_disk(
                decision, current_stage(root), root, source="manager_ruling",
            )
            state = read_pipeline_state(root)
            state.pop("stage_question_task", None)
            state.pop("stage_question", None)
            write_pipeline_state(root, state)
        record_assumptions(root, payload["assumptions"], item_id=task_id, source="stage")
        return "\n".join((
            f"Manager's stage ruling: {transition.action} at `{transition.target_stage}`. {decision.reason}",
            *(f"- chose {row['chose']} (not: {row['instead_of']})" for row in payload["assumptions"]),
        ))

    # ---- progress view ----
    def current_stage(self) -> str:
        """Which Stage the engine is on now (read from PIPELINE_STATE.json)."""
        from ..core.pipeline_state import read_pipeline_state

        try:
            state = read_pipeline_state(self.project_root)
            return str(state.get("current_stage") or "") or self.plan_stages(
                self._resolve_vertical_for_current_stage()
            )[0]
        except Exception:  # noqa: BLE001
            return ""

    def _resolve_vertical_for_current_stage(self) -> str:
        from ..skills.vertical_select import resolve_vertical
        return resolve_vertical(self.project_root)
