"""One claimed mission: execute, meter, settle, and persist its outcome.

``_run_one`` is the orchestrator for one backlog item's full lifecycle. Its
phases live in two sibling mixins so no single module grows unwieldy:

- ``_mission_execution_runtime.py``: claim/context setup, runner invocation
  (incl. the restricted validator-repair capability claim), basic outcome
  derivation, and the budget/provider pause short-circuit.
- ``_mission_execution_settlement.py``: repair-capability settlement, the
  dynamic-plan stage guard, final status resolution against the backlog, and
  the journal event + return-dict emission.

``bounded_dag_node_max_rounds`` / ``is_progressive_experiment_matrix`` are
re-exported here (from ``_mission_execution_helpers``) for backward-compatible
imports (``tests/life/test_bounded_dag_round_budget.py`` imports them from
this module path).
"""

from __future__ import annotations

import logging
from typing import Any

from ..memory import BacklogItem
from ._mission_execution_helpers import (
    bounded_dag_node_max_rounds,
    is_progressive_experiment_matrix,
)
from ._mission_execution_runtime import MissionExecutionRuntimeMixin
from ._mission_execution_settlement import MissionExecutionSettlementMixin

log = logging.getLogger(__name__)

__all__ = [
    "MissionExecutionMixin",
    "bounded_dag_node_max_rounds",
    "is_progressive_experiment_matrix",
]


class MissionExecutionMixin(
    MissionExecutionRuntimeMixin, MissionExecutionSettlementMixin,
):
    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self._build_mission_prelude(item)
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        claimed = self.memory.backlog.claim_next()
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

        state = self._prepare_mission_context(item, prelude)
        self._invoke_mission_runner(state)
        self._derive_basic_outcome_fields(state)

        paused_result = self._maybe_pause_for_recoverable_stop(state)
        if paused_result is not None:
            return paused_result

        self._settle_repair_capability(state)
        self._apply_dynamic_plan_stage_guard(state)

        transition_result = self._maybe_short_circuit_for_stage_transition(state)
        if transition_result is not None:
            return transition_result

        self._finalize_mission_status(state)
        return self._emit_mission_outcome_and_build_result(state)
