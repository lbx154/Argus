"""Per-mission state for agent-native Skill-library discovery."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MissionContext:
    workdir: Path
    run_id: str
    task: str
    skill_task: str
    request_anchor: str
    active_vertical: str
    engineer_role_banner: str
    seed_thread_id: str | None
    scope: str
    work_kind: str = ""


@dataclass
class SkillLibraryState:
    """Only path-discovery context is carried between mission phases."""

    reviewer_skill_block: str = ""
    skill_text: str = ""
    skill_libraries: Any = None
    # Compatibility for the protected Playground boundary; no Skill is selected.
    skill: Any = None
