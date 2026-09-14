"""One claimed mission: execute, meter, settle, and persist its outcome.

``_run_one`` is the orchestrator for one backlog item's full lifecycle. Its
phases live in two sibling mixins so no single module grows unwieldy:

- ``_mission_execution_runtime.py``: claim/context setup, runner invocation
  (incl. the restricted validator-repair capability claim), basic outcome
  derivation, and the budget/provider pause short-circuit.
- ``_mission_execution_settlement.py``: repair-capability settlement, the
  dynamic-plan stage guard, final status resolution against the backlog, and
  the journal event + return-dict emission.

"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..memory import BacklogItem
from ._mission_execution_runtime import MissionExecutionRuntimeMixin
from ._mission_execution_settlement import MissionExecutionSettlementMixin
from .backlog_guard import ensure_manager_decision

log = logging.getLogger(__name__)

__all__ = ["MissionExecutionMixin"]


class MissionExecutionMixin(
    MissionExecutionRuntimeMixin, MissionExecutionSettlementMixin,
):
    _emit: Callable[[dict[str, Any]], bool]

    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        """Claim -> prepare -> execute -> meter -> settle -> publish one attempt.

        Return paths and the phase that owns their durable effects:

        * Lost claim: no execution; undo an unexpected claim if necessary.
        * Superseded acceptance: meter the call, preserve the replacement task.
        * Recoverable stop: pause helper persists the pause and completion event,
          or requeues external work that changed before it could be parked.
        * Stage continuation/HOLD: stage helper requeues/fails the bounded item;
          it does not publish the ordinary mission completion event.
        * Ordinary settlement (including chartered iteration): finalizer writes
          backlog outcome, then publisher records learning, usage, and the event.

        Every post-execution branch follows metering. Pause and supersession
        return before repair/stage settlement; chartered iteration is assessed
        before a stage HOLD can fail the item. These are deliberate ownership
        boundaries, so there is no unconditional finalization in a ``finally``.
        Execution errors become outcomes in the runner phase; exceptions from
        preparation or settlement propagate to the supervisor's tick guard.
        """
        # Claim and prepare: resolve one canonical execution/contract context.
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        parallel_worker = getattr(self.config, "parallel_worker", False)
        coordinate_claims = getattr(
            self.config,
            "coordinate_parallel_claims",
            False,
        )
        claimed = self.memory.backlog.claim_next(
            parallel_only=parallel_worker,
            respect_running=coordinate_claims,
            expected_id=item.id,
            owner=str(
                getattr(self.config, "worker_id", "primary") or "primary"
            ),
        )
        if claimed is None or claimed.id != item.id:
            if claimed is not None:
                # Roll back so the next tick sees it again. running →
                # pending is a legal transition (only terminal states
                # are sealed).
                try:
                    self.memory.backlog.update(claimed.id, status="pending")
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: claim rollback failed")
            return {"status": "claim_lost", "item_id": item.id}
        item = claimed
        # Resolve the claimed node before consulting any repository-facing
        # policy.  Adoption updates the active campaign workdir, so the bound
        # Manager and every later mission phase see the same canonical tree.
        resolved_mission_workdir = self._resolve_mission_workdir(item)
        vertical_root = self._mission_vertical_root(
            item,
            resolved_mission_workdir,
        )

        # An item written straight into backlog.jsonl never passed through the
        # Manager, so no vertical, stage, or target level was chosen and the run
        # silently proceeds under the default workflow — the Manager looks like
        # it is doing nothing. Route it now rather than executing blind;
        # already-routed items are untouched.
        manager = (
            self._bound_manager()
            if getattr(self, "manager", None) is not None
            else None
        )
        from ...core.plugin_manager import PluginUnavailableError

        try:
            item = ensure_manager_decision(
                self.memory,
                item,
                getattr(self, "chat_state", None),
                manager=manager,
                vertical_root=vertical_root,
            )
        except PluginUnavailableError as exc:
            # No mission was started. Seal this attempt visibly so the plugin
            # monitor can leave "thinking" without paying for a fallback run.
            reason = str(exc)
            # "blocked" is an outcome, not a valid backlog status (unknown
            # statuses normalize to pending and would immediately retry).
            self.memory.backlog.update(item.id, status="failed", last_error=reason)
            self._emit({
                "type": "life.mission.completed", "item_id": item.id,
                "title": item.title, "success": False, "status": "failed",
                "outcome_class": "blocked", "stop_kind": "permanent_error",
                "stop_reason": reason, "failure_reason": reason, "summary": reason,
                "resumable": False, "recoverable": False,
            })
            return {
                "status": "failed", "item_id": item.id, "success": False,
                "outcome_class": "blocked", "stop_kind": "permanent_error",
                "stop_reason": reason,
            }

        prelude = self._build_mission_prelude(item, defer_memory=True)
        state = self._prepare_mission_context(
            item,
            prelude,
            resolved_mission_workdir,
            vertical_root,
        )
        # Execute, then meter before choosing an outcome-dependent settlement branch.
        self._invoke_mission_runner(state)
        self._derive_basic_outcome_fields(state)

        if getattr(state.outcome, "acceptance_assessment_superseded", False):
            # Preflight lost its contract/claim while the provider was running.
            # Meter that call, then leave the current task untouched. In
            # particular, never settle the newer task using this old outcome.
            return {
                "success": False, "status": "claim_lost", "item_id": item.id,
                "recoverable": True, "stop_reason": state.stop_reason,
                "cost_usd": state.usd, "known_cost_usd": state.known_usd,
            }

        paused_result = self._maybe_pause_for_recoverable_stop(state)
        if paused_result is not None:
            return paused_result

        # Settle authority and iteration before ordinary terminal classification.
        self._settle_repair_capability(state)
        self._apply_dynamic_plan_stage_guard(state)

        # A final-result miss normally makes the Manager HOLD the terminal
        # stage. Let the active vertical classify that miss before the generic
        # stage-hold branch terminalizes the item; otherwise the iteration
        # contract is unreachable on exactly the live fell-short path.
        state.iteration = self._maybe_requeue_chartered_shortfall(state)
        state.iteration_requeued = bool(
            state.iteration and state.iteration.get("requeued")
        )

        if state.iteration is None:
            transition_result = self._maybe_short_circuit_for_stage_transition(state)
            if transition_result is not None:
                return transition_result

        # Finalize the backlog first; only then publish the settled outcome.
        self._finalize_mission_status(state)
        return self._emit_mission_outcome_and_build_result(state)
