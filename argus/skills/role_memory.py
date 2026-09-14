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

# What a role can durably learn follows from what it decides. Engineer and
# Manager own repeatable procedure. Planner owns judgement, and asking it for a
# procedure discards its real lesson as "generic advice" — precisely the
# campaign-level learning that would question a stalled plan sooner. The
# Reviewer has no entry on purpose: it never edits Skills during a review, so
# its learning is written later by the independent post-mission pass.
_ROLE_LESSONS = {
    "planner": (
        "a strategic decision heuristic — the campaign pattern behind it, the "
        "question it should raise next time, where it applies, and one case "
        "where following it would be wrong"
    ),
}
_DEFAULT_LESSON = "a reusable procedure"


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
    normalized = (role or "").strip().lower()
    label = _ROLE_LABELS[normalized]
    return (
        f"## {label} self-evolution\n"
        f"Before finishing this {label} turn, retain "
        f"{_ROLE_LESSONS.get(normalized, _DEFAULT_LESSON)} "
        "only when it would materially improve a future turn of the same role. "
        "Do not store this task's history, outcome, or advice that names no "
        "evidence.\n"
        f"{role_skill_edit_rules(role, skill_dir)}\n"
        "If there is no durable role-specific learning, make no Skill edit.\n\n"
    )


__all__ = [
    "profile_role_skill_dir",
    "profile_self_skill_dir",
    "project_role_skill_dir",
    "role_skill_edit_rules",
    "role_skill_maintenance_enabled",
    "role_skill_maintenance_block",
]
