"""Helpers for injecting bundled role-context skills into agent prompts."""
from __future__ import annotations

from importlib import resources

_BUILTIN_PACKAGE = "argus_skill.builtin_skills"


def load_builtin_skill_text(filename: str) -> str:
    """Load a required bundled role skill.

    A missing role skill is a packaging error, not a recoverable model outcome.
    Failing loudly prevents a stale inline fallback from becoming a second,
    silently divergent source of role policy.
    """
    root = resources.files(_BUILTIN_PACKAGE)
    candidates = [root.joinpath(filename)]
    if "/" not in filename:
        candidates.extend(
            root.joinpath(subdir).joinpath(filename)
            for subdir in ("engineer", "reviewer", "planner", "manager", "curator")
        )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if not text:
            raise RuntimeError(f"required bundled role skill is empty: {filename}")
        return text
    raise FileNotFoundError(f"required bundled role skill not found: {filename}")
