"""Role missions — the uniform structure every agent role is built on.

Planner, engineer, and reviewer are all *missions*: each one matches the
skills for its role over a role-scoped pool, injects the matched playbook,
then runs and parses a role-specific verdict. This package owns the shared
mission scaffold so the three roles stay structurally identical where they
should be (skill matching, role identity, token accounting) and differ only
where they must (single-shot vs round-loop driving, verdict schema).

``RoleMission`` is the base; ``EngineerMission`` / ``ReviewerMission`` /
``PlannerMission`` are thin subclasses that fix the role and its policy
(primary + cross-read pools, default skill exclusions). The matcher itself
lives in :mod:`argus_skill.skills.role_match`; missions own the *policy*
around it.
"""
from __future__ import annotations

from .base import EngineerMission, PlannerMission, ReviewerMission, RoleMission

__all__ = [
    "RoleMission",
    "EngineerMission",
    "ReviewerMission",
    "PlannerMission",
]
