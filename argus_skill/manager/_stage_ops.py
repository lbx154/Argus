"""argus.manager._stage_ops — mixin for stage-transition authority.

``_StageDecisionMixin`` carries the Manager's sole stage-transition authority
(``decide_stage_transition``) and the ``current_stage`` reader.

``decide_stage_transition`` is decomposed into focused private helpers grouped
by phase — gather context, run the model, parse the decision, apply to disk —
so that no method exceeds 350 lines while the full decision logic is preserved
byte-for-byte.

``_manager_blocked_rollback_artifact`` is a module-level function (not in the
mixin) because it stands alone conceptually and is exercised by tests via the
public ``Manager.decide_stage_transition`` path.
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
    _read_json_object,
    gateway_run_exec,
    log,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helper (not in the mixin) — guards the rollback artifact path
# inside decide_stage_transition.
# ---------------------------------------------------------------------------

def _manager_blocked_rollback_artifact(
    root: Path,
    *,
    current_stage: str,
    stage_order: list[str],
) -> dict[str, Any] | None:
    payload = _read_json_object(root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json")
    if payload is None:
        return None
    if payload.get("outcome") != "MANAGER_BLOCKED":
        return None
    if payload.get("status") != "rollback-accepted":
        return None
    if payload.get("current_stage") != current_stage:
        return None
    if payload.get("requested_stage") != current_stage:
        return None
    target = payload.get("rollback_target")
    if not isinstance(target, str) or not target:
        return None
    if payload.get("earliest_broken_stage") != target:
        return None
    if payload.get("manager_action_required") != f"rollback_stage_to_{target}":
        return None
    if payload.get("pipeline_stage_fields_clean") is not True:
        return None
    try:
        current_idx = stage_order.index(current_stage)
        target_idx = stage_order.index(target)
    except ValueError:
        return None
    if target_idx >= current_idx:
        return None
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, dict) or not evidence_files:
        return None
    for rel in evidence_files.values():
        if not isinstance(rel, str) or not rel:
            return None
        if not (root / rel).exists():
            return None
    return payload


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
        from ..skills.stage_checklists import (
            _active_vertical_checklist_defs as _vertical_defs,
        )
        from ..skills.stage_checklists import current_stage as _current_stage
        from ..skills.stage_checklists import (
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
                correction_prompt = (
                    prompt
                    + "\n\n## Required correction\n"
                    + "Your previous response did not refresh the Manager-owned "
                    + "checkpoint. Return the same evidence-based stage ruling, "
                    + "but include a substantive `.argus/live/` presentation with "
                    + "Current node, Verified progress, Current blocker, and Next action."
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
            reject_certified_ground_truth_snapshot_rollback,
        )

        if not str(raw or "").strip():
            decision = fallback_empty_stage_decision(
                review,
                current_stage=cur,
                stage_order=order,
                checklist_contract=checklist_contract,
            )
            if not open_ended:
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
                if final_decision is not None:
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
        decision = reject_certified_ground_truth_snapshot_rollback(
            decision,
            project_root=root,
            current_stage=cur,
        )

        if not open_ended:
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
            if final_decision is not None:
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
        from ..skills.stage_checklists import (
            advance_stage as _advance,
        )
        from ..skills.stage_checklists import (
            complete_final_stage as _complete,
        )
        from ..skills.stage_checklists import (
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
        then WRITE it. The Manager is the SOLE post-bootstrap writer of
        ``current_stage`` — the reviewer/planner only ADVISE (via ``review`` /
        ``planner_verdict``); the engineer never edits stage state.

        THICK: the Manager makes its own LLM judgment from the reviewer's
        structured feedback + the current-stage checklist, parses a strict JSON
        verdict, and on advance/rollback calls
        :func:`stage_checklists.advance_stage` / ``rollback_stage``.

        Fail-safe — writes NOTHING and returns a HOLD when: ``review is None``
        (no feedback → never advance), there is no backend, the LLM/parse errors,
        or the model picks an illegal target. A HOLD simply leaves the stage put;
        the mission/planner loop continues, so the daemon never deadlocks.
        """
        from ..skills.stage_checklists import rollback_stage as _rollback
        from ._core import StageTransition

        root = Path(project_root) if project_root is not None else self.project_root

        # --- Phase 1: Gather context (may return early on config hold) ---
        ctx = self._gather_stage_context(root)
        if isinstance(ctx, StageTransition):
            return ctx
        cur, order, checklist_contract = ctx

        # --- Phase 2: Consume blocked-rollback artifact ---
        artifact = _manager_blocked_rollback_artifact(
            root, current_stage=cur, stage_order=order
        )
        if artifact is not None:
            target = str(artifact["rollback_target"])
            try:
                _rollback(
                    root,
                    target_stage=target,
                    reason=(
                        "stage_check accepted positive evidence rollback packet: "
                        f"earliest_broken_stage={artifact['earliest_broken_stage']}"
                    ),
                    rolled_back_by="manager",
                )
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback artifact target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="manager_blocked_artifact_illegal_target",
                )
            return StageTransition(
                "rollback",
                target,
                "stage_check accepted positive evidence rollback packet",
                cur,
                "manager_blocked_rollback_artifact",
                "accepted_manager_blocked_artifact",
            )

        # --- Phase 3: Compute reconciliation flags ---
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
                    planner_report={
                        "forward_progress": False,
                        "blocker": planner_reason,
                        "recommended_next": (
                            "HOLD if this is a genuine live external wait. ROLL "
                            "BACK only if current_stage prevents prerequisite "
                            "work that belongs to an earlier stage."
                        ),
                    },
                    checklist=[],
                )
            else:
                review = SimpleNamespace(
                    status="done",
                    reason=(
                        "The final-stage checkpoint is reviewer-certified, but the "
                        "open-ended campaign objective remains unresolved. "
                        f"Planner advisory: {planner_reason}"
                    ),
                    planner_report={
                        "forward_progress": False,
                        "blocker": planner_reason,
                        "recommended_next": (
                            "Manager decides whether to roll back for another "
                            "evidence-led cycle or hold."
                        ),
                    },
                    checklist=[],
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
            from ..skills.stage_checklists import format_stage_checklist as _format_checklist
            from .live_view import manager_rendering_prompt
            from .stage_decider import build_stage_decision_prompt

            checklist_md = _format_checklist(cur, role="planner", project_root=root)
            prompt = build_stage_decision_prompt(
                current_stage=cur,
                next_stage=next_stage,
                earlier_stages=earlier,
                checklist_md=checklist_md,
                review=review,
                planner_verdict=planner_verdict,
                rendering_block=manager_rendering_prompt(
                    root,
                    review=review,
                    manifest_root=self.manager_session_root,
                ),
                open_ended=open_ended,
                continuous_objective=continuous_objective,
            )
            from ..skills.vertical_select import resolve_vertical
            from ..verticals._base import load_vertical, vertical_role_banner

            manager_vertical_context = vertical_role_banner(
                load_vertical(resolve_vertical(root), project_root=root),
                "manager",
            )
            if manager_vertical_context:
                prompt = (
                    "## Active vertical Manager skill\n"
                    f"{manager_vertical_context}\n\n{prompt}"
                )
            # Inject the Manager's fixed role skill (+ any matched adaptive
            # manager skill) ahead of the decision prompt. No-op when no
            # skill_store is wired — the prompt is then byte-for-byte identical to
            # before, preserving the stage-decision output contract. The matcher
            # objective is the current stage + the reviewer's reason so the
            # role-scoped matcher has a concrete task descriptor.
            _match_objective = " ".join(
                p for p in (cur, str(getattr(review, "reason", "") or "")) if p
            )
            prompt = self._role_skill_block(_match_objective, match=False) + prompt

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
