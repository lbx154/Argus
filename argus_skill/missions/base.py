"""Role-mission base class and the three concrete role missions.

A :class:`RoleMission` bundles the *policy* a role applies when it matches
skills: which on-disk pools are its own vs read-only references, which
builtin skills it already injects verbatim (and so must exclude from the
matcher to avoid double-injection), and the role identity threaded into the
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

from ..skills.role_match import RoleSkillMatch, match_role_skills
from ..skills.store import SkillStore


class RoleMission:
    """Base class for a single role's skill-matching mission.

    Subclasses set :attr:`role` and, optionally, :attr:`default_exclude`
    (builtin skills the role injects verbatim elsewhere, so the matcher must
    never re-surface them). Everything else — pool scoping, primary/reference
    partitioning, token accounting — is inherited from the shared matcher.
    """

    #: Role identity. Subclasses MUST override.
    role: ClassVar[str] = ""
    #: Builtin skill filenames this role injects verbatim and so excludes
    #: from its matcher pool (avoids double-injection). Subclasses may set.
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
        )


class EngineerMission(RoleMission):
    """Engineer role: own pool {engineer, general}, references {reviewer}."""

    role = "engineer"


class ReviewerMission(RoleMission):
    """Reviewer role: own pool {reviewer}, references {engineer}.

    The three reviewer skills that ``Reviewer`` injects verbatim into every
    prompt are excluded from the matcher so they are never double-injected.
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


__all__ = [
    "RoleMission",
    "EngineerMission",
    "ReviewerMission",
    "PlannerMission",
]
