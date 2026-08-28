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
from typing import Any

from ..memory import BacklogItem
from ._mission_execution_runtime import MissionExecutionRuntimeMixin
from ._mission_execution_settlement import MissionExecutionSettlementMixin

log = logging.getLogger(__name__)

__all__ = ["MissionExecutionMixin"]


from .backlog_guard import ensure_manager_decision


class MissionExecutionMixin(
    MissionExecutionRuntimeMixin, MissionExecutionSettlementMixin,
):
    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
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
        item = ensure_manager_decision(
            self.memory,
            item,
            getattr(self, "chat_state", None),
            manager=manager,
            vertical_root=vertical_root,
        )

        prelude = self._build_mission_prelude(item)
        state = self._prepare_mission_context(
            item,
            prelude,
            resolved_mission_workdir,
            vertical_root,
        )
        self._invoke_mission_runner(state)
        self._derive_basic_outcome_fields(state)

        paused_result = self._maybe_pause_for_recoverable_stop(state)
        if paused_result is not None:
            return paused_result

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

        transition_result = (
            None
            if state.iteration is not None
            else self._maybe_short_circuit_for_stage_transition(state)
        )
        if transition_result is not None:
            return transition_result

        self._finalize_mission_status(state)
        return self._emit_mission_outcome_and_build_result(state)
