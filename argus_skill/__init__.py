"""argus-skill: supervised skill-driven coding agent.

Public API:
    SkillLoop      — the integrated matcher → distiller → engineer-with-reviewer loop.
    SkillStore     — on-disk markdown skill cache with LLM matcher.
    LoopOutcome    — result of a SkillLoop.run() invocation.
    RunnerBackend  — protocol any LLM backend must implement.
"""
from __future__ import annotations

from .core.models import (
    CheckResult,
    LoopOutcome,
    ReviewDecision,
    RunnerOptions,
    RunnerResult,
)
from .core.ports import RunnerBackend, SkillSource
from .loop import SkillLoop, SkillLoopConfig
from .skills.store import Skill, SkillStore

__all__ = [
    "CheckResult",
    "LoopOutcome",
    "ReviewDecision",
    "RunnerBackend",
    "RunnerOptions",
    "RunnerResult",
    "Skill",
    "SkillLoop",
    "SkillLoopConfig",
    "SkillSource",
    "SkillStore",
]

__version__ = "0.1.0"
