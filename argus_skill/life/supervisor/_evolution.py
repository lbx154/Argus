"""Budgeted post-mission source promotion for runtime-evolved skills."""

from __future__ import annotations

import logging
import os
from typing import Any

from ...core.mission_budget import build_mission_budget_guard

log = logging.getLogger(__name__)


def _per_mission_distill_enabled() -> bool:
    """Whether successful missions may promote runtime skills into source."""
    return os.environ.get("ARGUS_SKILL_PER_MISSION_DISTILL", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class EvolutionMixin:
    def _evolve_runtime_skills_after_mission(
        self,
        *,
        success: bool,
        usage_mission_id: str,
        mission_budget: Any,
    ) -> dict[str, int]:
        """Run opted-in source promotion inside the mission ledger and cap."""
        if not success or not _per_mission_distill_enabled():
            return {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}

        set_usage = getattr(self.runner, "_set_usage_context", None)
        set_guard = getattr(self.runner, "_set_budget_guard", None)
        try:
            if callable(set_usage):
                set_usage(usage_mission_id)
            if callable(set_guard):
                set_guard(build_mission_budget_guard(mission_budget))

            from ...manager.skill_tidy import tidy_after_mission

            counts = tidy_after_mission(
                self._project_workdir(),
                self.runner,
                project_state_dir=getattr(self.memory, "project_root", None),
                on_event=self._emit,
            )
            if counts.get("to_builtin") or counts.get("to_vertical"):
                log.info("manager skill tidy-up after mission: %s", counts)
            return counts
        except Exception:  # noqa: BLE001 - evolution must never change verdict
            log.warning("manager skill tidy-up after mission failed", exc_info=True)
            return {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 1}
        finally:
            if callable(set_guard):
                try:
                    set_guard(None)
                except Exception:  # noqa: BLE001 - cleanup must not mask completion
                    pass
            if callable(set_usage):
                try:
                    set_usage(None)
                except Exception:  # noqa: BLE001 - cleanup must not mask completion
                    pass


__all__ = ["EvolutionMixin", "_per_mission_distill_enabled"]
