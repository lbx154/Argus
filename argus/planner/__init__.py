"""Read-only Planner sub-agent that delegates implementation to Engineer.

Layer: roles
"""

from .planner import (
    NO_CONCRETE_TASKS_ERROR,
    OPEN_ENDED_PROJECT_DONE_ERROR,
    PLANNER_SUPERSEDED_ERROR,
    Planner,
    PlannerConfig,
    PlannerVerdict,
    TaskSpec,
    parse_planner_text,
)

__all__ = [
    "Planner",
    "PlannerConfig",
    "PlannerVerdict",
    "NO_CONCRETE_TASKS_ERROR",
    "OPEN_ENDED_PROJECT_DONE_ERROR",
    "PLANNER_SUPERSEDED_ERROR",
    "TaskSpec",
    "parse_planner_text",
]
