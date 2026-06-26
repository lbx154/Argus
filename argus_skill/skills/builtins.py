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


def vertical_skill_source_path(vertical: str) -> Path:
    """Filesystem path of a vertical's own skills: ``verticals/<v>/skills``.

    The skill-layering convention: ``builtin_skills/`` holds only cross-vertical
    (general) skills, while each vertical ships its own domain skills under
    ``argus_skill/verticals/<vertical>/skills/{engineer,reviewer}/``. This is the
    version-controlled read-only SOURCE for that vertical's skills.
    """
    if not vertical or "/" in vertical or "\\" in vertical or vertical.startswith("."):
        raise ValueError(f"invalid vertical name: {vertical!r}")
    return Path(__file__).resolve().parents[1] / "verticals" / vertical / "skills"


def iter_vertical_skill_texts(vertical: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for a vertical's own skills.

    Relative names are rooted at the vertical's ``skills/`` dir (e.g.
    ``reviewer/quant-factor-report-review.md``) so they match the skill paths a
    vertical's ``REVIEWER_CHECKLISTS`` names verbatim and overlay the same
    ``<role>/<name>.md`` layout as the bundled builtins. Fail-open: an unknown
    vertical or one with no ``skills/`` dir yields nothing.
    """
    root = vertical_skill_source_path(vertical)
    if not root.is_dir():
        return
    yield from _iter_builtin_skill_resources(root)


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
        elif _is_bundled_script(prefix, entry.name):
            # Scripts that ship alongside a skill (e.g.
            # engineer/figure_spec_scripts/figure_renderer.py) live in
            # ``*_scripts/`` subdirs and are seeded verbatim so the
            # skill can invoke them in the project workspace.
            yield relative_name, entry.read_text(encoding="utf-8")


_BUNDLED_SCRIPT_EXTENSIONS = (".py", ".json", ".sh")


def _is_bundled_script(prefix: str, filename: str) -> bool:
    """A file is a bundled-script asset iff it lives under a
    ``*_scripts/`` directory and has a known script extension."""
    if not any(filename.endswith(ext) for ext in _BUNDLED_SCRIPT_EXTENSIONS):
        return False
    # ``prefix`` ends with "/" by construction; split into segments.
    segments = [s for s in prefix.split("/") if s]
    return any(seg.endswith("_scripts") for seg in segments)


# ---------------------------------------------------------------------------
# Domain registry
# ---------------------------------------------------------------------------

AVAILABLE_DOMAINS: dict[str, list[str]] = {}

DOMAIN_DESCRIPTIONS: dict[str, str] = {}



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
    """Return the number of bundled default skill markdown files.

    Skill *markdown* only — bundled scripts (e.g. figure_spec_scripts/
    figure_renderer.py) are excluded because they're assets attached
    to a skill, not skills in their own right.
    """
    return sum(1 for name, _ in iter_builtin_skill_texts() if name.endswith(".md"))


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
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True
    return created


def seed_builtin_skills_for_vertical(
    skills_dir: Path,
    vertical: str,
    *,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed COMMON builtins + a vertical's own skills into ``skills_dir``.

    Used to populate a mission's project workspace (``argus_builtin_skills/``) or
    the runtime vertical layer so the agent sees the cross-vertical skills PLUS
    the active vertical's domain skills. The vertical's real skill bodies
    OVERWRITE any same-path builtin stub (a moved domain skill leaves a pointer
    stub under ``builtin_skills/``; here the real body wins), so the workspace
    never carries the pointer.

    Note: this uses the FULL bundled set (``iter_builtin_skill_texts``), not
    ``iter_common_builtin_skill_texts`` — the latter skips the ``engineer/`` and
    ``reviewer/`` subdirectories, which is exactly where the cross-vertical
    skills live. Files the vertical will overwrite are skipped on the builtin
    pass so a pointer stub is never written into the workspace at all.

    Returns a map of relative filename → created/replaced (True) or skipped
    (False, an existing file left untouched because ``overwrite`` is False).
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}

    # The vertical's own skills (real bodies) — these always win over a builtin
    # stub of the same relative path.
    vertical_texts = dict(iter_vertical_skill_texts(vertical))

    # 1. Common/bundled builtins, skipping any path the vertical will overwrite
    #    (so a pointer stub is never written into the workspace).
    for filename, text in iter_builtin_skill_texts():
        if filename in vertical_texts:
            continue
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        if dest.exists() and not overwrite:
            created[filename] = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True

    # 2. The vertical's real skill bodies — always written so the agent gets the
    #    real body, never the stub (this is the point of vertical-aware seeding).
    for filename, text in vertical_texts.items():
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, text)
        created[filename] = True

    return created


def seed_vertical_layer(vertical: str, *, overwrite: bool = False) -> dict[str, bool]:
    """Seed a vertical's OWN skills into its runtime VERTICAL layer.

    Populates ``~/.argus-skill/verticals/<vertical>/skills/`` from the
    version-controlled source ``argus_skill/verticals/<vertical>/skills/``.
    Unlike :func:`seed_builtin_skills_for_vertical` (which mixes in the
    cross-vertical builtins to populate an agent WORKSPACE), this seeds ONLY the
    vertical's own domain skills into the middle layer of the three-layer skill
    library — the cross-vertical skills live in the global layer. Idempotent:
    existing files are preserved unless ``overwrite``.
    """
    from ..core.paths import skills_vertical_root

    target = skills_vertical_root(vertical)
    target.mkdir(parents=True, exist_ok=True)
    created: dict[str, bool] = {}
    for filename, text in iter_vertical_skill_texts(vertical):
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = target / filename
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
