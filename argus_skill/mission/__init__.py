"""argus-skill's own mission-loop machinery.

This package replaces the upstream ``codex_autoloop.core.engine.LoopEngine``
+ ``codex_autoloop.reviewer.Reviewer`` + ``codex_autoloop.planner.Planner``
trio for argus-skill missions. We still reuse upstream's ``LoopStateStore``
(plain persistence — no prompt opinions) and the upstream model dataclasses
(``RoundSummary``, ``PlanDecision``, ``ReviewDecision``) so existing
mission-runtime / chat-app wiring keeps working unchanged.

The motivation: upstream's prompts are SWE-bench-flavoured ("must do at
least one concrete repo action", "end with DONE/REMAINING/BLOCKERS",
"generic role acknowledgment without concrete repository work" guard).
Those are right for a coding-bench harness but wrong for a general-purpose
agent that may also need to chat, answer a question, draft a doc, etc.
This package's prompts are deliberately neutral: state the objective,
hand it the prior reviewer feedback if any, and let the engineer decide
the right output shape from the task text.
"""

from .engine import (
    MissionLoopConfig,
    MissionLoopEngine,
    MissionLoopResult,
)
from .planner import MissionPlanner, MissionPlannerConfig
from .reviewer import MissionReviewer, MissionReviewerConfig

__all__ = [
    "MissionLoopConfig",
    "MissionLoopEngine",
    "MissionLoopResult",
    "MissionPlanner",
    "MissionPlannerConfig",
    "MissionReviewer",
    "MissionReviewerConfig",
]
