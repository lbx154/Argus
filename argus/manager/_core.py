"""Composition root for the user-facing Manager.

The Manager owns control-plane decisions around a mission: front-door routing,
vertical selection and persistence, and stage transitions. Mission execution
remains with LifeSupervisor, Planner, Engineer, and Reviewer.

The implementation is split by concern across three sibling mixins. This module
contains only the public result dataclasses and the ``Manager`` shell that wires
shared state, usage accounting, and pipeline locking.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    vertical: str            # research | software | … | an installed plugin vertical | a Manager-authored data domain
    stages: list[str]        # the vertical's Stage template (engine advances current_stage)
    domain: str = ""         # optional built-in overlay, currently for research
    workflow_mode: str = "staged"
    execution_task: str = ""
    require_independent_review: bool = True
    # Set when the Manager AUTHORED a new data domain for a task that fit no
    # preset vertical. ``pending_confirmation`` means the proposal has NOT been
    # written yet — the interactive caller must confirm and then call
    # :meth:`Manager.commit_domain`. Autonomous callers receive an already-
    # committed Division with ``pending_confirmation=False``.
    proposed_domain: Any = None
    pending_confirmation: bool = False
    learned_vertical_status: str = ""
    start_stage: str = ""
    workflow_profile: str = ""
    workflow_summary: str = ""

    def headline(self) -> str:
        if self.proposed_domain is not None and self.pending_confirmation:
            return (f"[manager] no preset vertical fit → PROPOSED new domain "
                    f"`{self.vertical}` ({len(self.stages)} stage(s): "
                    f"{' → '.join(self.stages)}) — awaiting confirmation")
        domain = f", domain={self.domain}" if self.domain else ""
        return (f"[manager] vertical={self.vertical}{domain}, "
                f"workflow={self.workflow_mode}, "
                f"{len(self.stages)} stage(s): {' → '.join(self.stages)}"
                + (f"\n{self.workflow_summary}" if self.workflow_summary else ""))


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
    # manager_llm | manager_deterministic | no_review_hold | no_runner_hold |
    # operator_abort_hold | failsafe_hold | illegal_target_hold
    source: str = "manager_llm"
    # Non-secret parser/runtime code for log triage (never raw model output).
    diagnostic: str = ""


# ---------------------------------------------------------------------------
# Manager — thin composition shell
# ---------------------------------------------------------------------------

class Manager(
    _VerticalDecisionMixin,
    _StageDecisionMixin,
):
    """User-facing Manager control plane.

    ``project_root`` is the session-scoped harness state root for pipeline,
    domain, and stage authority. ``execution_workdir`` is the user repository
    inspected by tools and modified by Engineer. They default to the same path
    for library compatibility, but Web/daemon composition keeps them separate.
    """

    def __init__(
        self,
        project_root: Path | str = ".",
        runner: Any = None,
        *,
        execution_workdir: Path | str | None = None,
        skill_store: Any = None,
        manager_session_root: Path | str | None = None,
        learned_vertical_root: Path | str | None = None,
        usage_context: Any = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.execution_workdir = Path(execution_workdir or project_root)
        self.runner = runner
        self._usage_context_factory = usage_context
        self.manager_session_root = (
            Path(manager_session_root)
            if manager_session_root is not None
            else self.project_root
        )
        self.learned_vertical_root = Path(
            learned_vertical_root or self.project_root
        )
        # One persistent, flock-serialized model session shared by stateful
        # Manager calls. Vertical routing deliberately uses the raw runner with
        # fresh context instead. ``None`` means model-owned calls are unavailable.
        self._session = (
            _ManagerSession(runner, self.manager_session_root)
            if runner is not None
            else None
        )
        # Optional agent-native Skill library. No store means no Skill context.
        self.skill_store = skill_store
        from ..skills.missions import ManagerMission, SelfMission

        self.mission = ManagerMission(skill_store)
        self.self_mission = SelfMission(skill_store)
        if self._session is not None:
            paths = (
                self.mission.libraries().native_paths
                + self.self_mission.libraries().native_paths
            )
            self._session.skill_paths = [str(path) for path in dict.fromkeys(paths)]

    def bind_execution_workdir(self, workdir: Path | str) -> "Manager":
        """Retarget repository-facing operations without replacing state."""
        self.execution_workdir = Path(workdir)
        return self

    def _task_usage_scope(self, root_task_id: str | None):
        if not root_task_id or self._usage_context_factory is None:
            return nullcontext()
        return self._usage_context_factory(root_task_id)

    def pipeline_lock(self, *, cancelled: Callable[[], bool] | None = None):
        return manager_pipeline_lock(self.manager_session_root, cancelled=cancelled)
