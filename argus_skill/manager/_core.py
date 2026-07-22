"""Composition root for the user-facing Manager.

The Manager owns control-plane decisions around a mission: front-door routing,
vertical selection and persistence, stage transitions, and bounded
self-maintenance. Mission execution remains with LifeSupervisor, Planner,
Engineer, and Reviewer.

The implementation is split by concern across four sibling mixins. This module
contains only the public result dataclasses and the ``Manager`` shell that wires
shared state, usage accounting, and pipeline locking.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._front_door_ops import _FrontDoorMixin
from ._maintenance_ops import _MaintenanceMixin
from ._session_ops import _ManagerSession, manager_pipeline_lock
from ._stage_ops import _StageDecisionMixin
from ._vertical_ops import _VerticalDecisionMixin

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class Division:
    """The Manager's verdict on how to divide a Task."""
    task: str
    vertical: str            # research | speedrun | … | a Manager-authored data domain
    kind: str                # research | optimize | software | custom
    stages: list[str]        # the vertical's Stage template (engine advances current_stage)
    workflow_mode: str = "staged"
    execution_task: str = ""
    # Set when the Manager AUTHORED a new data domain for a task that fit no
    # preset vertical. ``pending_confirmation`` means the proposal has NOT been
    # written yet — the interactive caller must confirm and then call
    # :meth:`Manager.commit_domain`. Autonomous callers receive an already-
    # committed Division with ``pending_confirmation=False``.
    proposed_domain: Any = None
    pending_confirmation: bool = False

    def headline(self) -> str:
        if self.proposed_domain is not None and self.pending_confirmation:
            return (f"[manager] no preset vertical fit → PROPOSED new domain "
                    f"`{self.vertical}` ({len(self.stages)} stage(s): "
                    f"{' → '.join(self.stages)}) — awaiting confirmation")
        label = "custom domain" if self.kind == "custom" else f"{self.kind} task"
        return (f"[manager] {label} → vertical={self.vertical}, "
                f"workflow={self.workflow_mode}, "
                f"{len(self.stages)} stage(s): {' → '.join(self.stages)}")


@dataclass
class StageTransition:
    """The Manager's verdict on whether/how to move the pipeline stage.

    ``action`` is ``advance`` | ``hold`` | ``rollback`` | ``complete``. A
    ``hold`` writes nothing; ``advance``/``rollback`` are applied to
    ``current_stage`` and ``complete`` marks the final stage done while leaving
    ``current_stage`` coherent. ``source`` records WHY this was the verdict —
    useful for journaling and to distinguish a model decision from a fail-safe
    HOLD.
    """

    action: str            # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    current_stage: str = ""
    # manager_llm | no_review_hold | no_runner_hold | failsafe_hold | illegal_target_hold
    source: str = "manager_llm"
    # Non-secret parser/runtime code for log triage (never raw model output).
    diagnostic: str = ""
    # True only when an authoritative Manager HOLD satisfies a persisted
    # Planner waiting condition and requests immediate replanning.
    resolves_wait: bool = False


# ---------------------------------------------------------------------------
# Manager — thin composition shell
# ---------------------------------------------------------------------------

class Manager(
    _MaintenanceMixin,
    _VerticalDecisionMixin,
    _StageDecisionMixin,
    _FrontDoorMixin,
):
    """User-facing Manager control plane.

    ``project_root`` is the mission's real project workdir, where pipeline,
    domain, and stage artifacts live. It must not be the daemon's internal
    ``life_dir``. ``manager_session_root`` is independent: it stores the
    Manager's persistent model session and lock files and may be life-dir
    scoped. ``runner`` is required for model-owned decisions such as vertical
    selection; those decisions fail loudly when no backend is available.
    """

    def __init__(
        self,
        project_root: Path | str = ".",
        runner: Any = None,
        *,
        skill_store: Any = None,
        manager_session_root: Path | str | None = None,
        usage_context: Any = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.runner = runner
        self._usage_context_factory = usage_context
        self.manager_session_root = (
            Path(manager_session_root)
            if manager_session_root is not None
            else self.project_root
        )
        # One persistent, flock-serialized model session shared by stateful
        # Manager calls. Vertical routing deliberately uses the raw runner with
        # fresh context instead. ``None`` means model-owned calls are unavailable.
        self._session = (
            _ManagerSession(runner, self.manager_session_root)
            if runner is not None
            else None
        )
        # Optional role/adaptive skill source for self-maintenance and
        # stage-decision prompts. No store means no injected skill block.
        self.skill_store = skill_store
        from ..skills.missions import ManagerMission

        self.mission = ManagerMission(skill_store)

    def _task_usage_scope(self, root_task_id: str | None):
        if not root_task_id or self._usage_context_factory is None:
            return nullcontext()
        return self._usage_context_factory(root_task_id)

    def pipeline_lock(self):
        return manager_pipeline_lock(self.manager_session_root)
