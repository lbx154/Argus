"""Role wrappers for agent-native Skill-library discovery."""
from __future__ import annotations

from typing import Callable, ClassVar

from .role_library import RoleSkillLibraries, role_skill_libraries
from .store import SkillStore


class RoleMission:
    role: ClassVar[str] = ""
    default_exclude: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        skill_store: SkillStore | None,
        *,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        if not self.role:
            raise TypeError(f"{type(self).__name__} must define a role")
        self.skill_store = skill_store
        self.on_event = on_event

    def libraries(
        self,
        task: str = "",
        *,
        extra_exclude: set[str] | None = None,
        force_empty_match: bool = False,
        required_relative_paths: tuple[str, ...] = (),
    ) -> RoleSkillLibraries:
        _ = (extra_exclude, force_empty_match)
        return role_skill_libraries(
            self.skill_store,
            role=self.role,
            task=task,
            on_event=self.on_event,
            required_relative_paths=required_relative_paths,
        )


class EngineerMission(RoleMission):
    role = "engineer"


class ReviewerMission(RoleMission):
    role = "reviewer"


class PlannerMission(RoleMission):
    role = "planner"


class ManagerMission(RoleMission):
    role = "manager"


class SelfMission(RoleMission):
    role = "self"


__all__ = [
    "RoleMission",
    "SelfMission",
    "EngineerMission",
    "ReviewerMission",
    "PlannerMission",
    "ManagerMission",
]
