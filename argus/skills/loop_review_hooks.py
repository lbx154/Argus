"""Minimal pre-review setup and Host-owned round checkpoint hooks."""
from __future__ import annotations

from ..core.models import RoundRecord
from .loop_state import MissionContext


class ReviewedRoundHooksMixin:
    """Keep Reviewer setup and persistence outside the Reviewer agent."""

    def _prepare_review_context(self, mission: MissionContext) -> None:
        if not self.config.wiki_enabled:
            return
        from ..wiki.auto_hooks import prepare_wikis_for_review

        prepare_wikis_for_review(
            mission.workdir,
            mission_id=mission.run_id,
            emit=self.on_event,
        )

    def _capture_reviewed_round(self, mission: MissionContext, record: RoundRecord) -> None:
        if self.config.round_checkpoint_enabled and record.review.checkpoint_recommended:
            from ..core.event_catalog import EventType
            from .round_checkpoint import checkpoint_round

            result = checkpoint_round(
                mission.workdir,
                mission_id=mission.run_id,
                round_index=record.round_index,
                message=(
                    str(record.review.reason or "").splitlines()[0]
                    or f"Reviewed round {record.round_index}"
                ),
            )
            if result.recorded:
                self._emit({
                    "type": EventType.ROUND_CHECKPOINT_RECORDED,
                    "run_id": mission.run_id,
                    "round": record.round_index,
                    "ref": result.ref,
                })
            elif result.error:
                self._emit({
                    "type": EventType.ROUND_CHECKPOINT_FAILED,
                    "run_id": mission.run_id,
                    "round": record.round_index,
                    "error": result.error,
                })
