"""argus.manager — user-facing Manager that divides a Task into Stages.

See ``_core.py``. The Manager classifies the task into a vertical (research /
optimize), splits it into that vertical's Stage template, and commits the choice
so the existing LifeSupervisor engine executes it stage-by-stage.
"""
from __future__ import annotations

from ._core import Division, Manager, StageTransition, reset_manager_session

__all__ = ["Manager", "Division", "StageTransition", "reset_manager_session"]
