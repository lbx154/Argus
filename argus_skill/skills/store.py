"""Minimal, agent-readable Skill library.

A Skill is a Markdown document with exactly two frontmatter fields::

    ---
    name: <semantic name>
    description: <short description>
    ---

    # Title

    Markdown body

The runtime deliberately does not parse Skill documents.  Agents receive library
paths and use their own file/search tools to discover and read relevant Skills.
There is no matcher, programmatic Skill identity, reuse counter, fingerprint, or
content digest in this module.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# These constants remain as path-scope vocabulary for callers that organise
# role-specific libraries.  They no longer drive a matcher.
ROLE_SKILL_POOLS: dict[str, frozenset[str]] = {
    "engineer": frozenset({"engineer", "general"}),
    "reviewer": frozenset({"reviewer"}),
    "planner": frozenset({"planner"}),
    "manager": frozenset({"manager"}),
}
ROLE_CROSS_READ_POOLS: dict[str, frozenset[str]] = {
    "engineer": frozenset({"reviewer"}),
    "reviewer": frozenset({"engineer"}),
    "planner": frozenset({"engineer", "reviewer"}),
    "manager": frozenset({"engineer", "reviewer", "planner"}),
}
_ROLE_SUBDIRS = frozenset({"engineer", "reviewer", "planner", "manager"})


def role_of_path(path: str | os.PathLike[str], skills_dir: Path) -> str:
    """Return the role represented by the first semantic path component."""
    try:
        parts = Path(path).resolve().relative_to(Path(skills_dir).resolve()).parts
    except ValueError:
        parts = Path(path).parts
    return parts[0] if len(parts) > 1 and parts[0] in _ROLE_SUBDIRS else "general"


@dataclass
class Skill:
    """An in-memory Skill authored by an Agent.

    ``path`` is an explicit semantic destination.  There is intentionally no
    parser: persisted documents are read by Agents, not converted back into
    this object by the runtime.
    """

    name: str
    description: str
    content: str
    path: str = ""

    def render(self) -> str:
        # JSON string literals are valid YAML scalars and safely preserve colons,
        # quotes, and non-ASCII text without expanding the schema.
        return (
            "---\n"
            f"name: {json.dumps(self.name, ensure_ascii=False)}\n"
            f"description: {json.dumps(self.description, ensure_ascii=False)}\n"
            "---\n\n"
            f"{self.content.rstrip()}\n"
        )


class SkillStore:
    """Path-only view of an on-disk Skill library.

    The store can enumerate paths and atomically write an explicitly addressed
    document.  It never parses documents, chooses a Skill, injects Skill text,
    creates names, records outcomes, or mutates metadata.
    """

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def library_roots(self) -> list[Path]:
        return [self.skills_dir.resolve()]

    def iter_paths(self) -> Iterable[Path]:
        """Yield active semantic Skill paths without reading their contents."""
        for path in sorted(self.skills_dir.rglob("*.md")):
            rel = path.relative_to(self.skills_dir)
            if path.name.casefold() == "index.md":
                continue
            if any(
                part.startswith(".")
                or part in {"_archive", "_history", "_shared_verticals"}
                for part in rel.parts
            ):
                continue
            yield path

    def list_paths(self) -> list[str]:
        return [str(path.resolve()) for path in self.iter_paths()]

    def list_summaries(self) -> list[dict[str, object]]:
        """Compatibility path listing; it does not inspect Skill Markdown.

        Callers that need semantic meaning must hand ``path`` to an Agent.  The
        relative path is exposed as a navigation label only, not as parsed Skill
        metadata.
        """
        rows: list[dict[str, object]] = []
        for path in self.iter_paths():
            rel = path.relative_to(self.skills_dir)
            rows.append(
                {
                    "name": rel.with_suffix("").as_posix(),
                    "description": "",
                    "path": str(path.resolve()),
                    "role": role_of_path(path, self.skills_dir),
                }
            )
        return rows

    def save(self, skill: Skill) -> Path:
        """Write one explicitly named semantic Skill path.

        No path is derived from ``name``.  This prevents numbered fallbacks and
        other programmatic knowledge names.
        """
        if not str(skill.path or "").strip():
            raise ValueError("an Agent-authored semantic Skill path is required")
        path = Path(skill.path)
        if not path.is_absolute():
            path = self.skills_dir / path
        resolved_root = self.skills_dir.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"Skill path must stay inside {resolved_root}") from exc
        if resolved_path.suffix.casefold() != ".md":
            raise ValueError("Skill path must end in .md")
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved_path.with_name(
            f".{resolved_path.name}.writing-{os.getpid()}-{threading.get_ident()}"
        )
        try:
            temporary.write_text(skill.render(), encoding="utf-8")
            os.replace(temporary, resolved_path)
        finally:
            temporary.unlink(missing_ok=True)
        skill.path = str(resolved_path)
        return resolved_path

    def archive_path(self, path: str | os.PathLike[str]) -> Path:
        """Move a semantic Skill path to ``_archive`` without parsing it."""
        source = Path(path).resolve()
        relative = source.relative_to(self.skills_dir.resolve())
        destination = self.skills_dir / "_archive" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(
                f"semantic archive destination already exists: {destination}"
            )
        os.replace(source, destination)
        return destination


__all__ = [
    "ROLE_CROSS_READ_POOLS",
    "ROLE_SKILL_POOLS",
    "Skill",
    "SkillStore",
    "role_of_path",
]
