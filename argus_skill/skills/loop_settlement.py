"""Mission completion without runtime knowledge interpretation.

Agents may edit Skill/Wiki Markdown during their normal reviewed mission.  The
runtime does not score, parse, rewrite, compact, or reconcile that knowledge
when the mission ends.
"""
from __future__ import annotations

from ..core.event_catalog import EventType
from ..core.models import LoopOutcome
from ..core.stop_kinds import stop_kind_is_recoverable
from .loop_state import MissionContext, SkillLibraryState


class MissionSettlementMixin:
    def _settle_mission_outcome(
        self,
        mission: MissionContext,
        state: SkillLibraryState,
        status: str,
        rounds: list,
        final_message: str,
        reason: str,
        last_thread_id: str | None,
    ) -> LoopOutcome:
        stop_kind = rounds[-1].stop_kind if rounds else None
        if status == "paused_budget" and stop_kind is None:
            stop_kind = "budget_exhausted"
        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            final_message=final_message,
            reason=reason,
            workdir=str(mission.workdir),
            last_thread_id=last_thread_id,
            stop_kind=stop_kind,
            recoverable=stop_kind_is_recoverable(stop_kind),
        )
        self._emit(
            {
                "type": EventType.LOOP_DONE,
                "text": f"status={status} rounds={len(rounds)} reason={reason[:80]}",
            }
        )
        return outcome
