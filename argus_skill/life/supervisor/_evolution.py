"""Post-mission source promotion for runtime-evolved skills."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _knob_enabled(name: str, default: bool) -> bool:
    from ...core.knobs import resolve_knob

    value = resolve_knob(name, "1" if default else "0").value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _cross_project_propagation_enabled() -> bool:
    """Whether successful missions share reviewed Skills with other projects."""
    return _knob_enabled(
        "ARGUS_SKILL_CROSS_PROJECT_PROPAGATION",
        True,
    )


def _per_mission_distill_enabled() -> bool:
    """Whether successful missions additionally write shared Skills to source."""
    return _knob_enabled("ARGUS_SKILL_PER_MISSION_DISTILL", False)


def _project_state_root(memory: object) -> Path | None:
    value = getattr(memory, "project_root", None)
    if value is None:
        value = getattr(memory, "root", None)
    return Path(value) if value is not None else None


def _shared_skills_root(runner: object, memory: object) -> Path:
    resolver = getattr(runner, "shared_skills_root", None)
    if callable(resolver):
        return Path(resolver())
    global_root = getattr(memory, "global_root", None)
    if global_root is not None:
        return Path(global_root) / "skills"
    from ...core.paths import skills_global_root

    return skills_global_root()


class EvolutionMixin:
    def _evolve_runtime_skills_after_mission(
        self,
        *,
        success: bool,
        usage_mission_id: str,
    ) -> dict[str, int]:
        """Run shared propagation and optional source promotion in the usage ledger."""
        if not success:
            return {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}

        set_usage = getattr(self.runner, "_set_usage_context", None)
        try:
            if callable(set_usage):
                set_usage(usage_mission_id)

            from ...manager.skill_tidy import (
                propagate_after_mission,
                tidy_after_mission,
            )

            counts: dict[str, int] = {}
            if _cross_project_propagation_enabled():
                counts.update(propagate_after_mission(
                    self._project_workdir(),
                    self.runner,
                    project_state_dir=_project_state_root(self.memory),
                    shared_root=_shared_skills_root(self.runner, self.memory),
                    on_event=self._emit,
                ))
            if _per_mission_distill_enabled():
                source_counts = tidy_after_mission(
                    self._project_workdir(),
                    self.runner,
                    project_state_dir=_project_state_root(self.memory),
                    on_event=self._emit,
                )
                counts.update({
                    f"source_{key}": value for key, value in source_counts.items()
                })
            if any(counts.values()):
                log.info("manager skill propagation after mission: %s", counts)
            return counts
        except Exception:  # noqa: BLE001 - evolution must never change verdict
            log.warning("manager skill tidy-up after mission failed", exc_info=True)
            return {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 1}
        finally:
            if callable(set_usage):
                try:
                    set_usage(None)
                except Exception:  # noqa: BLE001 - cleanup must not mask completion
                    pass


__all__ = [
    "EvolutionMixin",
    "_cross_project_propagation_enabled",
    "_per_mission_distill_enabled",
]
