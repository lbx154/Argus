"""Planner sub-agent.

The continuous Planner directly edits and verifies the active project, then
reports completion through a small key-value footer mapped to
:class:`PlannerVerdict`.  The bounded handoff helper may still emit key-value
task blocks for Manager-authored DAGs.
"""

from .planner import (
    Planner,
    PlannerConfig,
    PlannerVerdict,
    TaskSpec,
    WaitingContract,
    parse_planner_text,
)

__all__ = [
    "Planner",
    "PlannerConfig",
    "PlannerVerdict",
    "TaskSpec",
    "WaitingContract",
    "parse_planner_text",
]
