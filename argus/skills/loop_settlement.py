"""Mission completion without runtime knowledge interpretation.

Agents may edit Skill/Wiki Markdown during their normal reviewed mission.  The
runtime does not score, parse, rewrite, compact, or reconcile that knowledge
when the mission ends.
"""
from __future__ import annotations

from ..core.event_catalog import EventType
from ..core.models import LoopOutcome, LoopStatus
from ..core.stop_kinds import run_park_reason, stop_kind_is_recoverable
from .loop_state import MissionContext, SkillLibraryState


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
        # The Manager stops a run without a round of its own, for the Planner
        # to retire; any other park is its last round's stop.
        park_reason = "research_open" if status == "manager_stop" else ""
        if park_reason:
            status, stop_kind = "parked", None
        elif status == "parked":
            park_reason = run_park_reason(stop_kind)
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
