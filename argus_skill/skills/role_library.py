"""Agent-native Skill-library discovery.

Every role receives library roots and searches them with its own file tools. No
runtime component inspects the task or Skill documents before the Agent does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType


@dataclass
class RoleSkillLibraries:
    role: str
    library_roots: list[Path] = field(default_factory=list)
    block: str = ""


def skill_library_roots(skill_store: object | None) -> list[Path]:
    if skill_store is None:
        return []
    resolver = getattr(skill_store, "library_roots", None)
    if callable(resolver):
        roots = [Path(item).resolve() for item in resolver()]
    else:
        value = getattr(skill_store, "skills_dir", None)
        roots = [Path(value).resolve()] if value is not None else []
    result: list[Path] = []
    for root in roots:
        if root not in result:
            result.append(root)
    return result


def render_skill_library_paths(skill_store: object | None, *, role: str) -> str:
    roots = skill_library_roots(skill_store)
    if not roots:
        return ""
    listing = "\n".join(f"- `{root}`" for root in roots)
    return (
        "## Skill libraries (agent-native discovery)\n"
        f"Role: {role}\n"
        "Search these directories directly with your file/search tools:\n"
        f"{listing}\n\n"
        "Skill files contain only `name`, `description`, and Markdown guidance. "
        "Choose what to read yourself; a listed library is not evidence that a "
        "relevant Skill exists. Read files from their source paths and do not "
        "expect Skill bodies to be copied into this prompt. Skill guidance and "
        "historical traces about paths, GPU models, allocations, host access, "
        "tunnels, credentials, or service health do not prove current availability. "
        "Probe mutable runtime facts before depending on them; a failed probe means "
        "availability is unconfirmed, not that the resource does not exist."
    )


def role_skill_libraries(
    skill_store: object | None,
    *,
    role: str,
    on_event: Callable[[dict], None] | None = None,
) -> RoleSkillLibraries:
    roots = skill_library_roots(skill_store)
    if on_event is not None and roots:
        on_event(
            {
                "type": EventType.SKILL_LIBRARY_AVAILABLE,
                "role": role,
                "paths": [str(path) for path in roots],
                "text": "Skill library paths supplied for agent-native discovery",
            }
        )
    return RoleSkillLibraries(
        role=role,
        library_roots=roots,
        block=render_skill_library_paths(skill_store, role=role),
    )


__all__ = [
    "RoleSkillLibraries",
    "render_skill_library_paths",
    "role_skill_libraries",
    "skill_library_roots",
]
