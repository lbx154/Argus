"""argus.manager._stage_ops — the completion gate, and the Manager's read-only turns.

``_StageDecisionMixin`` carries the completion gate (``decide_stage_transition``):
the one owner of "stage ready" and "project done", which reads evidence and
asks no model. It also runs the Manager's read-only prose turns
(``run_turn`` / ``write_prose``) and reads the current stage.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core.handoff import HandOffActions
from ..core.role_actions import role_actions
from ._helpers import (
    _manager_model,
    _manager_reasoning_effort,
    gateway_run_exec,
    log,
)

_log = logging.getLogger(__name__)


_MISSING_REVIEW_FIELD = object()


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class _StageDecisionMixin:
    """Mixin: the completion gate (decide_stage_transition), prose turns, current_stage."""

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
            transition = self._apply_stage_decision_to_disk(decision, cur, root, source=source)
        if transition.action in {"advance", "rollback"}:
            self._tell_stage_move(root, cur, transition.target_stage, transition.action)
        return transition

    def _tell_stage_move(self, root: Path, cur: str, target: str, action: str) -> None:
        """The Manager tells the person what the gate did, in one short paragraph of their language."""
        import time

        from ..core.mission_view import load_mission_view, say, session_is_chinese, stage_label
        from ..core.team_reports import show_team_message

        try:
            chinese = session_is_chinese(load_mission_view(root))
            text = (
                say("stage_moved", chinese, from_stage=stage_label(cur, chinese), to_stage=stage_label(target, chinese))
                if action == "advance"
                else say("stage_reopened", chinese, to_stage=stage_label(target, chinese))
            )
            show_team_message(
                root, text=text, message_id=f"stage-{action}-{cur}-{target}-{int(time.time())}",
                event_fields={"stage_move": True, "action": action, "from_stage": cur, "target_stage": target},
            )
        except Exception:  # noqa: BLE001 - telling never undoes the committed move
            log.warning("could not tell the operator about the stage move", exc_info=True)

    @staticmethod
    def _is_clean_reviewer_acceptance(review: Any) -> bool:
        """Whether ``review`` can be committed without another semantic vote.

        An accepting verdict's note is its record, not a request for more work.
        A verdict recovered from an older record may lack the control fields a
        ``hand_off`` supplies; a missing one reads as unset, while a present but
        malformed one is not an acceptance.
        """
        unset = {
            "operator_question": "", "operator_options": [], "backend_unavailable": False,
            "backend_fatal_error": "", "session_signal": {},
        }

        def field(name: str) -> Any:
            value = getattr(review, name, _MISSING_REVIEW_FIELD)
            return unset.get(name, value) if value is _MISSING_REVIEW_FIELD else value

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
            # The Reviewer's acceptance, or the Engineer's own where the task
            # required no review (its form refuses ``done`` otherwise).
            and review_source.strip().lower() in {"reviewer", "engineer_self_review"}
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

    def _gate_completion(
        self,
        decision: Any,
        cur: str,
        order: list[str],
        review: Any,
        open_ended: bool,
        mission_scope: str,
        checklist_contract: Any,
        root: Path,
        judged_item_id: str = "",
    ) -> Any:
        """Put a stage move the evidence supports to the completion checks.

        ``complete`` passes only when every completion check passes (the final
        stage, the person's required steps, the vertical's completion issues,
        an external gate, the certifying review); otherwise it holds and names
        each check that refused, so the Planner repairs what is missing. An
        external gate that blocks completion reopens its rework stage.
        """
        from ..core.external_completion_gate import external_completion_gate_issue
        from ..core.research_contract import resolve_research_target_level
        from ..skills.vertical_select import (
            resolve_vertical,
            resolve_workflow_mode,
        )
        from .stage_decider import (
            StageDecision,
            external_completion_gate_rework_decision,
            external_completion_gate_stage_guard_decision,
            final_stage_completion_blockers,
            final_stage_completion_decision,
            required_steps_blocker,
        )

        if decision.action == "complete":
            # Read only for a completion: an advance or a hold never reaches them.
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
                for blocker in (
                    external_completion_gate_issue(self.execution_workdir),
                    required_steps_blocker(root, judged_item_id),
                )
                if blocker
            ]
            if (
                _research_target_level in {"publishable", "doctoral"}
                and not _allow_early_completion
            ):
                from ..verticals._base import load_vertical_contract

                # Final completion re-runs the active provider's own strongest
                # stage validator through the vertical contract, so the gate stays
                # domain-blind and project-local research providers gate it too.
                _completion_blockers.extend(
                    load_vertical_contract(_completion_vertical, project_root=root).completion_issues(
                        stage=order[-1],
                        project_root=self.execution_workdir,
                        state_root=root,
                    )
                )
            _completion_blocker = "; ".join(_completion_blockers)
            checks = dict(
                current_stage=cur,
                stage_order=order,
                vertical=_completion_vertical,
                mission_scope=mission_scope,
                project_root=root,
                research_target_level=_research_target_level,
                checklist_contract=checklist_contract,
                completion_blocker=_completion_blocker,
                allow_early_completion=_allow_early_completion,
            )
            final_decision = final_stage_completion_decision(
                review, **checks,
                trigger_diagnostic=decision.diagnostic,
                trigger_reason=decision.reason,
            )
            if final_decision is not None:
                decision = final_decision
            else:
                # Report which of the checks refused, so the Planner repairs
                # what is actually missing.
                detail = "; ".join(final_stage_completion_blockers(review, **checks))
                decision = StageDecision(
                    "hold",
                    cur,
                    (
                        f"Completion held: {detail}"
                        if detail
                        else "Completion held by the project completion contract"
                    ),
                    "gate_completion_rejected",
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
        """The completion gate: move the stage when the evidence says it is ready.

        One owner decides a stage (docs/role-handoff-design.md, "New
        ownership"): the gate. It reads evidence, never asks a model: the
        accepted candidate (the Reviewer's acceptance, or the Engineer's where
        no review is required), the vertical's stage completion checks, the
        person's required steps (必经步骤) and the project completion
        contract. The Planner proposes a stage ready by planning the task that
        closes it (``stage_closing``) or with ``advance_to_stage``; a task that
        does not close the stage moves nothing. The Manager only tells the
        person what happened.

        Writes nothing and holds, with the reason the Planner reads, when the
        work was not accepted, the stage was not proposed ready, the person
        asked to decide stage moves themselves, or a check refuses.
        ``root_task_id`` is the backlog task whose work is judged.
        ``run_exec`` is accepted for older callers and unused.
        """
        from ._core import StageTransition

        _ = (run_exec, on_event)
        root = Path(project_root) if project_root is not None else self.project_root

        with self.pipeline_lock():
            ctx = self._gather_stage_context(root)
            if isinstance(ctx, StageTransition):
                return ctx
            expected_context = self._stage_context_facts(root)
        cur, order, checklist_contract = ctx

        def hold(reason: str, source: str, diagnostic: str = "") -> "StageTransition":  # noqa: F821
            return StageTransition(
                "hold", cur, reason, current_stage=cur, source=source, diagnostic=diagnostic,
            )

        if (
            getattr(review, "engineer_aborted_before_review", False) is True
            and getattr(review, "backend_stop_kind", None) == "operator_abort"
        ):
            return hold(
                "engineer was operator-aborted before review",
                "operator_abort_hold", "engineer_aborted_before_review",
            )
        if review is None:
            return hold("no accepted work to judge the stage by", "no_review_hold")

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
                return hold(
                    "Keep Experiment open for Planner's post-result scale assessment. "
                    "After Reviewer accepts the current experiment, Planner must "
                    "compare its training and evaluation coverage with the operator "
                    "objective, expand under the benchmark policy if insufficient, or "
                    "request ADVANCE_TO_STAGE=paper with the adequacy rationale.",
                    "planner_scale_assessment_hold", "experiment_scale_assessment_required",
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
                return hold(acceptance_issue, "venue_acceptance_hold", "final_venue_acceptance_required")

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
                return hold(
                    str(freshness.get("message") or "stale manuscript review"),
                    "stale_manuscript_review_hold", "reviewed_manuscript_version_mismatch",
                )

        decision = self._gate_decision(
            review, cur, order, root,
            open_ended=open_ended, mission_scope=mission_scope,
            stage_closing=stage_closing, checklist_contract=checklist_contract,
            judged_item_id=str(root_task_id or ""),
        )
        return self._commit_stage_decision_if_current(
            decision, cur, root, expected_context=expected_context, source="completion_gate",
        )

    def _gate_decision(
        self,
        review: Any,
        cur: str,
        order: list[str],
        root: Path,
        *,
        open_ended: bool,
        mission_scope: str,
        stage_closing: bool,
        checklist_contract: Any,
        judged_item_id: str = "",
    ) -> Any:
        """What the evidence says about the stage: advance, complete, roll back, or hold and why."""
        from ..core.pipeline_state import read_pipeline_state
        from ..skills.stage_machine import StageCompletionError, _ensure_stage_completion
        from ..skills.vertical_select import resolve_workflow_mode
        from .stage_decider import StageDecision

        def held(reason: str, diagnostic: str) -> StageDecision:
            return StageDecision("hold", cur, reason, diagnostic)

        if not self._is_clean_reviewer_acceptance(review):
            return held("the stage moves on accepted work only", "gate_not_accepted")
        if not stage_closing:
            return held(
                "this task does not close the stage; the Planner proposes the stage ready "
                "(a stage-closing task or advance_to_stage)",
                "gate_not_proposed",
            )
        if read_pipeline_state(root).get("operator_decides_stages"):
            return held(
                "the person asked to decide stage moves: the Planner asks them before "
                "advance_to_stage",
                "gate_person_decides",
            )
        at = order.index(cur) if cur in order else -1
        next_stage = order[at + 1] if 0 <= at < len(order) - 1 else ""
        terminal_stage = bool(order and cur == order[-1])
        allow_early_completion = not open_ended and resolve_workflow_mode(root) == "direct"
        if not (next_stage or terminal_stage):
            return held(f"stage {cur!r} is not part of this project's stage order", "gate_unknown_stage")
        if not (allow_early_completion and not terminal_stage):
            try:
                _ensure_stage_completion(root, cur, evidence_root=self.execution_workdir)
            except StageCompletionError as exc:
                return held(str(exc), "stage_completion_gate_failed")
            except Exception:  # noqa: BLE001 - unreadable evidence never moves a stage
                log.warning("stage completion checks could not run", exc_info=True)
                return held("the stage completion checks could not be read", "stage_completion_check_error")
        if terminal_stage or allow_early_completion:
            decision = StageDecision(
                "complete",
                cur,
                # Direct mode gives the Reviewer no stage checklist, so the
                # recorded reason must not claim one was certified.
                "the accepted work is the whole task and the completion checks passed"
                if allow_early_completion
                else "the accepted work closes the final stage and the completion checks passed",
                "gate_complete",
            )
        else:
            decision = StageDecision(
                "advance",
                next_stage,
                "the accepted work closes this stage and its completion checks passed",
                "gate_advance",
            )
        return self._gate_completion(
            decision, cur, order, review, open_ended, mission_scope, checklist_contract, root,
            judged_item_id=judged_item_id,
        )

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
