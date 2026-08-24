"""Small pure helpers + the mutable scratch state for one mission run.

``_MissionRunState`` is threaded through the lifecycle phase methods in
``_mission_execution_runtime.py`` and ``_mission_execution_settlement.py``. It
exists only to avoid re-deriving/re-threading dozens of interdependent locals
through method signatures; it is process-local scratch state for a single
``_run_one`` call, never persisted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..memory import BacklogItem


class _MissionRunState:
    """Mutable scratch state threaded through one ``_run_one`` lifecycle.

    Fields are populated progressively by each phase method (claim/context ->
    runner invocation -> repair capability -> outcome settlement/journal); the
    final phase reads whatever it needs off this object to build the event
    payload and the return dict. Attribute set is intentionally open (no
    ``__slots__``) because different phases set optional fields.
    """

    def __init__(self, item: BacklogItem) -> None:
        self.item = item

        # Set by ``_prepare_mission_context``.
        self.prelude: str = ""
        self.pipeline_stage_at_start: str = ""
        self.usage_attempt_id: str = ""
        self.item_scope: str = ""
        self.usage_root: Path | None = None
        self.context_packet_path: Path | None = None
        self.usage_ledger: Any = None
        self.cost_sink: Any = None
        self.item_tags: set[str] = set()
        self.execution_workdir: Path | None = None
        self.configured_execution_workdir: str = ""

        # Set by ``_invoke_mission_runner``.
        self.t0: float = 0.0
        self.outcome: Any = None
        self.exc_str: str | None = None
        self.repair_store: Any = None
        self.repair_identity: Any = None
        self.repair_capability: dict[str, Any] | None = None
        self.recovered_repair_settlement: dict[str, Any] | None = None
        self.elapsed: float = 0.0

        # Set by ``_derive_basic_outcome_fields``.
        self.success: bool = False
        self.status: str = "error"
        self.rounds: int = 0
        self.stop_reason: str = ""
        self.stop_kind: str | None = None
        self.usage_summary: Any = None
        self.usd: float = 0.0
        self.known_usd: float = 0.0
        self.auth_failure: bool = False

        # Set by ``_settle_repair_capability``.
        self.repair_settlement: dict[str, Any] | None = None

        # Set by ``_apply_dynamic_plan_stage_guard`` /
        # ``_maybe_short_circuit_for_stage_transition``.
        self.stage_transition: dict[str, Any] = {}
        self.stage_action: str = ""
        self.planner_bounded_node: bool = False

        # Set by ``_finalize_mission_status``.
        self.research_pause: bool = False
        self.replan_requested: bool = False
        self.intentional_abort: bool = False
        self.stage_reconciled_replan: bool = False
        self.err: str = ""
        self.resumable: bool = False
        self.outcome_dimensions: Any = None
        self.iteration: dict[str, Any] | None = None
        self.iteration_requeued: bool = False

        # Set by ``_emit_mission_outcome_and_build_result``.
        self.kind: str = ""
        self.final_submission_certified: bool = False
        self.final_submission_signature: str = ""
        self.scientist_totals: Any = None
        self.scientist_usage_by_model: Any = None


__all__ = ["_MissionRunState"]
