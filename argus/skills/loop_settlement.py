"""Mission completion without runtime knowledge interpretation.

Agents may edit Skill/Wiki Markdown during their normal reviewed mission.  The
runtime does not score, parse, rewrite, compact, or reconcile that knowledge
when the mission ends.
"""
from __future__ import annotations

from ..core.event_catalog import EventType
from ..core.models import LoopOutcome, LoopStatus
from ..core.stop_kinds import stop_kind_is_recoverable
from .loop_state import MissionContext, SkillLibraryState

_MANAGER_PARKS = {"manager_wait": "operator_question", "manager_stop": "research_open"}


class MissionSettlementMixin:
    def _settle_mission_outcome(
        self,
        mission: MissionContext,
        state: SkillLibraryState,
        status: LoopStatus,
        rounds: list,
        final_message: str,
        reason: str,
        last_thread_id: str | None,
    ) -> LoopOutcome:
        stop_kind = rounds[-1].stop_kind if rounds else None
        # The Manager parks a run without a round of its own; any other park
        # is its last round's stop, and a host that could not start is the
        # infrastructure's.
        park_reason = _MANAGER_PARKS.get(status, "")
        if park_reason:
            status, stop_kind = "parked", None
        elif status == "parked":
            park_reason = "infra" if stop_kind == "backend_unavailable" else str(stop_kind or "")
        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            final_message=final_message,
            reason=reason,
            workdir=str(mission.workdir),
            last_thread_id=last_thread_id,
            stop_kind=stop_kind,
            recoverable=stop_kind_is_recoverable(stop_kind),
            park_reason=park_reason,
        )
        self._emit({"type": EventType.LOOP_DONE, "status": status, "rounds": len(rounds)})
        return outcome
