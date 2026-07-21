"""argus.manager — the user-facing Manager that DIVIDES a Task.

When the user hands over a Task, the Manager first decides whether it is a
"regular" task — one that maps to a preset vertical pipeline (a research paper,
or a lean optimize/speedrun loop) — then splits it into that vertical's Stages
and commits the choice. The existing engine (LifeSupervisor → Planner → SkillLoop
→ Engineer ↔ Reviewer) then advances stage-by-stage on its own.

This is a thin ORCHESTRATION layer — it reuses the real machinery, adding only
the user-facing *division* step:

  * decide     → ``Manager.decide_vertical`` — an explicit built-in env choice is
                 reused directly; otherwise the Manager's tool-free fast pass picks a clear
                 existing vertical in one model request. Only uncertainty/new-domain
                 cases escalate to one bounded grounded call (no keyword classifier;
                 see ``manager/domain_author.py``)
  * stage list → ``verticals/<v>/stages.py`` ``STAGE_ORDER`` via ``load_vertical``
  * commit     → ``skills.vertical_select.persist_vertical`` — the supervisor then
                 TRUSTS the persisted vertical and does NOT re-classify.

The Manager never judges the win and never plans loops itself — it only divides
the task and hands the current Stage to the existing Planner.

Implementation note
-------------------
The ``Manager`` class is composed from four mixin classes defined in sibling
modules (one concern per module).  This file keeps only:

* the ``Division`` / ``StageTransition`` dataclasses (public API);
* re-exports of all module-level names that external code currently imports
  from ``argus_skill.manager._core`` (backward compatibility);
* the thin ``Manager`` class that wires ``__init__``, ``pipeline_lock``, and
  ``_task_usage_scope`` while inheriting decision logic from the mixins.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._front_door_ops import _FrontDoorMixin

# ---------------------------------------------------------------------------
# Re-exports — all names that external code already imports from this module.
# (Webapi server/tests, daemon, and the public __init__.py all depend on
#  these being addressable as ``argus_skill.manager._core.<name>``.)
# ---------------------------------------------------------------------------
from ._helpers import (  # noqa: F401 — re-exported for backward compat
    _OPTIMIZE_VERTICALS,
    _manager_backend_failure,
    _manager_fast_route_enabled,
    _manager_fast_route_min_confidence,
    _manager_model,
    _manager_reasoning_effort,
    _manager_route_positive_int,
    _manager_safe_mode,
    _manager_vertical_reasoning_effort,
    _read_json_object,
    gateway_run_exec,
    log,
)

# Mixin classes (one concern per module — no circular imports).
from ._maintenance_ops import _MaintenanceMixin
from ._session_ops import (  # noqa: F401 — re-exported
    _PIPELINE_LOCK,
    _PIPELINE_YIELD_FILE,
    _SESSION_FILE,
    _SESSION_LOCK,
    _acquire_session_lock,
    _clear_pipeline_yield_if_token,
    _ManagerSession,
    _pipeline_lock_timeout_s,
    _restore_files_on_error,
    _session_lock_timeout_s,
    clear_manager_pipeline_yield,
    manager_pipeline_lock,
    manager_pipeline_yield_requested,
    manager_session_lock,
    request_manager_pipeline_yield,
    reset_manager_session,
)
from ._stage_ops import (
    _manager_blocked_rollback_artifact,  # noqa: F401 — re-exported
    _StageDecisionMixin,
)
from ._vertical_ops import _VerticalDecisionMixin

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class Division:
    """The Manager's verdict on how to divide a Task."""
    task: str
    vertical: str            # research | speedrun | … | a Manager-authored data domain
    kind: str                # "research" | "optimize" | "custom"
    regular: bool            # True = maps to a preset pipeline; False = free-form
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
        tag = "regular" if self.regular else "free-form"
        if self.kind == "custom":
            tag = "new domain"
        return (f"[manager] {self.kind} task ({tag}) → vertical={self.vertical}, "
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
    """User-facing entry: divide a Task, then hand it to the existing engine.

    ``project_root`` is the mission's real project WORKDIR — where
    ``research/PIPELINE_STATE.json``, ``research/DOMAINS/*.json``, and every
    other stage/vertical artifact live, matching what
    ``skills.stage_checklists`` / ``skills.vertical_select`` / the reviewer's
    stage-gated checklist all read and write. It must NEVER be the daemon's
    internal life_dir (a distinct, life-of-the-daemon scoped directory) — the
    two are easy to conflate but reads/writes against life_dir are invisible
    to everything else that tracks pipeline stage. ``manager_session_root``
    is the separate, orthogonal concern: where the Manager's OWN persistent
    codex session/lock files live (safe to keep daemon/life_dir-scoped).
    ``runner`` is an optional LLM backend for classification; without it the
    classifier degrades to the deterministic keyword heuristic.
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
        # One persistent, flock-serialized codex session shared by every Manager
        # LLM call within THIS Argus session. ``None`` when there is no runner —
        # the classifier then falls back to the keyword heuristic as before.
        self._session = (
            _ManagerSession(runner, self.manager_session_root)
            if runner is not None
            else None
        )
        # Optional role-mission skill matcher (the same scaffold engineer,
        # reviewer, and planner use). ``None`` skill_store ⇒ an empty match and
        # NO injected skill block, so the Manager's existing classify / stage /
        # approve behaviour is byte-for-byte unchanged for every current caller
        # that does not pass a store (full backward compatibility). When a store
        # IS wired, the Manager injects its fixed role skill plus any matched
        # adaptive manager skill into its stage-decision prompt.
        self.skill_store = skill_store
        from ..skills.missions import ManagerMission

        self.mission = ManagerMission(skill_store)

    def _task_usage_scope(self, root_task_id: str | None):
        if not root_task_id or self._usage_context_factory is None:
            return nullcontext()
        return self._usage_context_factory(root_task_id)

    def pipeline_lock(self):
        return manager_pipeline_lock(self.manager_session_root)
