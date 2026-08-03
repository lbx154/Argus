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
from pathlib import Path
from typing import Any

_RUNNER_HARD_IDLE_ENV = "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS"
_SHIFT_ROUND_LIMIT_ENV = "ARGUS_SKILL_SHIFT_ROUND_LIMIT"
_THREAD_TOKEN_LIMIT_ENV = "ARGUS_SKILL_THREAD_TOKEN_LIMIT"
_DECISION_PROGRESS_TIMEOUT_ENV = "ARGUS_SKILL_DECISION_PROGRESS_TIMEOUT_SECONDS"
# Toggle for the background-subagent advisory + agent-driven cadence wait. When
# unset/true, each round surfaces in-flight supervised subagents so the engineer
# does not babysit a self-watched run. Set to 0 to disable (e.g. tests).
_BG_SUBAGENT_ADVISORY_ENV = "ARGUS_SKILL_BG_SUBAGENT_ADVISORY"
_COMPACT_CONTINUATION_PROMPTS_ENV = "ARGUS_SKILL_COMPACT_CONTINUATION_PROMPTS"
_CONTINUE_WORK_SENTINEL = "CONTINUE_WORK:"
_CONTINUE_WORK_MAX_CHARS = 500
# Compatibility defaults for the retired resumed-thread policy. Autonomous
# Engineer/Reviewer calls are always fresh, so no token roll is needed.
_DEFAULT_THREAD_TOKEN_LIMIT = 0
_DEFAULT_DECISION_PROGRESS_TIMEOUT_SECONDS = 30 * 60
_RUNNER_DEFAULT_HARD_IDLE_SECONDS = 45 * 60


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    # Pipeline stages in which the engineer runs with codex's native live
    # web_search enabled (``codex exec --search``). Default: the research stage,
    # so idea discovery / literature grounding does REAL live search instead of
    # cached/recalled results. Empty set → never enable it.
    live_search_stages: frozenset[str] = frozenset({"research"})


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


def _fit_guard(threshold: int, reachable_max: int) -> int:
    """Clamp one absolute round guard into a budget it can still fire in.

    ``0`` means "explicitly disabled" for every guard that uses this, so it is
    returned untouched. A guard that already fits is returned unchanged, which
    keeps the default 500-round budget byte-for-byte identical. Anything larger
    is pulled down to ``reachable_max`` but never below 1, because a guard at 0
    would read as "disabled" rather than "fires immediately".
    """
    value = int(threshold)
    if value <= 0:
        return value
    if value <= reachable_max:
        return value
    return max(1, reachable_max)


