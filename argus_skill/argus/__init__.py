"""argus — the slimmed Argus spine.

Manager (user-facing conductor) → Planner → Engineer ↔ Reviewer, with the win defined
by a FROZEN external Judge the agents cannot touch, a flat Journal carrying the attempt
log, a Skill library (Trace-distilled, SkillSelector-chosen), a project Wiki
(research-first), and a Session that rolls over on token budget via checkpoint.json.
This is the target the old life/ + verticals/ + islands/ + meta/ machinery
consolidates into. See docs/ARCH.md.
"""
from .core import Task, Stage, Evidence, Verdict, Node, Skill
from .judge import FrozenJudge, JudgeConfig
from .session import Session, Journal
from .skills import SkillStore, SkillSelector
from .wiki import Wiki, research
from .roles import Planner, Engineer, Reviewer, triage, split_stages, audit_skill
from .manager import Manager
from .orchestrator import Run, RunConfig

__all__ = [
    "Task", "Stage", "Evidence", "Verdict", "Node", "Skill",
    "FrozenJudge", "JudgeConfig", "Session", "Journal",
    "SkillStore", "SkillSelector", "Wiki", "research",
    "Planner", "Engineer", "Reviewer", "triage", "split_stages", "audit_skill",
    "Manager", "Run", "RunConfig",
]
