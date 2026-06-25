"""argus — the slimmed Argus spine.

Manager → Planner → Engineer ↔ Reviewer, with the win defined by a FROZEN external
Judge the agents cannot touch, and a HypothesisTree carrying the lineage. This is the
target the old life/ + verticals/ + islands/ + meta/ machinery consolidates into.
See docs/ARCH.md.
"""
from .core import Task, Stage, Evidence, Verdict, Node, Skill
from .judge import FrozenJudge, JudgeConfig
from .tree import HypothesisTree
from .session import Session
from .roles import Manager, Planner, Engineer, Reviewer
from .orchestrator import Run, RunConfig

__all__ = [
    "Task", "Stage", "Evidence", "Verdict", "Node", "Skill",
    "FrozenJudge", "JudgeConfig", "HypothesisTree", "Session",
    "Manager", "Planner", "Engineer", "Reviewer", "Run", "RunConfig",
]
