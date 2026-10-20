"""Minimal pre-review setup kept outside the Reviewer agent."""
from __future__ import annotations

from .loop_state import MissionContext


class ReviewedRoundHooksMixin:
    """Keep Reviewer setup outside the Reviewer agent."""

    def _prepare_review_context(self, mission: MissionContext) -> None:
        if not self.config.wiki_enabled:
            return
        from ..wiki.auto_hooks import prepare_wikis_for_review

        prepare_wikis_for_review(
            mission.workdir,
            mission_id=mission.run_id,
            emit=self.on_event,
        )
