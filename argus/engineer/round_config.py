"""Round-loop phase: engineer/supervised config dataclasses + env helpers.

``EngineerConfig`` and ``SupervisedConfig`` are the two knob-holding
dataclasses ``SupervisedEngineer`` is constructed with; the small
``_env_*`` helpers and ``parse_continue_work_request`` are standalone,
config-adjacent parsing utilities with no round-loop control-flow of
their own. Moved out of ``runner.py`` verbatim (mechanical extraction,
no behavior change) to keep that module under the maintainability
line-count target.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from functools import wraps
from pathlib import Path
from typing import Any

from ..core.knobs import env_int

_RUNNER_STALLED_IDLE_ENV = "ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS"
_NARRATIVE_REVIEW_ENFORCEMENT_ENV = "ARGUS_SKILL_NARRATIVE_REVIEW_ENFORCEMENT"
_CONTINUE_WORK_SENTINEL = "CONTINUE_WORK:"
_CONTINUE_WORK_MAX_CHARS = 500
# A turn is never stopped by a timer by default: a live process is not proof
# of work, but silence is not proof of a hang either. A turn that makes no
# progress (no model stream event, no CPU time in its commands) for the stalled
# limit is reported to the Manager once, and stopped only if the Manager
# decides so.
_RUNNER_DEFAULT_STALLED_IDLE_SECONDS = 30 * 60
# Framework-owned fallback for ``EngineerConfig.live_search_stages``: the
# research stage, where idea discovery / literature grounding happens. A
# vertical that owns a different pipeline (math runs scope/solve/review and has
# no research stage at all) declares its own set through the vertical contract;
# this stays the answer for every vertical that declares nothing.
DEFAULT_LIVE_SEARCH_STAGES: frozenset[str] = frozenset({"research"})
# Records the host writes for a round nobody judged: the Engineer turn ended
# before review, or the Reviewer leg rendered no verdict. They sit in the round
# list beside real verdicts but carry no review judgment of any kind.
HOST_PLACEHOLDER_REVIEW_SOURCES = frozenset(
    {
        "backend_failure",
        "provider_background_wait",
        "provider_turn_cap",
        "reviewer_unavailable",
        "silent_command",
        "unchanged_return",
    }
)


def _narrative_review_enforcement() -> str:
    raw = os.environ.get(_NARRATIVE_REVIEW_ENFORCEMENT_ENV, "shadow")
    return "blocking" if raw.strip().lower() == "blocking" else "shadow"


def parse_continue_work_request(message: str | None) -> str | None:
    """Parse an engineer-requested, bounded continuation before review.

    The request must be the final non-empty line of a substantive response.
    This keeps a quoted example or casual mention from changing control flow,
    while letting the engineer preserve its normal evidence and summary.
    """
    if not message:
        return None
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    line = lines[-1]
    if line.startswith("`") and line.endswith("`") and len(line) >= 2:
        line = line[1:-1].strip()
    if not line.upper().startswith(_CONTINUE_WORK_SENTINEL):
        return None
    next_step = line[len(_CONTINUE_WORK_SENTINEL) :].strip()
    if not next_step or len(next_step) > _CONTINUE_WORK_MAX_CHARS:
        return None
    return next_step


@dataclass
class EngineerConfig:
    model: str
    reasoning_effort: str | None = None
    initial_reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    skill_paths: list[str] | None = None
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    # Public, historical caller configuration for pipeline stages in which the
    # engineer runs with codex's native live web_search enabled
    # (``codex exec --search``). The value and type of this default are part of
    # the API; private provenance below, rather than ``None``, records omission.
    live_search_stages: frozenset[str] = DEFAULT_LIVE_SEARCH_STAGES
    # State root that owns the pipeline stage used by the live-search gate.
    # Execution may happen in a separate workdir.
    vertical_state_root: Path | None = None
    # Directories outside the workdir the Engineer may read: Skill and recall
    # libraries, the sources they cite, its handoffs. A sandbox reads no other.
    add_dirs: list[str] | None = None
    # ``dataclasses.replace`` reconstructs an instance by passing every init
    # field back to ``__init__``. Keeping provenance as a keyword-only init
    # field lets that replay carry the original state instead of mistaking the
    # replayed public value for a caller choice. Keyword-only keeps every
    # historical positional parameter in the same place.
    _live_search_stages_explicit: bool = field(
        default=False,
        repr=False,
        compare=False,
        kw_only=True,
    )

    def __setattr__(self, name: str, value: Any) -> None:
        """Treat post-construction writes to the public knob as explicit."""
        if (
            name == "live_search_stages"
            and "_live_search_stages_explicit" in self.__dict__
        ):
            object.__setattr__(self, "_live_search_stages_explicit", True)
        object.__setattr__(self, name, value)

    def replace(self, **changes: Any) -> EngineerConfig:
        """Return an updated config while preserving caller provenance.

        Use this public entry point when updating ``live_search_stages``.
        ``dataclasses.replace`` cannot distinguish an explicitly supplied
        default-equivalent value from a field it copied unchanged, so use this
        method when that distinction matters.
        """
        if "live_search_stages" in changes:
            changes["_live_search_stages_explicit"] = True
        return dataclass_replace(self, **changes)


_engineer_config_init = EngineerConfig.__init__


@wraps(_engineer_config_init)
def _init_engineer_config_with_provenance(
    self: EngineerConfig,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Capture argument presence without losing replayed provenance."""
    provenance_supplied = "_live_search_stages_explicit" in kwargs
    provenance = kwargs.get("_live_search_stages_explicit", False)
    stages_supplied = len(args) > 10 or "live_search_stages" in kwargs
    explicit = stages_supplied
    # ``dataclasses.replace`` supplies the old provenance alongside every init
    # field. Preserve that state, but recognize a replayed non-default public
    # value as an explicit replacement. Only a replacement equal to the public
    # default is inherently ambiguous; ``EngineerConfig.replace`` records that
    # intent before ``dataclasses.replace`` discards the changed-key information.
    if provenance_supplied:
        explicit = bool(provenance)
        if not explicit and stages_supplied:
            stages = (
                args[10]
                if len(args) > 10
                else kwargs["live_search_stages"]
            )
            explicit = stages != DEFAULT_LIVE_SEARCH_STAGES
    _engineer_config_init(self, *args, **kwargs)
    self._live_search_stages_explicit = explicit


