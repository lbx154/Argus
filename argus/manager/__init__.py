"""User-facing Manager control plane.

Layer: runtime

The Manager routes operator input, selects and persists mission verticals,
and decides stage transitions. Mission execution remains with the existing
LifeSupervisor engine.
"""
from __future__ import annotations

from ._core import Division, Manager, StageTransition
from ._session_ops import reset_manager_session
from ._stage_ops import fallback_project_report
from .plan_challenge import PlanChallengeDecision, adjudicate_plan_challenge

__all__ = [
    "Manager",
    "Division",
    "StageTransition",
    "PlanChallengeDecision",
    "adjudicate_plan_challenge",
    "fallback_project_report",
    "reset_manager_session",
]
