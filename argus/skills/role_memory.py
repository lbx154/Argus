"""Small shared contract for role-owned Skill self-evolution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_ROLE_LABELS = {
    "self": "SELF",
    "engineer": "Engineer",
    "reviewer": "Reviewer",
    "planner": "Planner",
    "manager": "Manager",
}


def project_role_skill_dir(skill_store: Any, role: str) -> Path | None:
    """Return the project-layer directory owned by ``role``."""
    project_store = getattr(skill_store, "project", None)
    root = getattr(project_store, "skills_dir", None)
    if root is None:
        root = getattr(skill_store, "skills_dir", None)
    normalized = (role or "").strip().lower()
    if root is None or normalized not in _ROLE_LABELS:
        return None
    return Path(root).expanduser().resolve() / normalized


def profile_role_skill_dir(skill_store: Any, role: str) -> Path | None:
    """Return the cross-session profile directory owned by ``role``."""
    global_store = getattr(skill_store, "global_", None)
    root = getattr(global_store, "skills_dir", None)
    normalized = (role or "").strip().lower()
    if root is None or normalized not in _ROLE_LABELS:
        return None
    return Path(root).expanduser().resolve() / normalized


def profile_self_skill_dir(skill_store: Any) -> Path | None:
    """Return the cross-session SELF Skill directory."""
    return profile_role_skill_dir(skill_store, "self")


def role_skill_edit_rules(role: str, skill_dir: Path | str) -> str:
    """Render the shared agent-native edit rules for one role."""
    label = _ROLE_LABELS[(role or "").strip().lower()]
    return (
        f"{label} Skill directory (project layer only): {skill_dir}\n"
        "Inspect existing Markdown first. Each Skill has exactly `name` and "
        "`description` frontmatter followed by Markdown. Use an explicit semantic "
        "path, update a related Skill instead of duplicating it, and never write "
        "shared/global layers. Keep only reusable role learning here; "
        "when a shared project Wiki is listed in the prompt, route durable project "
        "facts, contracts, support limits, environment constraints, and scoped "
        "measurements to that Wiki instead."
    )


__all__ = [
    "profile_role_skill_dir",
    "profile_self_skill_dir",
    "project_role_skill_dir",
    "role_skill_edit_rules",
]
