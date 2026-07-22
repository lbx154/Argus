"""Typed request/result objects for role prompt composition."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RoleName(str, Enum):
    MANAGER = "manager"
    PLANNER = "planner"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"


class ChecklistMode(str, Enum):
    NONE = "none"
    STAGE = "stage"
    FULL_PIPELINE = "full_pipeline"
    AUTO = "auto"


@dataclass(frozen=True)
class RolePromptRequest:
    """Structured prompt selection inputs.

    ``operation`` names a role-owned prompt path. ``banner_role`` lets the
    Engineer-owned Skill Scientist request its existing vertical overlays
    without pretending Scientist is a fifth persistent role.
    """

    role: RoleName
    operation: str
    project_root: Path | str | None = None
    vertical: str | None = None
    banner_role: str | None = None
    stage: str | None = None
    scope: str = ""
    checklist_mode: ChecklistMode = ChecklistMode.NONE
    checklist_role: RoleName | None = None
    include_search_altitude: bool = False


@dataclass(frozen=True)
class ResolvedRolePrompt:
    """Role prompt hyperparameters resolved from one authoritative catalog."""

    role: RoleName
    operation: str
    vertical: str
    banner_role: str
    stage: str
    scope: str
    role_banner: str
    stage_checklist: str
    stage_order: tuple[str, ...]
    completion_gate: str
    workflow_mode: str
    requires_independent_review: bool
    search_altitude: str
    fragment_ids: tuple[str, ...]

    @property
    def full_paper(self) -> bool:
        return self.completion_gate == "full_paper"

    def prepend_role_banner(
        self,
        prompt: str,
        *,
        heading: str = "## Active vertical role",
    ) -> str:
        banner = self.role_banner.strip()
        if not banner:
            return prompt
        return f"{heading}\n{banner}\n\n{prompt}"


__all__ = [
    "ChecklistMode",
    "ResolvedRolePrompt",
    "RoleName",
    "RolePromptRequest",
]
