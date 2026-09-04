"""Bounded Skill recall with agent-native discovery as a fallback."""
from __future__ import annotations

import json
import re
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
    recalled_paths: list[Path] = field(default_factory=list)
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


_TERM = re.compile(r"[a-z0-9][a-z0-9_.+-]*", re.I)
_STOP_TERMS = frozenset({
    "and", "are", "for", "from", "into", "not", "only", "that", "the",
    "this", "with", "工作", "任务", "优化", "运行",
})
_MAX_RECALLED_SKILLS = 4
_MAX_RECALLED_CHARS = 16_000


def _skill_header(path: Path) -> tuple[str, str]:
    try:
        prefix = path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return "", ""
    if not prefix.startswith("---\n"):
        return "", ""
    header, marker, _body = prefix[4:].partition("\n---")
    if not marker:
        return "", ""
    values: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, raw = line.partition(":")
        if not separator or key.strip() not in {"name", "description"}:
            continue
        value = raw.strip()
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = value
        if isinstance(decoded, str):
            values[key.strip()] = decoded.strip()
    return values.get("name", ""), values.get("description", "")


def _terms(text: str) -> set[str]:
    return {
        term
        for term in (match.group(0).casefold() for match in _TERM.finditer(text))
        if len(term) >= 3 and term not in _STOP_TERMS
    }


def _recalled_paths(paths: list[Path], task: str) -> list[Path]:
    task_terms = _terms(task)
    if not task_terms:
        return []
    ranked: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    for root in paths:
        for path in sorted(root.rglob("*.md")):
            resolved = path.resolve()
            if resolved in seen or path.name.casefold() == "index.md":
                continue
            seen.add(resolved)
            name, description = _skill_header(path)
            if not description:
                continue
            relative = path.relative_to(root).as_posix()
            descriptor_terms = _terms(f"{relative} {name} {description}")
            overlap = task_terms & descriptor_terms
            if not overlap:
                continue
            path_terms = _terms(relative)
            score = sum(len(term) for term in overlap)
            score += 4 * sum(len(term) for term in overlap & path_terms)
            ranked.append((-score, str(resolved), resolved))
    ranked.sort()
    return [path for _score, _label, path in ranked[:_MAX_RECALLED_SKILLS]]


def _render_recalled_skills(paths: list[Path]) -> str:
    if not paths:
        return ""
    sections = [
        "## Recalled Skills — mandatory execution context",
        "These Skills matched the current task. Apply their reusable constraints "
        "before repository work; current task and fresh evidence still take precedence.",
    ]
    used = sum(len(section) for section in sections)
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        remaining = _MAX_RECALLED_CHARS - used
        if remaining <= 0:
            break
        rendered = f"### `{path}`\n{text[:remaining]}"
        sections.append(rendered)
        used += len(rendered)
    return "\n\n".join(sections)


def render_skill_library_paths(
    skill_store: object | None,
    *,
    role: str,
    task: str = "",
    required_relative_paths: tuple[str, ...] = (),
) -> str:
    roots = skill_library_roots(skill_store)
    if not roots:
        return ""
    native_project_paths = _native_project_roots(skill_store, roots)
    role_scoped_roots = [root for root in roots if root not in native_project_paths]
    own_pools = ROLE_SKILL_POOLS.get(role, frozenset({role}))
    reference_pools = ROLE_CROSS_READ_POOLS.get(role, frozenset())
    own_paths = _pool_paths(role_scoped_roots, own_pools)
    reference_paths = _pool_paths(role_scoped_roots, reference_pools)
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
    recalled = _recalled_paths(
        [*native_project_paths, *own_paths, *reference_paths],
        task,
    )
    required_block = (
        "\nRequired for this mission (open these bodies before repository work):\n"
        + "\n".join(f"- `{path}`" for path in required)
        + "\n"
        if required
        else ""
    )
    discovery = (
        "## Skill libraries (on-demand)\n"
        f"Role: {role}. Order: project → vertical/domain → global; OWN > REFERENCE.\n"
        + "\n".join(lines)
        + required_block
        + "\n\nApply any recalled Skill bodies above before repository work. Then, "
        "only if the recalled set does not cover the task, make one native Skill "
        "decision: "
        "inspect the available descriptions (not every body), and immediately open "
        "only the body whose description clearly names this task's operation or "
        "artifact. Do not postpone this decision until after repository exploration. "
        "If native descriptions are unavailable, do one targeted filename/frontmatter "
        "search in OWN paths; on a miss, open nothing. Never scan all bodies or open "
        "adjacent Skills. Task, evidence, and role boundaries override Skills. These "
        "paths are the portable fallback; unmatched bodies are not injected. "
        "Re-probe mutable facts before use."
    )
    recalled_block = _render_recalled_skills(recalled)
    return recalled_block + ("\n\n" if recalled_block else "") + discovery


def role_skill_libraries(
    skill_store: object | None,
    *,
    role: str,
    task: str = "",
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
    recalled_paths = _recalled_paths(
        [*native_project_paths, *own_paths, *reference_paths],
        task,
    )
    if on_event is not None and roots:
        on_event(
            {
                "type": EventType.SKILL_LIBRARY_AVAILABLE,
                "role": role,
                "paths": [str(path) for path in roots],
                "own_paths": [str(path) for path in own_paths],
                "reference_paths": [str(path) for path in reference_paths],
                "required_paths": [str(path) for path in required_paths],
                "recalled_paths": [str(path) for path in recalled_paths],
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
        recalled_paths=recalled_paths,
        block=render_skill_library_paths(
            skill_store,
            role=role,
            task=task,
            required_relative_paths=required_relative_paths,
        ),
    )


__all__ = [
    "RoleSkillLibraries",
    "render_skill_library_paths",
    "role_skill_libraries",
    "skill_library_roots",
]
