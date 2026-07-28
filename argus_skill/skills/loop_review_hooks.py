"""Reviewed-round hooks + post-completion skill-maintenance phase for
``SkillLoop.run``.

Covers the three callbacks handed to ``SupervisedEngineer.run``:
pre-review wiki source/index priming (``_prepare_review_context``), per-reviewed-
round context-packet capture (``_capture_reviewed_round``), and the
same-session Engineer skill create/update continuation invoked after a
self-approved completion (``_maintain_skill_with_engineer``). Extracted
verbatim from the historical nested closures in ``SkillLoop.run``.
"""
from __future__ import annotations

import logging

from ..core.models import RoundRecord
from .loop_state import MissionContext

log = logging.getLogger(__name__)


class ReviewedRoundHooksMixin:
    """Reviewed-round hook + skill-maintenance phase methods for ``SkillLoop``."""

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
        if self.config.context_packet_path:
            try:
                from ..life.context_packet import record_reviewed_handoff

                record_reviewed_handoff(
                    mission_context_path=self.config.context_packet_path,
                    round_index=record.round_index,
                    engineer_summary=record.engineer_message,
                    review=record.review,
                    checkpoint_path=self.config.checkpoint_path,
                )
            except Exception:  # noqa: BLE001 - handoff persistence is fail-soft
                log.exception("failed to persist reviewed context packet")
