"""argus.manager._maintenance_ops — mixin for self-maintenance and skill injection.

``_MaintenanceMixin`` carries:

* ``decide_self_maintenance`` — Manager's evidence-bound daemon health decision.
* ``_role_skill_block`` — builds the Manager's fixed role skill + matched adaptive
  skill block, prepended to stage / SELF decision prompts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._helpers import (
    _manager_backend_failure,
    _manager_model,
    _manager_reasoning_effort,
    gateway_run_exec,
    log,
)


class _MaintenanceMixin:
    """Mixin: decide_self_maintenance and _role_skill_block."""

    @staticmethod
    def role_context() -> str:
        """Return the Manager's authoritative fixed operating contract."""
        from ..skills.role_context import format_role_context

        return format_role_context(
            "Argus manager role skill",
            "argus-manager-role.md",
        )

    def decide_self_maintenance(
        self,
        observations: list[dict[str, Any]],
        *,
        daemon_state: dict[str, Any],
        framework_root: Path | str,
        on_event: Any = None,
        usage_mission_id: str = "",
    ) -> Any:
        """Decide whether observed daemon evidence warrants one framework repair."""
        from ..core.models import RunnerOptions
        from ..roles.prompts.manager import build_maintenance_prompt
        from .self_maintenance import (
            MaintenanceDecision,
            parse_maintenance_decision,
        )
        from .stage_decider import extract_answer

        backend = self._session or self.runner
        if backend is None:
            return MaintenanceDecision(
                action="no_action",
                reason="Manager backend unavailable",
                error="no_runner",
            )
        prompt = build_maintenance_prompt(
            observations,
            daemon_state=daemon_state,
            framework_root=str(framework_root),
        )
        options = RunnerOptions(
            model=_manager_model(),
            reasoning_effort=_manager_reasoning_effort(),
            working_dir=str(Path(framework_root).resolve()),
            dangerous_yolo=True,
            skip_git_repo_check=True,
        )

        def run_exec(value: str) -> Any:
            return gateway_run_exec(
                backend,
                prompt=value,
                options=options,
                run_label="manager-self-maintenance",
            )

        if callable(on_event):
            from ..core.cost_events import metered_run_exec

            run_exec = metered_run_exec(
                run_exec,
                on_event,
                layer="manager",
                model=_manager_model(),
                run_label="manager-self-maintenance",
            )
        try:
            with self._task_usage_scope(usage_mission_id or None):
                result = run_exec(
                    self._role_skill_block(
                        "evidence-bound daemon self-maintenance",
                        include_libraries=False,
                    )
                    + prompt
                )
        except Exception as exc:  # noqa: BLE001 - maintenance must never stop research
            return MaintenanceDecision(
                action="no_action",
                reason=f"Manager maintenance audit failed: {type(exc).__name__}",
                error="runner_exception",
            )
        failed, detail = _manager_backend_failure(result)
        if failed:
            return MaintenanceDecision(
                action="no_action",
                reason="Manager maintenance backend failed"
                + (f": {detail}" if detail else ""),
                error="backend_failure",
            )
        decision = parse_maintenance_decision(
            extract_answer(result),
            valid_evidence_ids=[
                str(row.get("id") or "") for row in observations
            ],
        )
        if callable(on_event):
            on_event({
                "type": "manager.self_maintenance.decision",
                "action": decision.action,
                "reason": decision.reason,
                "problem": decision.problem,
                "evidence_ids": list(decision.evidence_ids),
                "affected_paths": list(decision.affected_paths),
                "error": decision.error,
                "agent_layer": "manager",
            })
        return decision

    # ---- role context, library discovery, and direct maintenance ----
    def _role_skill_block(
        self, objective: str, *, include_libraries: bool = True
    ) -> str:
        """Return Manager context, optional library paths, and edit rules."""
        if self.skill_store is None:
            return ""
        block = self.role_context()
        if include_libraries and (objective or "").strip():
            try:
                libraries = self.mission.libraries()
                if libraries.block:
                    block += libraries.block + "\n\n"
            except Exception:  # noqa: BLE001 — path discovery is fail-soft
                log.debug("manager Skill-library discovery failed", exc_info=True)
        from ..skills.role_memory import role_skill_maintenance_block

        return block + role_skill_maintenance_block(
            self.skill_store,
            "manager",
            enabled=self.memory_maintenance_enabled,
        )
