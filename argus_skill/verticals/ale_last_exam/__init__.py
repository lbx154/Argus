"""Agents' Last Exam vertical.

This is a single-stage, artifact-delivery vertical for ALE's hardest
long-horizon computer-use tasks.  It deliberately has no paper or metric-search
pipeline: the benchmark's hidden evaluator scores the final deliverables after
the agent exits.
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
