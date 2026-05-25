"""Helpers for injecting bundled role-context skills into agent prompts."""
from __future__ import annotations

from importlib import resources

_BUILTIN_PACKAGE = "argus_skill.builtin_skills"


def load_builtin_skill_text(filename: str, fallback: str) -> str:
    try:
        text = (
            resources.files(_BUILTIN_PACKAGE)
            .joinpath(filename)
            .read_text(encoding="utf-8")
            .strip()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return fallback.strip()
    return text or fallback.strip()


def format_role_context(heading: str, filename: str, fallback: str) -> str:
    return f"{heading}:\n{load_builtin_skill_text(filename, fallback)}\n\n"
