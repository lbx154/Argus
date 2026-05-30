"""Shared skill-matching primitive for every role mission.

Engineer, reviewer, and planner are all uniform "missions": each one
matches the relevant skills for its role, injects the matched playbooks,
then runs and parses a role-specific verdict. This module owns the single
matcher entry point, the primary-vs-reference partitioning, and the
skill-playbook rendering that all three share, so skill selection is
normalized (role-scoped metadata-first matching for recall, with the
executing agent making the final relevance call).

Two skill classes come back from one matcher pass:

* **primary** skills live in the requesting role's own pool
  (:data:`store.ROLE_SKILL_POOLS`). These are the role's playbooks — the
  ``primary`` skill drives distill-on-miss and is the only writeback
  target.
* **reference** skills live in a cross-read pool
  (:data:`store.ROLE_CROSS_READ_POOLS`) — another role's standards,
  surfaced read-only so the role can anticipate them. They are NEVER
  treated as the role's own skill and NEVER written back to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .store import ROLE_SKILL_POOLS, Skill, SkillStore


@dataclass
class RoleSkillMatch:
    """Result of matching skills for one role mission.

    ``primary_skills`` are the role's own high-fit playbooks;
    ``reference_skills`` are cross-role standards surfaced read-only. The
    rendered ``block`` keeps the two visually separated. ``primary`` (the
    operative skill for distill-on-miss / writeback) is the first primary
    skill only — never a reference skill.
    """

    role: str
    primary_skills: list[Skill] = field(default_factory=list)
    reference_skills: list[Skill] = field(default_factory=list)
    block: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def primary(self) -> Skill | None:
        """The operative own-role skill (distill-on-miss / writeback target)."""
        return self.primary_skills[0] if self.primary_skills else None

    @property
    def skills(self) -> list[Skill]:
        """All matched skills (primary first, then references)."""
        return [*self.primary_skills, *self.reference_skills]


def partition_by_role(
    skill_store: SkillStore, role: str, skills: list[Skill]
) -> tuple[list[Skill], list[Skill]]:
    """Split matched skills into (primary, reference) by their on-disk role.

    A skill is *primary* when its role bucket is in the requesting role's
    own pool; otherwise it is a cross-role *reference*. Order within each
    group is preserved (matcher returns most-relevant first).
    """
    primary_pool = ROLE_SKILL_POOLS.get(role, frozenset({role, "general"}))
    primary: list[Skill] = []
    reference: list[Skill] = []
    for skill in skills:
        if skill_store.role_for(skill) in primary_pool:
            primary.append(skill)
        else:
            reference.append(skill)
    return primary, reference


def _render_own(skill_store: SkillStore, skills: list[Skill]) -> str:
    """Render the role's own high-fit skills (bare body for a single match)."""
    if not skills:
        return ""
    if len(skills) == 1:
        return skill_store.render_skill(skills[0])
    parts = [
        "The matcher found multiple high-fit skills. They are candidates, "
        "not orders: read each, apply the one(s) genuinely relevant to "
        "THIS task, and ignore any that do not fit.",
    ]
    for skill in skills:
        parts.append(
            f"### Candidate skill: {skill.name}\n"
            + skill_store.render_skill(skill)
        )
    return "\n\n".join(parts)


def render_skill_playbook(
    skill_store: SkillStore,
    primary_skills: list[Skill],
    reference_skills: list[Skill] | None = None,
) -> str:
    """Render matched skills into a role prompt.

    The role's own (primary) skills come first as the actionable playbook.
    Cross-role reference skills follow under a clearly framed header so the
    role treats them as context — another role's standards to anticipate —
    not as its own marching orders.
    """
    reference_skills = reference_skills or []
    own_block = _render_own(skill_store, primary_skills)
    if not reference_skills:
        return own_block

    ref_parts = [
        "## Reference: other-role skills",
        "The skills below belong to a DIFFERENT role. They are provided so "
        "you can anticipate that role's standards (e.g. the reviewer's "
        "acceptance bar) — they are NOT your own playbook and you must not "
        "execute them as your task.",
    ]
    for skill in reference_skills:
        ref_parts.append(
            f"### Reference skill: {skill.name}\n"
            + skill_store.render_skill(skill)
        )
    ref_block = "\n\n".join(ref_parts)
    return f"{own_block}\n\n{ref_block}" if own_block else ref_block


def match_role_skills(
    skill_store: SkillStore | None,
    *,
    role: str,
    task: str,
    on_event: Callable[[dict], None] | None = None,
    exclude_files: set[str] | None = None,
) -> RoleSkillMatch:
    """Run the role-scoped matcher and render the matched playbook block.

    Returns an empty match (no skills, empty block, zero tokens) when no
    store is wired or nothing clears the ``high`` bar. Callers treat an
    empty match as "no skill injected", never as a failure. Cross-role
    reference skills never populate ``primary`` — so distill-on-miss and
    writeback decisions key off own-role skills only.
    """
    if skill_store is None:
        return RoleSkillMatch(role=role)

    matched, _tokens = skill_store.find_relevant(
        task,
        on_event=on_event,
        role=role,
        exclude_files=exclude_files,
    )
    skills = list(matched) if matched else []
    primary, reference = partition_by_role(skill_store, role, skills)
    return RoleSkillMatch(
        role=role,
        primary_skills=primary,
        reference_skills=reference,
        block=render_skill_playbook(skill_store, primary, reference),
        input_tokens=int(getattr(skill_store, "last_match_input_tokens", 0) or 0),
        cached_input_tokens=int(
            getattr(skill_store, "last_match_cached_input_tokens", 0) or 0
        ),
        output_tokens=int(getattr(skill_store, "last_match_output_tokens", 0) or 0),
    )


__all__ = [
    "RoleSkillMatch",
    "match_role_skills",
    "partition_by_role",
    "render_skill_playbook",
]
