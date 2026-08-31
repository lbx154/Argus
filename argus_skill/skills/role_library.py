"""Agent-native, on-demand Skill discovery.

Roles receive ordered library paths and decide what to inspect with their own
tools. The runtime does not parse, match, rank, rewrite, or inject Skill bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType
from .store import ROLE_CROSS_READ_POOLS, ROLE_SKILL_POOLS


@dataclass
class RoleSkillLibraries:
    role: str
    library_roots: list[Path] = field(default_factory=list)
    own_paths: list[Path] = field(default_factory=list)
    reference_paths: list[Path] = field(default_factory=list)
    native_paths: list[Path] = field(default_factory=list)
    required_paths: list[Path] = field(default_factory=list)
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
    return list(dict.fromkeys(roots))


def _native_project_roots(
    skill_store: object | None,
    roots: list[Path],
) -> list[Path]:
    if skill_store is None:
        return []
    resolver = getattr(skill_store, "native_project_roots", None)
    if not callable(resolver):
        return []
    available = set(roots)
    return list(
        dict.fromkeys(
            Path(item).resolve()
            for item in resolver()
            if Path(item).resolve() in available
        )
    )


def _pool_paths(roots: list[Path], pools: frozenset[str]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        for pool in sorted(pools):
            path = root if pool == "general" else root / pool
            if pool == "general" and not any(
                item.is_file() and item.name.casefold() != "index.md"
                for item in root.glob("*.md")
            ):
                continue
            if path.exists() and path not in paths:
                paths.append(path)
    return paths


def _required_paths(
    roots: list[Path],
    relative_paths: tuple[str, ...],
) -> list[Path]:
    required: list[Path] = []
    for relative in relative_paths:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"required Skill path must be relative: {relative!r}")
        for root in roots:
            path = root / candidate
            if path.is_file():
                required.append(path)
                break
    return required


def render_skill_library_paths(
    skill_store: object | None,
    *,
    role: str,
    required_relative_paths: tuple[str, ...] = (),
) -> str:
    roots = skill_library_roots(skill_store)
    if not roots:
        return ""
    own_pools = ROLE_SKILL_POOLS.get(role, frozenset({role}))
    reference_pools = ROLE_CROSS_READ_POOLS.get(role, frozenset())
    lines = []
    for index, root in enumerate(roots, 1):
        own = ", ".join(
            "root" if pool == "general" else pool for pool in sorted(own_pools)
        )
        references = ", ".join(sorted(reference_pools)) or "none"
        lines.append(
            f"{index}. `{root}` (OWN: {own}; REFERENCE only: {references})"
        )
    required = _required_paths(roots, required_relative_paths)
    required_block = (
        "\nRequired for this mission (open these bodies before repository work):\n"
        + "\n".join(f"- `{path}`" for path in required)
        + "\n"
        if required
        else ""
    )
    return (
        "## Skill libraries (on-demand)\n"
        f"Role: {role}. Order: project → vertical/domain → global; OWN > REFERENCE.\n"
        + "\n".join(lines)
        + required_block
        + "\n\nYour first action, before any repository tool, is one native Skill decision: "
        "inspect the available descriptions (not every body), and immediately open "
        "only the body whose description clearly names this task's operation or "
        "artifact. Do not postpone this decision until after repository exploration. "
        "If native descriptions are unavailable, do one targeted filename/frontmatter "
        "search in OWN paths; on a miss, open nothing. Never scan all bodies or open "
        "adjacent Skills. Task, evidence, and role boundaries override Skills. These "
        "paths are the portable fallback; bodies are not injected. Re-probe mutable "
        "facts before use."
    )


def role_skill_libraries(
    skill_store: object | None,
    *,
    role: str,
    on_event: Callable[[dict], None] | None = None,
    required_relative_paths: tuple[str, ...] = (),
) -> RoleSkillLibraries:
    roots = skill_library_roots(skill_store)
    native_project_paths = _native_project_roots(skill_store, roots)
    role_scoped_roots = [root for root in roots if root not in native_project_paths]
    own_paths = _pool_paths(
        role_scoped_roots,
        ROLE_SKILL_POOLS.get(role, frozenset({role})),
    )
    reference_paths = _pool_paths(
        role_scoped_roots,
        ROLE_CROSS_READ_POOLS.get(role, frozenset()),
    )
    required_paths = _required_paths(roots, required_relative_paths)
    if on_event is not None and roots:
        on_event(
            {
                "type": EventType.SKILL_LIBRARY_AVAILABLE,
                "role": role,
                "paths": [str(path) for path in roots],
                "own_paths": [str(path) for path in own_paths],
                "reference_paths": [str(path) for path in reference_paths],
                "required_paths": [str(path) for path in required_paths],
                "precedence": ["project", "vertical", "global"],
                "discovery": "native-or-path-fallback",
                "text": "Skill library paths supplied for on-demand discovery",
            }
        )
    return RoleSkillLibraries(
        role=role,
        library_roots=roots,
        own_paths=own_paths,
        reference_paths=reference_paths,
        native_paths=list(
            dict.fromkeys([*native_project_paths, *own_paths, *reference_paths])
        ),
        required_paths=required_paths,
        block=render_skill_library_paths(
            skill_store,
            role=role,
            required_relative_paths=required_relative_paths,
        ),
    )


__all__ = [
    "RoleSkillLibraries",
    "render_skill_library_paths",
    "role_skill_libraries",
    "skill_library_roots",
]
