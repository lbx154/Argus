"""Lifetime-agent runtime infrastructure (backend-neutral).

This module owns the non-interactive machinery shared by the daemon, teammate
runner, and Manager front-door:

- ``build_life_runner``        — factory for memory / codex backends.
- ``run_life_supervisor``      — non-interactive driver (drain a backlog
                                  without a TTY).
- ``_invoke_supervisor``       — assemble a runtime context + run the
                                  supervisor for a single backend.
- ``LifeStderrSink``           — terminal event renderer for headless runs.
- ``_inbox_drainer_for``       — operator-inbox drain callable.
- the runner adapters (``_MemoryRunner`` / ``_ScriptedPlannerBackend`` /
  ``_SkillLoopRunner``) and the duck-typed ``_Outcome`` they return.

The infrastructure below is intentionally independent of the Ink/Web
presentation layer so daemon and teammate paths never import a terminal UI.

This module is a thin facade: the actual implementations live in sibling
``_runtime_*`` modules (helpers/protocols, runner construction, execute
lifecycle, stage transition, supervisor driver) so no single module here
exceeds the maintainability line-count target. Every name previously
importable from this module (public or private) remains importable from
here via explicit re-export.
"""
from __future__ import annotations

import logging

from ..core import paths as core_paths  # noqa: F401 — re-exported convenience
from ..life import BacklogItem  # noqa: F401 — re-exported convenience
from ._env import env_flag as _env_flag  # noqa: F401 — re-exported, see __all__
from ._env import env_int as _env_int  # noqa: F401 — re-exported, see __all__
from ._runtime_backends import (  # noqa: F401 — re-exported, see __all__
    _TEST_DAEMON_PLANNER_SCRIPT_ENV,
    _MemoryRunner,
    _Outcome,
    _ScriptedPlannerBackend,
)
from ._runtime_construction import (  # noqa: F401 — re-exported, see __all__
    _inbox_drainer_for,
    _pending_question_resolver_for,
    _resolve_role_runner_backend_name,
    _resolve_runner_backend_name,
    _RunnerConstructionMixin,
    build_life_runner,
)
from ._runtime_execute import SkillLoopExecuteMixin
from ._runtime_helpers import (  # noqa: F401 — re-exported, see __all__
    LifeStderrSink,
    _checkpoint_path_for,
    _CommonMemory,
    _ExecuteState,
    _memory_global_root,
    _memory_project_root,
    _project_state_dir_for,
    _resolve_global_root,
    _should_run_stage_transition,
    _SplitMemory,
)
from ._runtime_stage_transition import StageTransitionMixin
from ._runtime_supervisor import (  # noqa: F401 — re-exported, see __all__
    _build_supervisor_config,
    _independent_review_required_for_project_root,
    _invoke_supervisor,
    _paper_mission_for_project_root,
    _workflow_mode_for_project_root,
    run_life_supervisor,
)
from ._self_reply import SelfReplyMixin
from ._self_reply import (  # noqa: F401 — re-exported, see __all__
    self_retryable_transport_failure as _self_retryable_transport_failure,
)

log = logging.getLogger(__name__)


class _SkillLoopRunner(
    _RunnerConstructionMixin,
    SkillLoopExecuteMixin,
    StageTransitionMixin,
    SelfReplyMixin,
):
    """Backend-neutral mission runner used by the daemon, teammate, and
    Manager front-door.

    Composed from sibling-module mixins (construction/backend-wiring,
    execute lifecycle, stage-transition decision, self-reply chat
    fast-path) — see each mixin's module docstring for its slice of
    responsibility. This class itself carries no additional state or
    methods; every behaviour lives in one of the mixins above.
    """


__all__ = [
    "_env_flag",
    "_env_int",
    "_self_retryable_transport_failure",
    "_CommonMemory",
    "_SplitMemory",
    "_memory_project_root",
    "_memory_global_root",
    "_resolve_global_root",
    "_checkpoint_path_for",
    "LifeStderrSink",
    "_Outcome",
    "_MemoryRunner",
    "_ScriptedPlannerBackend",
    "_SkillLoopRunner",
    "log",
    "_TEST_DAEMON_PLANNER_SCRIPT_ENV",
    "_inbox_drainer_for",
    "build_life_runner",
    "_build_supervisor_config",
    "run_life_supervisor",
    "_invoke_supervisor",
]
