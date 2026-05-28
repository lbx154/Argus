"""Bundled default skills for new argus-skill homes.

The files under :mod:`argus_skill.builtin_skills` are argus-native
research/paper playbooks adapted from ARIS workflow concepts. They are
seeded into ``~/.argus-skill/skills`` on initialization so the agent can
start research and paper-writing missions before it has distilled its own
local skills.
"""
from __future__ import annotations

import os
import threading
import uuid
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

from .store import Skill

_BUILTIN_PACKAGE = "argus_skill.builtin_skills"
DEFAULT_PROJECT_BUILTIN_SKILLS_DIR = "argus_builtin_skills"


def builtin_skill_source_path() -> Path:
    """Return the filesystem path for bundled skill markdown when available."""
    return Path(__file__).resolve().parents[1] / "builtin_skills"


def iter_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for every bundled default skill."""
    root = resources.files(_BUILTIN_PACKAGE)
    yield from _iter_builtin_skill_resources(root)


def iter_common_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield top-level common skills, excluding domain-pack subdirectories."""
    root = resources.files(_BUILTIN_PACKAGE)
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")) or not entry.name.endswith(".md"):
            continue
        yield entry.name, entry.read_text(encoding="utf-8")


def _iter_builtin_skill_resources(
    root: Traversable,
    prefix: str = "",
) -> Iterable[tuple[str, str]]:
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")):
            continue
        relative_name = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _iter_builtin_skill_resources(entry, f"{relative_name}/")
        elif entry.name.endswith(".md"):
            yield relative_name, entry.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Domain registry
# ---------------------------------------------------------------------------

AVAILABLE_DOMAINS: dict[str, list[str]] = {}

DOMAIN_DESCRIPTIONS: dict[str, str] = {}


def list_domains() -> dict[str, str]:
    """Return available domains with descriptions."""
    return dict(DOMAIN_DESCRIPTIONS)


def iter_domain_skill_texts(domain: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for selected domain subdirectories."""
    if domain not in AVAILABLE_DOMAINS:
        raise ValueError(
            f"Unknown domain {domain!r}. Available: {list(AVAILABLE_DOMAINS.keys())}"
        )
    base = builtin_skill_source_path() / "domains"
    for subdir_name in AVAILABLE_DOMAINS[domain]:
        subdir = base / subdir_name
        if not subdir.is_dir():
            continue
        for entry in sorted(subdir.iterdir()):
            if entry.name.startswith(("_", ".")) or not entry.name.endswith(".md"):
                continue
            yield f"domains/{subdir_name}/{entry.name}", entry.read_text(encoding="utf-8")
    # Always include research-ops
    ops_dir = base / "research-ops"
    if ops_dir.is_dir():
        for entry in sorted(ops_dir.iterdir()):
            if entry.name.startswith(("_", ".")) or not entry.name.endswith(".md"):
                continue
            yield f"domains/research-ops/{entry.name}", entry.read_text(encoding="utf-8")


def seed_builtin_skills_for_domain(
    skills_dir: Path,
    domain: str,
    *,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed common skills + selected domain skills into ``skills_dir``.

    This prevents skill explosion by only loading domain-relevant skills.
    Common top-level skills (orchestration, paper writing, etc.) are always
    included. Domain-specific skills from ``domains/<subdir>/`` are added
    based on the chosen domain.

    Returns a map of filename → created (True) or skipped (False).
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}

    # 1. Always seed common top-level skills.
    for filename, text in iter_common_builtin_skill_texts():
        _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            created[filename] = False
            continue
        _atomic_write_text(dest, text)
        created[filename] = True

    # 2. Seed selected domain-specific skills under their original subdirectories.
    for rel_path, text in iter_domain_skill_texts(domain):
        _validate_builtin(rel_path, text)
        dest = skills_dir / rel_path
        if dest.exists() and not overwrite:
            created[rel_path] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[rel_path] = True

    return created


def builtin_skill_count() -> int:
    """Return the number of bundled default skills."""
    return sum(1 for _ in iter_builtin_skill_texts())


def seed_builtin_skills(skills_dir: Path, *, overwrite: bool = False) -> dict[str, bool]:
    """Seed bundled skills into ``skills_dir``.

    Existing files are preserved by default. The return value maps each
    bundled filename to ``True`` when it was created/replaced and ``False``
    when an existing user file was left untouched.
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}
    for filename, text in iter_builtin_skill_texts():
        _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True
    return created


def _validate_builtin(filename: str, text: str) -> None:
    skill = Skill.parse(text, filename)
    if not skill.name.strip():
        raise ValueError(f"bundled skill has no name: {filename}")
    if not skill.description.strip():
        raise ValueError(f"bundled skill has no description: {filename}")


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