EngineerConfig.__init__ = _init_engineer_config_with_provenance  # type: ignore[method-assign]


def _engineer_live_search(workdir: Any, stages: "frozenset[str]") -> bool:
    """Whether to enable codex ``--search`` for this engineer round.

    True when the project's current pipeline stage is in ``stages`` (default:
    the research stage, where idea discovery happens). ``current_stage`` resolves
    the framework default (``research``) when no ``PIPELINE_STATE`` exists yet, so
    a fresh/bootstrapping project also gets live search during ideation. Any hard
    error resolving the stage fails closed to False — never break the round.
    """
    if not stages:
        return False
    try:
        from ..skills.stage_machine import current_stage

        return (current_stage(workdir) or "").strip().lower() in stages
    except Exception:  # noqa: BLE001 — stage lookup must never break the round
        return False


@dataclass
class SupervisedConfig:
    """Knobs for the round-loop control."""

    # Zero means no arbitrary round ceiling; explicit positive budgets still work.
    max_rounds: int = 0
    # Separate from the provider's per-call allowance: zero permits as many
    # checkpointed context rotations as this mission needs. Actual budget,
    # provider, and operator stops still apply on every call.
    provider_turn_cap_streak_limit: int = 0
    # Keep the historical reviewed loop by default. Planner-classified
    # low-risk bounded work may opt into an Engineer self-review completion.
    require_independent_review: bool = True
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    session_id: str | None = None
    role_session_dir: Path | None = None
    # Absolute path to THIS mission's engineer execution log (the per-project
    # ``<life_dir>/events.jsonl``). The reviewer runs in the project work-tree
    # and only sees the engineer's final summary, so it cannot otherwise tell
    # HOW a result was produced (hardcoded answer? skipped step? cheat method?
    # faked metric?). When set, the reviewer prompt gains a one-line fallback
    # pointer; empty string (memory backend / tests / unresolvable path) omits
    # it. The engineer's shell commands still land in the ``text`` field of
    # each ``engineer.progress`` event in this file.
    engineer_log_path: str = ""
    # Prompt routing operation selected once from the active vertical/stage.
    # Narrative editing forces a fresh Engineer context so review wording and
    # authoring chronology cannot leak into the reader-facing pass.
    engineer_operation: str = "mission"
    narrative_mission_id: str = ""
    narrative_snapshot_root: str = ""
    # New semantic-loss/cold-read signals calibrate in shadow mode first.
    # Set ARGUS_SKILL_NARRATIVE_REVIEW_ENFORCEMENT=blocking only after evals.
    narrative_review_enforcement: str = field(
        default_factory=_narrative_review_enforcement
    )
    # Ordinary Markdown file edited directly by Engineer and Reviewer. None
    # disables the shared checkpoint for callers that intentionally opt out.
    checkpoint_path: Path | None = None
    # Mission-level canonical packet. Round handoffs are written beside it.
    context_packet_path: str = ""
    runner_stalled_idle_seconds: int = field(
        default_factory=lambda: env_int(
            _RUNNER_STALLED_IDLE_ENV,
            _RUNNER_DEFAULT_STALLED_IDLE_SECONDS,
        )
    )
    operator_questions_allowed: bool = True
    operator_question_policy_root: Path | None = None
