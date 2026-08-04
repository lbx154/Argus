"""Small shared contract for role-owned Skill self-evolution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_ROLE_LABELS = {
    "engineer": "Engineer",
    "reviewer": "Reviewer",
    "planner": "Planner",
    "manager": "Manager",
}


def role_skill_maintenance_enabled() -> bool:
    """Resolve the existing post-task-learning A/B switch."""
    from ..core.knobs import resolve_knob

    value = resolve_knob("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "1").value
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


def role_skill_edit_rules(role: str, skill_dir: Path | str) -> str:
    """Render the common, intentionally short direct-edit rules."""
    label = _ROLE_LABELS[(role or "").strip().lower()]
    return (
        f"{label} skill directory (project layer only): {skill_dir}\n"
        "Inspect existing Markdown first. Create or update the distinct "
        "role-specific Skills justified by the learning; prefer updating a related "
        "Skill over duplicating it. Preserve valid frontmatter, increment `version` "
        "on update, never modify `protected: true`, and never write shared/global "
        "layers."
    )


def role_skill_maintenance_block(
    skill_store: Any,
    role: str,
    *,
    enabled: bool,
) -> str:
    """Give one role a selective, direct self-evolution path."""
    if not enabled:
        return ""
    skill_dir = project_role_skill_dir(skill_store, role)
    if skill_dir is None:
        return ""
    label = _ROLE_LABELS[(role or "").strip().lower()]
    return (
        f"## {label} self-evolution\n"
        f"Before finishing this {label} turn, retain a durable reusable procedure "
        "only when it would materially improve a future turn of the same role. "
        "Do not store this task's history, outcome, or generic advice.\n"
        f"{role_skill_edit_rules(role, skill_dir)}\n"
        "If there is no durable role-specific learning, make no Skill edit.\n\n"
    )


__all__ = [
    "project_role_skill_dir",
    "role_skill_edit_rules",
    "role_skill_maintenance_enabled",
    "role_skill_maintenance_block",
]
