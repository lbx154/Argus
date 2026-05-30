"""Shared skill-matching primitive for every role mission.

Engineer, reviewer, and planner are all uniform "missions": each one
matches the relevant skills for its role, injects the matched playbooks,
then runs and parses a role-specific verdict. This module owns the single
matcher entry point and skill-playbook rendering that all three share, so
skill selection is normalized (role-scoped metadata-first matching for
recall, with the executing agent making the final relevance call).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .store import Skill, SkillStore


@dataclass
class RoleSkillMatch:
    """Result of matching skills for one role mission."""

    role: str
    skills: list[Skill] = field(default_factory=list)
    block: str = ""  # rendered "## Skill playbook" body ("" when no match)
    primary: Skill | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


def render_skill_playbook(skill_store: SkillStore, skills: list[Skill]) -> str:
    """Render matched skills into the engineer/reviewer/planner prompt.

    The matcher returns every skill that independently clears the ``high``
    bar. We inject all of them and let the executing agent make the final
    relevance call for THIS task instead of pre-committing to a single
    match. A single match renders as the bare body (back-compat); multiple
    matches are framed as candidates under per-skill headers.
    """
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
    empty match as "no skill injected", never as a failure.
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
    return RoleSkillMatch(
        role=role,
        skills=skills,
        block=render_skill_playbook(skill_store, skills),
        primary=skills[0] if skills else None,
        input_tokens=int(getattr(skill_store, "last_match_input_tokens", 0) or 0),
        cached_input_tokens=int(
            getattr(skill_store, "last_match_cached_input_tokens", 0) or 0
        ),
        output_tokens=int(getattr(skill_store, "last_match_output_tokens", 0) or 0),
    )


__all__ = ["RoleSkillMatch", "match_role_skills", "render_skill_playbook"]
