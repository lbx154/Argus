"""Composed mission runner and supported runtime entry points."""
from __future__ import annotations

from ._runtime_construction import (
    _inbox_drainer_for,
    _pending_question_resolver_for,
    _RunnerConstructionMixin,
    build_life_runner,
)
from ._runtime_execute import SkillLoopExecuteMixin
from ._runtime_helpers import LifeStderrSink
from ._runtime_stage_transition import StageTransitionMixin
from ._runtime_supervisor import (
    _final_certification_for_project_root,
    _paper_mission_for_project_root,
    run_life_supervisor,
)
from ._self_reply import SelfReplyMixin


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
    "_SkillLoopRunner",
    "build_life_runner",
    "run_life_supervisor",
    "LifeStderrSink",
    # Shared with the daemon configuration builder.
    "_inbox_drainer_for",
    "_pending_question_resolver_for",
    # Imported by the community vertical package.
    "_final_certification_for_project_root",
    "_paper_mission_for_project_root",
]
