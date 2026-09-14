"""User-facing Manager control plane.

Layer: runtime

The Manager routes operator input, selects and persists mission verticals,
and decides stage transitions. Mission execution remains with the existing
LifeSupervisor engine.
"""
from __future__ import annotations

from ._core import Division, Manager, StageTransition
from ._session_ops import reset_manager_session
from .plan_challenge import PlanChallengeDecision, adjudicate_plan_challenge

__all__ = [
    "Manager",
    "Division",
    "StageTransition",
    "PlanChallengeDecision",
    "adjudicate_plan_challenge",
    "reset_manager_session",
]
