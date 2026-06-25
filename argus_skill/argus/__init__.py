"""argus — the slimmed Argus spine.

Manager (user-facing conductor) → Planner → Engineer ↔ Reviewer, with the win defined
by a FROZEN external Judge the agents cannot touch, a HypothesisTree carrying the
lineage, a Skill library (Trace-distilled, SkillSelector-chosen), a project Wiki
(research-first), and a Session that rolls over on token budget via checkpoint.json.
This is the target the old life/ + verticals/ + islands/ + meta/ machinery
consolidates into. See docs/ARCH.md.
"""
from .core import Task, Stage, Evidence, Verdict, Node, Skill
from .judge import FrozenJudge, JudgeConfig
from .tree import HypothesisTree
from .session import Session
from .skills import SkillStore, SkillSelector
from .wiki import Wiki, research
from .roles import Planner, Engineer, Reviewer, triage, split_stages, audit_skill
from .manager import Manager
from .orchestrator import Run, RunConfig

__all__ = [
    "Task", "Stage", "Evidence", "Verdict", "Node", "Skill",
    "FrozenJudge", "JudgeConfig", "HypothesisTree", "Session",
    "SkillStore", "SkillSelector", "Wiki", "research",
    "Planner", "Engineer", "Reviewer", "triage", "split_stages", "audit_skill",
    "Manager", "Run", "RunConfig",
]
