"""fiction_writing vertical — narrative prose (short story / chapter) with a
consistent, structured story_state.

Re-exports the stage contract from :mod:`.stages` so
``argus_skill.verticals._base.load_vertical("fiction_writing")`` finds the
symbols it reads via ``getattr``. The shared narrative-state core (schemas +
safe patch engine) lives in :mod:`.state`.
"""
from __future__ import annotations

from .stages import (
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
    completion_gate,
    role_banner,
)

__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
]