@dataclass
class SupervisedConfig:
    """Knobs for the round-loop control."""

    max_rounds: int = 500
    no_progress_threshold: int = 2  # consecutive rounds with no engineer message before bailing
    # Consecutive reviewed rounds classified by the Reviewer as setup-only,
    # artifact-sync-only, or no decision progress. The harness counts the
    # structured verdict; it never infers scientific progress from activity.
    stall_threshold: int = 4
    # Round 1 receives the full task/skill contract. Continuation rounds use
    # Reviewer guidance plus the shared CHECKPOINT.md baton.
    compact_continuation_prompts: bool = field(
        default_factory=lambda: _env_bool(_COMPACT_CONTINUATION_PROMPTS_ENV, True)
    )
    # Safe round-boundary budget since the last Reviewer-classified decision or
    # evidence increment. This never interrupts a live provider call.
    decision_progress_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            _DECISION_PROGRESS_TIMEOUT_ENV,
            _DEFAULT_DECISION_PROGRESS_TIMEOUT_SECONDS,
        )
    )
    # Anti-livelock escalation — distinct from the stall guards above, which fire
    # when the engineer is idle or the Reviewer classifies repeated rounds as
    # nondecision work. A mission that makes evidence progress every round but
    # never passes its gate would otherwise drift to ``max_rounds``.
    # At ``soft_round_limit`` the reviewer is instructed to return ``blocked`` if
    # the binding constraint is an external/unresolvable dependency; at
    # ``hard_escalate_rounds`` the loop force-ends as ``blocked`` so the planner
    # re-plans/decomposes and the operator inbox is re-read on the next mission.
    # The continuous planner makes many SHORT missions, so a single mission this
    # long without finishing is anomalous. 0 disables either guard.
    soft_round_limit: int = 12
    hard_escalate_rounds: int = 24
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    session_id: str | None = None
    # Absolute path to THIS mission's engineer execution log (the per-project
    # ``<life_dir>/events.jsonl``). The reviewer runs in the project work-tree
    # and only sees the engineer's final summary, so it cannot otherwise tell
    # HOW a result was produced (hardcoded answer? skipped step? cheat method?
    # faked metric?). When set, the reviewer prompt gains an execution-log
    # audit section pointing here with grep recipes; empty string (memory
    # backend / tests / unresolvable path) = legacy behaviour, no audit section,
    # byte-for-byte unchanged. The engineer's shell commands land in the
    # ``text`` field of each ``engineer.progress`` event in this file.
    engineer_log_path: str = ""
    # Retained as a compatibility knob for callers that still construct the
    # config explicitly. Autonomous Engineer/Reviewer calls now always start a
    # fresh provider session, so the value is no longer consulted by the loop.
    shift_round_limit: int = field(default_factory=lambda: _env_int(_SHIFT_ROUND_LIMIT_ENV, 1))
    # Compatibility-only alongside ``shift_round_limit``; fresh-per-round calls
    # do not carry a thread whose token count needs policing.
    thread_token_limit: int = field(
        default_factory=lambda: _env_int(_THREAD_TOKEN_LIMIT_ENV, _DEFAULT_THREAD_TOKEN_LIMIT)
    )
    # Ordinary Markdown file edited directly by Engineer and Reviewer. None
    # disables the shared checkpoint for callers that intentionally opt out.
    checkpoint_path: Path | None = None
    # Mission-level canonical packet. Round handoffs are written beside it.
    context_packet_path: str = ""
    # Constructor compatibility for older callers. Semantic progress is never
    # inferred from provider-private session files or project mtimes.
    effective_progress_warning_seconds: int = 0
    effective_progress_stalled_seconds: int = 0
    effective_progress_timeout_seconds: int = 0
    effective_progress_check_interval_seconds: float = 0.0
    runner_hard_idle_seconds: int = field(
        default_factory=lambda: _env_int(
            _RUNNER_HARD_IDLE_ENV,
            _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        )
    )
    # Constructor compatibility; provider-private compaction logs are not read.
    round_compaction_limit: int = 0
    # Surface in-flight SUPERVISED subagents (read from
    # ``<workdir>/.argus_subagents``) in the engineer prompt each round so the
    # agent does not burn rounds babysitting a self-watched long job, and can
    # yield through the exact final-line JSON wait request instead of
    # busy-polling. Env
    # override: ARGUS_SKILL_BG_SUBAGENT_ADVISORY (0 disables).
    background_subagent_advisory: bool = field(
        default_factory=lambda: _env_bool(_BG_SUBAGENT_ADVISORY_ENV, True)
    )
    # Retained only for source compatibility with older callers.
    review_deferral_limit: int = 0

    def __post_init__(self) -> None:
        """Keep the round-budget guards reachable when ``max_rounds`` shrinks.

        ``stall_threshold`` / ``soft_round_limit`` / ``hard_escalate_rounds``
        are ABSOLUTE round counts sized for the default ``max_rounds`` (500).
        A caller may lower the budget far below them for one mission —
        ``bounded_dag_node_max_rounds()`` yields 3 — and a guard whose
        threshold is not strictly reachable within the budget can then never
        fire. Nothing reports this: the value stays in the config, is passed
        to the classifier, and evaluates to ``False`` on every round, so the
        loop silently degrades to the single coarsest guard
        (``no_progress_threshold``, which only counts EMPTY rounds and cannot
        tell a converging mission from a spinning one).

        ``_runtime_execute`` already performs the mirror-image coordination in
        the unbounded direction (a progressive experiment matrix raises
        ``max_rounds`` and explicitly zeroes both escalation guards). This does
        the same for the bounded direction, generically.

        Rescaling preserves each guard's MEANING ("stop after this much
        fruitless work") expressed in the budget actually available:

        * ``stall_threshold`` needs ``streak >= threshold`` while
          ``round_index < max_rounds``; a streak cannot exceed the round
          index, so it must be ``<= max_rounds - 1``.
        * ``soft_round_limit`` advises the Reviewer partway through, so it
          must also land strictly inside the budget.
        * ``hard_escalate_rounds`` force-ends the loop with a planner-readable
          reason; firing on the final round is still better than the generic
          ``Hit max_rounds`` path, so ``<= max_rounds`` is enough.

        A guard explicitly disabled with ``0`` stays disabled, and a budget
        large enough for the configured values is left byte-for-byte
        unchanged.
        """
        budget = int(self.max_rounds)
        if budget <= 0:
            return
        self.stall_threshold = _fit_guard(self.stall_threshold, budget - 1)
        self.soft_round_limit = _fit_guard(self.soft_round_limit, budget - 1)
        self.hard_escalate_rounds = _fit_guard(self.hard_escalate_rounds, budget)
