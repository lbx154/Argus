"""Learning vertical — self-CRUD of Argus's skill and wiki libraries from
operator-supplied learning material.

Deliverable is faithful, evidence-anchored library edits certified by the
reviewer — NOT a numeric metric and NOT a paper. See
``argus_skill.verticals.learning.stages`` for the stage graph, deterministic
completion checks, checklist, and role banners.
"""
from __future__ import annotations

from .stages import (
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    PROTECTED_SKILL_TAGS,
    STAGE_ORDER,
    completion_gate,
    role_banner,
    stage_completion_issues,
)

__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "PROTECTED_SKILL_TAGS",
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
    "stage_completion_issues",
]
