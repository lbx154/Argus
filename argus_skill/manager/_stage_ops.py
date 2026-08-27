"""argus.manager._stage_ops — mixin for stage-transition authority.

``_StageDecisionMixin`` carries the Manager's sole stage-transition authority
(``decide_stage_transition``) and the ``current_stage`` reader.

``decide_stage_transition`` is decomposed into focused private helpers grouped
by phase — gather context, run the model, parse the decision, apply to disk —
so that no method exceeds 350 lines while the full decision logic is preserved
byte-for-byte.

"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..core.role_decision import latest_role_decision
from ._helpers import (
    _manager_model,
    _manager_reasoning_effort,
    gateway_run_exec,
    log,
)

_log = logging.getLogger(__name__)

_MISSING_REVIEW_FIELD = object()
_NEUTRAL_REVIEW_TEXT = {
    "",
    "-",
    "n/a",
    "na",
    "no action",
    "no further action",
    "no further work",
    "none",
    "not applicable",
    "nothing further",
    "null",
}
_FRONTIER_CHANGES = {
    "artifact_improved",
    "risk_reduced",
    "uncertainty_reduced",
    "information_gain",
    "bounded_regression",
    "recovered",
    "unchanged_failure",
    "expanding_regression",
    "unexplained_regression",
}
_FRONTIER_CONFLICTS = {
    "bounded_regression",
    "unchanged_failure",
    "expanding_regression",
    "unexplained_regression",
}


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class _StageDecisionMixin:
    """Mixin: decide_stage_transition (sole stage authority) + current_stage."""

    # ------------------------------------------------------------------
    # Private helpers — each covers one logical phase of decide_stage_transition
    # ------------------------------------------------------------------

    @staticmethod
    def _is_clean_reviewer_acceptance(review: Any) -> bool:
        """Whether ``review`` can be committed without another semantic vote.

        Missing or malformed control fields are deliberately not normalized here:
        the Reviewer parser normally supplies every one, so an incomplete object
        is ambiguous evidence and must fall through to the Manager model.
        """

        def field(name: str) -> Any:
            return getattr(review, name, _MISSING_REVIEW_FIELD)

        status = field("status")
        reason = field("reason")
        next_action = field("next_action")
        operator_question = field("operator_question")
        operator_options = field("operator_options")
        review_source = field("review_source")
        backend_unavailable = field("backend_unavailable")
        backend_fatal_error = field("backend_fatal_error")
        planner_report = field("planner_report")
        frontier_report = field("frontier_report")
        session_signal = field("session_signal")

        if not (
            isinstance(status, str)
            and status.strip().lower() == "done"
            and isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(next_action, str)
            and next_action.strip().casefold() in _NEUTRAL_REVIEW_TEXT
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
            and isinstance(planner_report, dict)
            and isinstance(frontier_report, dict)
            and isinstance(session_signal, dict)
            and not session_signal
        ):
            return False

        allowed_plan_fields = {
            "forward_progress",
            "plan_signal",
            "challenge",
            "alternative",
            "authority_impact",
        }
        if not set(planner_report).issubset(allowed_plan_fields):
            return False
        if "forward_progress" in planner_report:
            progress = planner_report["forward_progress"]
            if not isinstance(progress, bool) or not progress:
                return False
        if "plan_signal" in planner_report:
            signal = planner_report["plan_signal"]
            if not isinstance(signal, str):
                return False
            signal = signal.strip().lower()
            if signal not in {"", "continue", "reconsider"} or signal == "reconsider":
                return False
        for name in ("challenge", "alternative"):
            if name in planner_report:
                value = planner_report[name]
                if not isinstance(value, str):
                    return False
                if value.strip().casefold() not in _NEUTRAL_REVIEW_TEXT:
                    return False
        if "authority_impact" in planner_report:
            authority = planner_report["authority_impact"]
            if not isinstance(authority, str):
                return False
            authority = authority.strip().lower()
            if authority not in {"", "technical", "manager_contract", "operator"}:
                return False
            if authority in {"manager_contract", "operator"}:
                return False

        allowed_frontier_fields = {
            "change",
            "summary",
            "resolved_obligations",
            "new_obligations",
            "regressed_obligations",
            "remaining_work",
            "proxy_changes",
            "artifacts",
            "evidence",
            "hypothesis",
            "uncertainty",
            "next_decision_point",
            "regression",
        }
        if not set(frontier_report).issubset(allowed_frontier_fields):
            return False
        if frontier_report:
            change = frontier_report.get("change", _MISSING_REVIEW_FIELD)
            if not isinstance(change, str):
                return False
            change = change.strip().lower()
            if change not in _FRONTIER_CHANGES or change in _FRONTIER_CONFLICTS:
                return False
        for name in (
            "resolved_obligations",
            "new_obligations",
            "regressed_obligations",
            "remaining_work",
            "proxy_changes",
            "artifacts",
            "evidence",
        ):
            if name in frontier_report:
                values = frontier_report[name]
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    return False
        for name in (
            "summary",
            "hypothesis",
            "uncertainty",
            "next_decision_point",
        ):
            if name in frontier_report and not isinstance(frontier_report[name], str):
                return False
        if "regression" in frontier_report and not isinstance(
            frontier_report["regression"], dict
        ):
            return False
        if any(
            frontier_report.get(name)
            for name in (
                "new_obligations",
                "regressed_obligations",
                "remaining_work",
                "next_decision_point",
                "regression",
            )
        ):
            return False
        return True

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
            _active_vertical_checklist_defs as _vertical_defs,
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
    ) -> "tuple[Any, StageTransition | None]":  # noqa: F821
        """Phase 2: build the LLM caller (with cost metering) if not supplied.

        Returns ``(wrapped_run_exec, None)`` on success or
        ``(None, StageTransition(hold, ...))`` when no backend is available.
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

        def _run_exec(prompt: str) -> Any:  # noqa: ANN401
            return gateway_run_exec(
                _backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=_manager_model(),
                    reasoning_effort=_manager_reasoning_effort(),
                    working_dir=str(self.execution_workdir),
                    dangerous_yolo=False,
                    sandbox_mode="read-only",
                    skip_git_repo_check=True,
                ),
                run_label="manager-stage",
            )

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
            run_label="manager-stage",
        )
        return _run_exec, None

    def _run_stage_model(
        self,
        run_exec: Any,
        prompt: str,
        root_task_id: str | None,
    ) -> str:
        """Phase 3: run the model with empty-output retry.

        Returns the raw model output string (may be empty on repeated failure).
        """
        with self._task_usage_scope(root_task_id):
            raw = self._extract_answer_safe(run_exec(prompt))
            # gpt-5.5/fnyweg (and other backends) occasionally return an EMPTY
            # turn. An empty raw makes parse_stage_decision fall back to a silent
            # "manager held (default)" — which, after a DONE reviewer verdict,
            # wedges current_stage FOREVER (research completes but never advances
            # to plan, because no later mission re-triggers a stage decision).
            # Retry a couple of times on an empty response before accepting a
            # hold, mirroring the planner's empty-output retry. A genuine,
            # non-empty hold verdict is never retried.
            _empty_retries = 0
            while not str(raw or "").strip() and _empty_retries < 2:
                _empty_retries += 1
                time.sleep(1.0)
                raw = self._extract_answer_safe(run_exec(prompt))
        return raw or ""

    @staticmethod
    def _extract_answer_safe(result: Any) -> str:
        """Wrap extract_answer so it never raises; returns '' on any failure."""
        from .stage_decider import extract_answer
        try:
            process_decision = latest_role_decision(result, "manager")
            if process_decision is not None:
                return json.dumps(process_decision, ensure_ascii=True)
            return extract_answer(result) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _parse_and_finalize_stage_decision(
        self,
        raw: str,
        cur: str,
        order: list[str],
        review: Any,
        open_ended: bool,
        mission_scope: str,
        planner_wait_reconciliation: bool,
        checklist_contract: Any,
        root: Path,
        on_event: Any,
    ) -> Any:
        """Phase 4: parse raw output, apply live view, finalize the decision.

        Returns a ``StageDecision``-like object (action, target_stage, reason, …).
        """
        from .stage_decider import (
            completion_trigger_reason,
            external_completion_gate_rework_decision,
            external_completion_gate_stage_guard_decision,
            fallback_empty_stage_decision,
            final_stage_completion_decision,
            parse_stage_decision,
        )

        if not str(raw or "").strip():
            decision = fallback_empty_stage_decision(
                review,
                current_stage=cur,
                stage_order=order,
                checklist_contract=checklist_contract,
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
            from ..skills.vertical_select import resolve_workflow_mode
            from .stage_decider import StageDecision

            if (
                planner_wait_reconciliation
                and resolve_workflow_mode(root) != "direct"
                and decision.action in {"advance", "complete"}
            ):
                decision = StageDecision(
                    "hold",
                    cur,
                    "planner waiting cannot advance without reviewer evidence",
                    "planner_wait_advance_rejected",
                )
            return decision

        # Apply live view and emit event.
        try:
            from .live_view import (
                apply_manager_rendering_response,
                parse_live_view_response,
            )

            live_decided, _live_view = parse_live_view_response(raw)
            live_view = apply_manager_rendering_response(
                self.execution_workdir,
                raw,
                manifest_root=self.manager_session_root,
            )
            if live_decided and on_event is not None:
                on_event({
                    "type": "manager.live_view.updated",
                    "title": live_view.title if live_view else "",
                    "paths": list(live_view.paths) if live_view else [],
                    "reason": live_view.reason if live_view else "",
                    "explicit_clear": live_view is None,
                    "text": (
                        f"Manager refreshed right sidebar: {live_view.title}"
                        if live_view
                        else "Manager cleared right sidebar"
                    ),
                })
        except Exception as exc:  # noqa: BLE001 — rendering never blocks stage
            log.debug("manager live-view refresh failed", exc_info=True)
            if on_event is not None:
                on_event({
                    "type": "manager.live_view.rejected",
                    "error": str(exc)[:500],
                    "text": (
                        "Manager right-sidebar update rejected; "
                        "previous valid view preserved"
                    ),
                })

        from ..core.external_completion_gate import external_completion_gate_issue
        from ..core.research_contract import resolve_research_target_level
        from ..skills.vertical_select import (
            resolve_vertical,
            resolve_workflow_mode,
        )

        # Computed once and shared by every check below that needs it.
        _allow_early_completion = (
            not open_ended and resolve_workflow_mode(root) == "direct"
        )
        decision = parse_stage_decision(raw, current_stage=cur, stage_order=order)

        _completion_vertical = resolve_vertical(root)
        _research_target_level = resolve_research_target_level(root)
        _completion_blockers = [
            blocker
            for blocker in (external_completion_gate_issue(self.execution_workdir),)
            if blocker
        ]
        if _research_target_level in {"publishable", "doctoral"}:
            from ..verticals._base import (
                load_vertical,
                vertical_stage_completion_issues,
            )

            # Final completion must re-run the active provider's own strongest
            # stage validator, but Manager must not import a named vertical to
            # do it. The contract keeps this path domain-blind and also lets
            # project-local/plugin research providers enforce equivalent gates.
            _completion_blockers.extend(
                vertical_stage_completion_issues(
                    load_vertical(_completion_vertical, project_root=root),
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
                from .stage_decider import (
                    StageDecision,
                    final_stage_completion_blockers,
                    stage_position_is_the_only_completion_blocker,
                )

                # Report which of the checks refused. The bare "rejected by the
                # project completion contract" left the Planner guessing:
                # testbed run 13 answered it by queueing a mission to "record
                # the missing route/ledger state or equivalent gate metadata",
                # when the real answer was that it was sitting at ``scope`` and
                # needed to advance.
                blockers = final_stage_completion_blockers(
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
                )
                if stage_position_is_the_only_completion_blocker(blockers):
                    # Nothing is wrong with this completion except where the
                    # pipeline is standing, so stand somewhere else. Reporting
                    # the refusal better was not enough on its own: run 15 got
                    # the improved sentence and still sat at ``scope`` with the
                    # problem solved, because a Manager cannot act on an
                    # explanation it is given after its turn has ended.
                    decision = StageDecision(
                        "advance",
                        order[order.index(cur) + 1],
                        decision.reason or "operator objective complete",
                        "complete_at_nonfinal_advanced",
                    )
                else:
                    detail = "; ".join(blockers)
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

        from .stage_decider import StageDecision

        if (
            planner_wait_reconciliation
            and resolve_workflow_mode(root) != "direct"
            and decision.action in {"advance", "complete"}
        ):
            decision = StageDecision(
                "hold",
                cur,
                "planner waiting cannot advance without reviewer evidence",
                "planner_wait_advance_rejected",
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
        from ..skills.stage_machine import StageCompletionError
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
                                   cur, source, decision.diagnostic,
                                   decision.resolves_wait)

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
                                   cur, source, decision.diagnostic,
                                   decision.resolves_wait)

        if decision.action == "rollback":
            try:
                _rollback(root, target_stage=decision.target_stage,
                          reason=decision.reason, rolled_back_by="manager",
                          evidence_root=self.execution_workdir)
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("rollback", decision.target_stage, decision.reason,
                                   cur, source, decision.diagnostic,
                                   decision.resolves_wait)

        return StageTransition("hold", cur, decision.reason or "manager held",
                               cur, source, decision.diagnostic,
                               decision.resolves_wait)

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
        ctx = self._gather_stage_context(root)
        if isinstance(ctx, StageTransition):
            return ctx
        cur, order, checklist_contract = ctx

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
        planner_wait_reconciliation = bool(
            open_ended
            and review is None
            and planner_verdict is not None
            and bool(getattr(planner_verdict, "waiting", False))
            and not bool(getattr(planner_verdict, "project_done", False))
            and not list(getattr(planner_verdict, "new_tasks", []) or [])
        )

        # --- Phase 4: Handle no-review (early hold or build synthetic review) ---
        # No reviewer feedback normally means no stage transition. Structured
        # open-ended terminal and Planner-wait reconciliations are the exceptions.
        if review is None:
            if not (
                open_ended_terminal_reconciliation
                or planner_wait_reconciliation
            ):
                return StageTransition(
                    "hold", cur, "no reviewer feedback", current_stage=cur,
                    source="no_review_hold",
                )
            planner_reason = str(
                getattr(planner_verdict, "reason", "") or planner_verdict
            )
            if planner_wait_reconciliation:
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
                    diagnostic="reviewed_manuscript_sha_mismatch",
                )

        # A parsed, conflict-free Reviewer acceptance is already the semantic
        # judgment for an intermediate stage. Manager still performs the exact
        # stage-machine preflight and remains the sole writer; only its duplicate
        # model vote is skipped. Terminal completion and every ambiguous signal
        # continue through the model path below.
        next_stage = (
            order[order.index(cur) + 1]
            if cur in order and order.index(cur) + 1 < len(order)
            else ""
        )
        external_gate_issue = ""
        if next_stage:
            from ..core.external_completion_gate import external_completion_gate_issue

            external_gate_issue = external_completion_gate_issue(
                self.execution_workdir
            )
        deterministic_candidate = bool(
            stage_closing
            and next_stage
            and self._is_clean_reviewer_acceptance(review)
            and planner_verdict is None
            and not external_gate_issue
        )
        if deterministic_candidate:
            try:
                from ..skills.stage_machine import _ensure_stage_completion
                from .stage_decider import StageDecision

                _ensure_stage_completion(
                    root,
                    cur,
                    evidence_root=self.execution_workdir,
                )
                return self._apply_stage_decision_to_disk(
                    StageDecision(
                        "advance",
                        next_stage,
                        "Reviewer certified the current-stage checklist and "
                        "deterministic completion checks passed",
                        "deterministic_reviewer_done",
                    ),
                    cur,
                    root,
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

            prompt_context = resolve_role_prompt(
                stage_decision_request(root, stage=cur)
            )
            _match_objective = " ".join(
                p for p in (cur, str(getattr(review, "reason", "") or "")) if p
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
                ),
                role_banner=prompt_context.role_banner,
                role_skill_block=self._role_skill_block(
                    _match_objective,
                    include_libraries=True,
                ),
            )

            raw = self._run_stage_model(
                run_exec,
                prompt,
                root_task_id,
            )

            decision = self._parse_and_finalize_stage_decision(
                raw,
                cur=cur,
                order=order,
                review=review,
                open_ended=open_ended,
                mission_scope=mission_scope,
                planner_wait_reconciliation=planner_wait_reconciliation,
                checklist_contract=checklist_contract,
                root=root,
                on_event=on_event,
            )
        except Exception:  # noqa: BLE001 — any failure → safe HOLD, write nothing
            log.debug("manager stage decision failed", exc_info=True)
            return StageTransition(
                "hold", cur, "manager decision error", current_stage=cur,
                source="failsafe_hold", diagnostic="exception",
            )

        # --- Phase 8: Apply decision to disk ---
        return self._apply_stage_decision_to_disk(decision, cur, root)

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
