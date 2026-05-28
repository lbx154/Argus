"""Helpers for injecting bundled role-context skills into agent prompts."""
from __future__ import annotations

from importlib import resources

_BUILTIN_PACKAGE = "argus_skill.builtin_skills"


def load_builtin_skill_text(filename: str, fallback: str) -> str:
    """Load a skill by filename, searching top-level and subdirectories."""
    root = resources.files(_BUILTIN_PACKAGE)
    # Try direct path first (e.g. "reviewer/argus-reviewer-role.md")
    try:
        text = root.joinpath(filename).read_text(encoding="utf-8").strip()
        if text:
            return text
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        pass
    # Search subdirectories for bare filename
    for subdir in ("engineer", "reviewer"):
        try:
            text = (
                root.joinpath(subdir).joinpath(filename)
                .read_text(encoding="utf-8").strip()
            )
            if text:
                return text
        except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
            continue
    return fallback.strip()


def format_role_context(heading: str, filename: str, fallback: str) -> str:
    return f"{heading}:\n{load_builtin_skill_text(filename, fallback)}\n\n"
