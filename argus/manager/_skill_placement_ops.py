"""The Manager's skill-placement judge (used by the Manager-as-janitor path)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skills import vertical_select


class _SkillPlacementMixin:
    """Mixin: skill placement."""

    project_root: Path
    learned_vertical_root: Path

    # ---- skill-library tidy-up (the Manager is the "janitor") ----
    def classify_skill_placement(self, *, content: str, task: str) -> Any:
        """Decide where a project-distilled skill belongs: global / a vertical /
        stay. Runs the placement judge on THIS Manager's runner with the known
        verticals as candidates. Returns a ``PlacementVerdict``."""
        from ..domains import BUILTIN_DOMAINS
        from .skill_review import classify_skill_placement as _classify

        return _classify(
            content=content,
            task=task,
            candidate_verticals=[*vertical_select.available_verticals(), *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )

    def classify_skill_placements(self, skills: list[dict[str, str]]) -> Any:
        """Batch variant used by shared runtime propagation."""
        from ..domains import BUILTIN_DOMAINS
        from .skill_review import classify_skill_placements as _classify_batch

        return _classify_batch(
            skills=skills,
            candidate_verticals=[*vertical_select.available_verticals(), *BUILTIN_DOMAINS],
            runner=(self._session or self.runner),
        )
