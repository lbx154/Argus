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
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ._helpers import (
    _manager_model,
    _manager_reasoning_effort,
    gateway_run_exec,
    log,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class _StageDecisionMixin:
    """Mixin: decide_stage_transition (sole stage authority) + current_stage."""

    # ------------------------------------------------------------------
    # Private helpers — each covers one logical phase of decide_stage_transition
    # ------------------------------------------------------------------

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
        checklist_state = str(
            getattr(getattr(checklist_contract, "state", ""), "value", "")
            or getattr(checklist_contract, "state", "")
        )
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
        root: Path,
        on_event: Any,
    ) -> "tuple[Any, StageTransition | None]":  # noqa: F821
        """Phase 2: build the LLM caller (with cost metering) if not supplied.

        Returns ``(wrapped_run_exec, None)`` on success or
        ``(None, StageTransition(hold, ...))`` when no backend is available.
        """
        from ._core import StageTransition

        if run_exec is not None:
            return run_exec, None
        if self.runner is None and self._session is None:
            return None, StageTransition(
                "hold", "", "no manager backend", current_stage="",
                source="no_runner_hold",
            )
        from ..core.models import RunnerOptions

        _backend = self._session or self.runner

        def _run_exec(prompt: str) -> Any:  # noqa: ANN401
            return gateway_run_exec(
                _backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=_manager_model(),
                    reasoning_effort=_manager_reasoning_effort(),
                    working_dir=str(root),
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
        root: Path,
        root_task_id: str | None,
    ) -> str:
        """Phase 3: run the model with empty-output retry and checkpoint refresh.

        Returns the raw model output string (may be empty on repeated failure).
        """
        from ..roles.prompts.manager import (
            build_manager_checkpoint_correction_prompt,
        )
        from .live_view import (
            manager_checkpoint_refresh_required,
            repair_manager_checkpoint_response,
        )

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
            if str(raw or "").strip() and manager_checkpoint_refresh_required(
                root,
                raw,
                manifest_root=self.manager_session_root,
            ):
                correction_prompt = build_manager_checkpoint_correction_prompt(
                    prompt
                )
                candidate = self._extract_answer_safe(run_exec(correction_prompt))
                if str(candidate or "").strip():
                    raw = candidate
            if str(raw or "").strip() and manager_checkpoint_refresh_required(
                root,
                raw,
                manifest_root=self.manager_session_root,
            ):
                raw = repair_manager_checkpoint_response(
                    root,
                    raw,
                    manifest_root=self.manager_session_root,
                )
        return raw or ""

    @staticmethod
    def _extract_answer_safe(result: Any) -> str:
        """Wrap extract_answer so it never raises; returns '' on any failure."""
        from .stage_decider import extract_answer
        try:
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
            from ..core.research_contract import resolve_research_target_level
            from ..skills.vertical_select import resolve_vertical

            _completion_vertical = resolve_vertical(root)
            _research_target_level = resolve_research_target_level(root)
            final_decision = final_stage_completion_decision(
                review,
                current_stage=cur,
                stage_order=order,
                vertical=_completion_vertical,
                mission_scope=mission_scope,
                research_target_level=_research_target_level,
                checklist_contract=checklist_contract,
                trigger_diagnostic=decision.diagnostic,
                trigger_reason=decision.reason,
            )
            if final_decision is not None and (
                not open_ended or decision.action == "hold"
            ):
                decision = final_decision
            from .stage_decider import StageDecision
            if planner_wait_reconciliation and decision.action in {"advance", "complete"}:
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
                root,
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

        decision = parse_stage_decision(raw, current_stage=cur, stage_order=order)

        from ..core.research_contract import resolve_research_target_level
        from ..skills.vertical_select import resolve_vertical

        _completion_vertical = resolve_vertical(root)
        _research_target_level = resolve_research_target_level(root)
        final_decision = final_stage_completion_decision(
            review,
            current_stage=cur,
            stage_order=order,
            vertical=_completion_vertical,
            mission_scope=mission_scope,
            research_target_level=_research_target_level,
            checklist_contract=checklist_contract,
            trigger_diagnostic=decision.diagnostic,
            trigger_reason=decision.reason,
        )
        if final_decision is not None and (
            not open_ended or decision.action == "hold"
        ):
            decision = final_decision

        from .stage_decider import StageDecision

        if planner_wait_reconciliation and decision.action in {"advance", "complete"}:
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
    ) -> "StageTransition":  # noqa: F821
        """Phase 5: write the chosen action to ``PIPELINE_STATE.json`` and return a
        ``StageTransition`` describing what happened."""
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
                         reason=decision.reason, advanced_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal advance target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("advance", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic,
                                   decision.resolves_wait)

        if decision.action == "complete":
            try:
                _complete(root, reason=decision.reason, completed_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal final-stage completion", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("complete", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic,
                                   decision.resolves_wait)

        if decision.action == "rollback":
            try:
                _rollback(root, target_stage=decision.target_stage,
                          reason=decision.reason, rolled_back_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("rollback", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic,
                                   decision.resolves_wait)

        return StageTransition("hold", cur, decision.reason or "manager held",
                               cur, "manager_llm", decision.diagnostic,
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
    ) -> "StageTransition":  # noqa: F821
        """Independently decide advance / hold / rollback for the pipeline stage,
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

        # --- Phase 5: Build the LLM caller ---
        run_exec, hold = self._build_stage_run_exec(run_exec, root, on_event)
        if hold is not None:
            return StageTransition(
                hold.action, cur, hold.reason, current_stage=cur,
                source=hold.source,
            )

        # --- Phase 6–7: Run model, parse, finalize (wrapped in fail-safe) ---
        try:
            cur_idx = order.index(cur) if cur in order else -1
            next_stage = order[cur_idx + 1] if 0 <= cur_idx < len(order) - 1 else ""
            earlier = order[:cur_idx] if cur_idx > 0 else []
            from ..roles.prompts import resolve_role_prompt
            from ..roles.prompts.manager import (
                assemble_manager_prompt,
                build_stage_decision_prompt,
                manager_rendering_prompt,
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
                    earlier_stages=earlier,
                    checklist_md=prompt_context.stage_checklist,
                    review=review,
                    planner_verdict=planner_verdict,
                    rendering_block=manager_rendering_prompt(
                        root,
                        review=review,
                        manifest_root=self.manager_session_root,
                    ),
                    open_ended=open_ended,
                    continuous_objective=continuous_objective,
                ),
                role_banner=prompt_context.role_banner,
                role_skill_block=self._role_skill_block(
                    _match_objective,
                    match=False,
                ),
            )

            raw = self._run_stage_model(run_exec, prompt, root, root_task_id)

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
        import json as _json

        try:
            state = _json.loads(
                (self.project_root / "research" / "PIPELINE_STATE.json")
                .read_text(encoding="utf-8")
            )
            return str(state.get("current_stage") or "") or self.plan_stages(
                self._resolve_vertical_for_current_stage()
            )[0]
        except Exception:  # noqa: BLE001
            return ""

    def _resolve_vertical_for_current_stage(self) -> str:
        from ..skills.vertical_select import resolve_vertical
        return resolve_vertical(self.project_root)
