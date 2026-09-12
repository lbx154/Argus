"""The process-local data contract for one claimed mission.

``_MissionRunState`` is threaded through the lifecycle phase methods in
``_mission_execution_runtime.py`` and ``_mission_execution_settlement.py``. It
contains only values that cross phase boundaries. It is never serialized;
backlog rows, usage records, context packets, and events own durable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..memory import BacklogItem

if TYPE_CHECKING:
    from ...core.stop_kinds import StopKind
    from ...core.usage import UsageLedger, UsageSummary
    from ...manager.control_state import CampaignControlStore, CampaignIdentity
    from ._cost import _CostTrackingSink


@dataclass(slots=True, eq=False, repr=False)
class _MissionRunState:
    """Explicit shared fields, grouped by the phase that first supplies them.

    ``_prepare_mission_context`` fills the context group before returning this
    object. Execution supplies the raw outcome; derivation meters it before any
    settlement branch can return. Settlement may revise the derived result, and
    final emission refreshes usage after post-mission learning. Fields used only
    inside one phase stay local to that phase.

    ``outcome`` deliberately accepts the production runner, guard, and test
    outcome shapes. JSON payloads retain their existing format; they are not
    additional runtime object extension points. Slots reject undeclared fields
    so a new cross-phase dependency must be added to this contract explicitly.
    """

    item: BacklogItem

    # Context: _prepare_mission_context. Optional services/packet may be absent.
    prelude: str = ""
    pipeline_stage_at_start: str = ""
    usage_attempt_id: str = ""
    item_scope: str = ""
    usage_root: Path | None = None
    context_packet_path: Path | None = None
    usage_ledger: UsageLedger | None = None
    cost_sink: _CostTrackingSink | None = None
    item_tags: set[str] = field(default_factory=set)
    plan_revision_witness: dict[str, Any] = field(default_factory=dict)
    execution_workdir: Path | None = None
    vertical_root: Path | None = None
    configured_execution_workdir: str = ""

    # Execution: _invoke_mission_runner, including acceptance/repair guards.
    t0: float = 0.0
    outcome: object | None = None
    exc_str: str | None = None
    repair_store: CampaignControlStore | None = None
    repair_identity: CampaignIdentity | None = None
    repair_capability: dict[str, Any] | None = None
    recovered_repair_settlement: dict[str, Any] | None = None
    elapsed: float = 0.0

    # Derivation: _derive_basic_outcome_fields; settlement can revise these.
    success: bool = False
    status: str = "error"
    rounds: int = 0
    stop_reason: str = ""
    stop_kind: StopKind | None = None
    usage_summary: UsageSummary | None = None
    usd: float | None = 0.0
    known_usd: float = 0.0
    auth_failure: bool = False

    # Repair and stage settlement: _settle_repair_capability, then stage guard.
    repair_settlement: dict[str, Any] | None = None
    stage_transition: dict[str, Any] = field(default_factory=dict)
    stage_action: str = ""
    planner_bounded_node: bool = False

    # Iteration: _run_one asks the vertical before considering a stage HOLD.
    iteration: dict[str, Any] | None = None
    iteration_requeued: bool = False

    # Finalization: _finalize_mission_status supplies the durable outcome fields.
    replan_requested: bool = False
    intentional_abort: bool = False
    err: str = ""
    resumable: bool = False
    outcome_dimensions: dict[str, object] | None = None


__all__ = ["_MissionRunState"]
