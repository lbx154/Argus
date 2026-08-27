"""Post-mission propagation for runtime-evolved Skills."""

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
    from ...core.paths import shared_skills_root

    return shared_skills_root()


class EvolutionMixin:
    def _evolve_runtime_skills_after_mission(
        self,
        *,
        success: bool,
        usage_mission_id: str,
        mission_objective: str = "",
        mission_result: str = "",
        reviewer_source: str = "",
        reviewer_reason: str = "",
        research_result: dict | None = None,
        evidence_refs: tuple[str, ...] = (),
        source_campaign: str = "",
    ) -> dict[str, int]:
        """Propagate reviewed project Skills into shared runtime layers."""
        set_usage = getattr(self.runner, "_set_usage_context", None)
        try:
            if callable(set_usage):
                set_usage(usage_mission_id)

            if (
                str(reviewer_source or "").strip().casefold() == "reviewer"
                and isinstance(research_result, dict)
            ):
                from ...core.paths import reviewed_facts_digest_path
                from ...manager.reviewed_facts import review_and_append_fact

                try:
                    review_and_append_fact(
                        self.runner,
                        digest_path=reviewed_facts_digest_path(
                            self._budget_global_root()
                        ),
                        source_campaign=(
                            source_campaign or str(self._project_workdir())
                        ),
                        reviewer_reason=reviewer_reason,
                        research_result=research_result,
                        evidence_refs=evidence_refs,
                    )
                except Exception:  # noqa: BLE001 - facts never own settlement
                    log.warning("Manager reviewed-facts hook failed", exc_info=True)

            if not bool(
                getattr(self.config, "role_skill_maintenance_enabled", True)
            ):
                return {"to_shared": 0, "to_vertical_shared": 0, "errors": 0}

            from ...manager.skill_tidy import propagate_after_mission

            counts: dict[str, int] = {}
            if _cross_project_propagation_enabled():
                counts.update(propagate_after_mission(
                    self._project_workdir(),
                    self.runner,
                    project_state_dir=_project_state_root(self.memory),
                    shared_root=_shared_skills_root(self.runner, self.memory),
                    mission_objective=mission_objective,
                    mission_success=success,
                    mission_result=mission_result,
                    on_event=self._emit,
                ))
            if success:
                from ...manager.domain_tidy import tidy_domains_after_mission

                tidy_domains_after_mission(
                    self._project_workdir(),
                    approve=None,
                    on_event=self._emit,
                )
            if any(counts.values()):
                log.info("manager skill propagation after mission: %s", counts)
            return counts
        except Exception:  # noqa: BLE001 - evolution must never change verdict
            log.warning("manager Skill propagation after mission failed", exc_info=True)
            return {"to_shared": 0, "to_vertical_shared": 0, "errors": 1}
        finally:
            if callable(set_usage):
                try:
                    set_usage(None)
                except Exception:  # noqa: BLE001 - cleanup must not mask completion
                    pass


__all__ = [
    "EvolutionMixin",
    "_cross_project_propagation_enabled",
]
