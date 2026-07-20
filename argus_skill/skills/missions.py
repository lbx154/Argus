"""Role-mission base class and the three concrete role missions.

A :class:`RoleMission` bundles the *policy* a role applies when it matches
skills: which on-disk pools are its own vs read-only references, which
builtin policies are already covered by the fixed prompt contract (and so must
be excluded from matching), and the role identity threaded into the
matcher prompt and cache. The actual matching/rendering primitive lives in
:mod:`argus_skill.skills.role_match`; the mission decides *how* it is called
for this role.

This keeps the three agents structurally uniform: each holds a mission and
calls ``mission.match(task)`` to obtain a :class:`RoleSkillMatch` whose
``primary`` skill (own-role only) drives distill-on-miss / writeback and
whose ``block`` is dropped into the prompt.
"""
from __future__ import annotations

from typing import Callable, ClassVar

from .role_match import RoleSkillMatch, match_role_skills
from .store import SkillStore


class RoleMission:
    """Base class for a single role's skill-matching mission.

    Subclasses set :attr:`role` and, optionally, :attr:`default_exclude`
    (builtin policies already represented in the fixed role prompt, so the
    matcher must never re-surface their long source skills). Everything else — pool scoping, primary/reference
    partitioning, token accounting — is inherited from the shared matcher.
    """

    #: Role identity. Subclasses MUST override.
    role: ClassVar[str] = ""
    #: Builtin policy files already represented by the compact fixed prompt and
    #: excluded from matching to avoid expensive duplicate injection.
    default_exclude: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        skill_store: SkillStore | None,
        *,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        if not self.role:
            raise TypeError(
                f"{type(self).__name__} must define a non-empty `role`"
            )
        self.skill_store = skill_store
        self.on_event = on_event

    def match(
        self,
        task: str,
        *,
        extra_exclude: set[str] | None = None,
        force_empty_match: bool = False,
    ) -> RoleSkillMatch:
        """Match skills for this mission's role against ``task``.

        ``extra_exclude`` adds to the role's :attr:`default_exclude` for this
        call. Returns an empty :class:`RoleSkillMatch` (no skills, empty
        block) when no store is wired or nothing clears the ``high`` bar.
        """
        exclude = set(self.default_exclude)
        if extra_exclude:
            exclude |= set(extra_exclude)
        return match_role_skills(
            self.skill_store,
            role=self.role,
            task=task,
            on_event=self.on_event,
            exclude_files=exclude or None,
            force_empty_match=force_empty_match,
        )


class EngineerMission(RoleMission):
    """Engineer role: own pool {engineer, general}, references {reviewer}."""

    role = "engineer"
    # The round prompt already carries the compact Engineer contract. Never let
    # the matcher re-inject the 14KB legacy role skill as a task playbook.
    default_exclude = frozenset({"argus-engineer-role.md"})


class ReviewerMission(RoleMission):
    """Reviewer role: own pool {reviewer}, references {engineer}.

    The Reviewer prompt carries compact equivalents of these fixed contracts;
    exclude their long source skills so the matcher cannot re-inject them.
    """

    role = "reviewer"
    default_exclude = frozenset({
        "argus-reviewer-role.md",
        "reviewer-engineer-handoff.md",
        "academic-paper-peer-review-benchmark.md",
    })


class PlannerMission(RoleMission):
    """Planner role: own pool {planner}, references {engineer, reviewer}.

    No ``builtin_skills/planner/`` OWN pool exists today, but the matchable pool
    UNIONs the cross-read references {engineer, reviewer} (non-empty), so this
    DOES fire a real matcher call and can surface engineer/reviewer skills to the
    planner as read-only references — it is not a no-op.
    """

    role = "planner"


class ManagerMission(RoleMission):
    """Manager role: own pool {manager}, references {engineer, reviewer, planner}.

    The Manager divides the task and owns the stage-transition / skill-approval
    decisions, so it benefits from seeing every other role's standards as
    read-only references. There is no on-disk OWN ``manager`` skill pool today
    (the fixed manager role skill is injected verbatim from ``builtin_skills``),
    but the matchable pool UNIONs the cross-read references, so ``match`` still
    fires a real matcher call and can surface engineer/reviewer/planner skills to
    the Manager as references. Self-evolution may add OWN manager skills later.
    """

    role = "manager"


__all__ = [
    "RoleMission",
    "EngineerMission",
    "ReviewerMission",
    "PlannerMission",
    "ManagerMission",
]
