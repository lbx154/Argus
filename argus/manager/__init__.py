"""User-facing Manager control plane.

Layer: runtime

The Manager routes operator input, selects and persists mission verticals,
and debugs a stuck process; stages move through the completion gate. Mission execution remains with the existing
LifeSupervisor engine.
"""
from __future__ import annotations

from ._core import Division, Manager, StageTransition
from ._session_ops import reset_manager_session

__all__ = [
    "Manager",
    "Division",
    "StageTransition",
    "reset_manager_session",
]
