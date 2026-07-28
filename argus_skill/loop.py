"""SkillLoop — the integrated matcher → supervised-engineer flow.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore`` (vendored from skill-agent): horizontal skill cache.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that accepts decisive Engineer self-verification for
    bounded work or otherwise supervises until the Reviewer is satisfied.

Skill and wiki memory normally use independent review. For a bounded mission,
the Engineer may explicitly self-verify and waive Reviewer; if it also identifies
durable skill learning, the same Engineer thread is resumed once to author the
create/update candidate, which still passes through SkillRouter safeguards.

End-to-end shape:

    task → matcher/Scientist → engineer round-loop (engineer → reviewer)
            outcome → record skill use and preserve validated memory edits
            continue → inject next_action, next round
            blocked → stop with reason; direct memory edits remain persisted
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.event_catalog import EventType
from .core.models import LoopOutcome, RoundRecord
from .core.ports import RunnerBackend
from .engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from .reviewer import Reviewer, ReviewerConfig
from .skills.loop_prompt import PromptContextMixin
from .skills.loop_review_hooks import ReviewedRoundHooksMixin
from .skills.loop_settlement import MissionSettlementMixin
from .skills.loop_skill_selection import (
    SkillSelectionMixin,
    _nearest_transfer_scores,  # noqa: F401 -- re-exported for tests
)
from .skills.loop_state import MissionContext
from .skills.missions import EngineerMission
from .skills.skill_router import SkillRouter
from .skills.store import SkillStore

log = logging.getLogger(__name__)

def _env_float_setting(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int_setting(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _knob_bool_setting(name: str, default: bool) -> bool:
    from .core.knobs import resolve_knob

    value = resolve_knob(name, "1" if default else "0").value
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SkillLoopConfig:
    """All knobs for one SkillLoop.run invocation, in one place."""
    engineer_model: str | None = "gpt-5.5"
    reviewer_model: str | None = None  # default: same as engineer (cheap)
    matcher_model: str | None = None   # default: same as engineer
    # Direct/bounded work starts at high. A Reviewer-requested second round
    # escalates to ``engineer_reasoning_effort`` (xhigh by default). Staged and
    # paper missions retain xhigh from round one.
    engineer_initial_reasoning_effort: str | None = "high"
    engineer_reasoning_effort: str | None = "xhigh"
    reviewer_reasoning_effort: str = "high"
    matcher_reasoning_effort: str | None = "low"
    # Cheap task-conditioning pass over the closest matched skill. This is a
    # single no-tool input/output request, not a Scientist or execution agent.
    skill_adapter_model: str | None = None
    skill_adapter_reasoning_effort: str = "low"
    skill_adapter_enabled: bool = True
    skill_adapter_max_bullets: int = 8
    nearest_transfer_min_score: float = field(
        default_factory=lambda: _env_float_setting(
            "ARGUS_SKILL_NEAREST_TRANSFER_MIN_SCORE", 0.12
        )
    )
    nearest_transfer_max_bullets: int = 4
    nearest_transfer_enabled: bool = field(
        default_factory=lambda: _knob_bool_setting(
            "ARGUS_SKILL_NEAREST_TRANSFER_ENABLED",
            False,
        )
    )
    # Evaluation/continuous-learning mode: completed tasks are asked to retain
    # only durable reusable learning. ``force_post_task_learning`` restores the
    # legacy every-task create/update contract for controlled ablations.
    require_post_task_learning: bool = field(
        default_factory=lambda: _knob_bool_setting(
            "ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING",
            True,
        )
    )
    max_rounds: int = 500
    no_progress_threshold: int = 2
    # Anti-livelock escalation thresholds threaded into SupervisedConfig: at
    # ``soft_round_limit`` the reviewer is told to escalate an unresolvable
    # external blocker to ``blocked``; at ``hard_escalate_rounds`` the round loop
    # force-ends as ``blocked`` so the planner re-plans. 0 disables either.
    soft_round_limit: int = 12
    hard_escalate_rounds: int = 24
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    # Repeated reviewer rejection is evidence that a matched playbook is not
    # enough. Ask the Scientist for a genuinely different strategy every N
    # non-terminal rounds; 0 disables.
    adaptive_skill_interval: int = 4
    # Restart-safe Scientist adaptation after Reviewer-classified method/skill
    # failures. Bounded calls prevent rejection loops from becoming unbounded
    # strategy generation.
    adaptive_rejection_threshold: int = 2
    adaptive_skill_max_triggers: int = 2
    # Legacy proposal compatibility only. Current Reviewers edit the injected
    # project skill path directly and their output schema has no skill_ops.
    skill_ops_enabled: bool = False
    # Shared declarative knowledge wiki. Roles edit pages directly.
    wiki_enabled: bool = False
    # Bootstrap one project wiki before the first mission.
    # Library callers remain opt-in; the daemon runtime enables this by default.
    auto_init_wiki: bool = False
    # Automatic library housekeeping (explicit opt-in). Finds near-duplicate
    # skills/wiki-pages accumulated across tasks or concurrent writers and
    # merges each cluster down to one representative. LLM grouping sees compact
    # summaries only; a no-op-safe,
    # REVERSIBLE archive/retire move, never a hard delete; a protected/
    # governing skill is never a merge candidate (self-governance floor).
    # Off by default; the daemon enables it.
    auto_compact_enabled: bool = False
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    extra_args: list[str] | None = None
    session_id: str | None = None
    # ``require_post_task_learning`` asks for selective durable learning. This
    # stronger compatibility flag restores the legacy every-task create/update
    # requirement for controlled evaluations only.
    force_post_task_learning: bool = field(
        default_factory=lambda: (
            os.environ.get("ARGUS_SKILL_FORCE_POST_TASK_LEARNING", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
    )
    engineer_file_read_budget: int = field(
        default_factory=lambda: _env_int_setting(
            "ARGUS_SKILL_ENGINEER_FILE_READ_BUDGET", 12
        )
    )
    engineer_test_run_budget: int = field(
        default_factory=lambda: _env_int_setting(
            "ARGUS_SKILL_ENGINEER_TEST_RUN_BUDGET", 3
        )
    )
    # Manager-selected execution topology. Every mode still uses skill/wiki.
    workflow_mode: str = "staged"
    # Explicit signal that this mission is a long-horizon academic-paper /
    # submission task. When True the engineer prompt carries the
    # long-horizon paper execution contract. Replaces the old keyword-based
    # objective sniffing; callers (e.g. the life runner) set it explicitly.
    paper_mission: bool = False
    # Ordinary Markdown file edited directly by Engineer and Reviewer as the
    # shared baton between fresh per-round sessions. None disables it.
    checkpoint_path: Path | None = None
    # Canonical machine-readable mission packet created by the supervisor.
    # Every fresh role session reads/writes versioned round handoffs beside it.
    context_packet_path: str = ""
    # Absolute path to this project's engineer execution log
    # (``<life_dir>/events.jsonl``), threaded down to SupervisedConfig so the
    # reviewer can grep HOW the engineer produced its result (process-correctness
    # audit). Empty = legacy behaviour (no audit section in the reviewer prompt);
    # the life runner fills it from the per-project state dir.
    engineer_log_path: str = ""
    # Campaign lifetime metadata threaded from the daemon's LifeWorkerConfig via
    # the argparse namespace so _SkillLoopRunner.execute can forward them to
    # _decide_stage_transition.  open_ended=True tells the Manager stage hook to
    # skip final_stage_completion_decision (which would otherwise overwrite the
    # Manager's own structured rollback verdict with a bounded completion).
    open_ended: bool = False
    continuous_objective: str = ""

    def resolved_reviewer_model(self) -> str:
        return self.reviewer_model or self.engineer_model

    def resolved_matcher_model(self) -> str:
        """Resolve the skill matcher model with env override.

        Precedence (highest first):
          1. ``ARGUS_SKILL_MATCHER_MODEL`` env var — operator override.
             Set to a cheap router (e.g. ``gpt-4o-mini``, ``haiku-3.5``)
             to slash selection cost: at our N=50 a single matcher call
             is ~180k input tokens, ~80% cheaper on gpt-4o-mini than on
             gpt-5.4 with negligible accuracy loss.
          2. ``matcher_model`` field (constructor / config).
          3. ``engineer_model`` fallback — backwards-compatible default.
        """
        import os
        env = os.environ.get("ARGUS_SKILL_MATCHER_MODEL", "").strip()
        if env:
            return env
        return self.matcher_model or self.engineer_model

    def resolved_skill_adapter_model(self) -> str:
        env = os.environ.get("ARGUS_SKILL_ADAPTER_MODEL", "").strip()
        return env or self.skill_adapter_model or self.engineer_model or ""

    def resolved_skill_adapter_reasoning_effort(self) -> str:
        env = os.environ.get(
            "ARGUS_SKILL_ADAPTER_REASONING_EFFORT", ""
        ).strip()
        return env or self.skill_adapter_reasoning_effort or "low"

    def resolved_initial_engineer_effort(self) -> str | None:
        if self.workflow_mode != "direct" or self.paper_mission:
            return self.engineer_reasoning_effort
        env = os.environ.get(
            "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", ""
        ).strip()
        return env or self.engineer_initial_reasoning_effort


class SkillLoop(
    SkillSelectionMixin,
    PromptContextMixin,
    ReviewedRoundHooksMixin,
    MissionSettlementMixin,
):
    """High-level entry point: ``loop.run("task description")``.

    Two injectable backends — typically the same in production (one codex
    CLI), but separable so tests can mock individually:

      * ``engineer_runner``  — for execution and skill distillation.
      * ``reviewer_runner``  — for the per-round verdict.

    There is no separate "author" backend: skill distillation reuses the
    engineer backend (and the unified ``gpt-5.5`` route). Pass the same
    backend twice if you only have one.
    """

    def __init__(
        self,
        *,
        skills_dir: Path,
        engineer_runner: RunnerBackend,
        reviewer_runner: RunnerBackend | None = None,
        config: SkillLoopConfig | None = None,
        skill_store: Any | None = None,
        on_event: Callable[[dict], None] | None = None,
        extra_guidance_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.config = config or SkillLoopConfig()
        self.skills_dir = Path(skills_dir)
        self.engineer_runner = engineer_runner
        self.reviewer_runner = reviewer_runner or engineer_runner
        self.on_event = on_event
        self.pre_settlement_guard: Callable[..., tuple[str, str, str]] | None = None
        self.canonical_playground_engineer_skill: Any | None = None
        self.canonical_playground_reviewer_skill: Any | None = None
        # Optional callable consulted at the start of each engineer round.
        # Returns a list of additional guidance strings to append to the
        # prompt (used by the daemon to honour /inject between rounds).
        self.extra_guidance_provider = extra_guidance_provider

        self.skill_store = skill_store or SkillStore(
            self.skills_dir,
            runner=engineer_runner,
            matcher_model=self.config.resolved_matcher_model(),
            matcher_reasoning_effort=self.config.matcher_reasoning_effort,
        )
        self.engineer_mission = EngineerMission(
            self.skill_store, on_event=self.on_event
        )
        self.reviewer = Reviewer(
            self.reviewer_runner,
            skill_store=self.skill_store,
            memory_maintenance_enabled=self.config.require_post_task_learning,
        )
        # The single front door to the skill library: selection (delegated to
        # the role matcher) plus structurally-safe CRUD. New versions are active
        # immediately; the Reviewer uses real trajectories to update/archive,
        # while protected skills retain a mechanical self-governance floor.
        self.skill_router = SkillRouter(
            skill_store=self.skill_store,
            matcher=self.engineer_mission,
        )
        self.supervised = SupervisedEngineer(
            engineer_runner=engineer_runner,
            reviewer=self.reviewer,
            engineer_config=EngineerConfig(
                model=self.config.engineer_model,
                reasoning_effort=self.config.engineer_reasoning_effort,
                initial_reasoning_effort=(
                    self.config.resolved_initial_engineer_effort()
                ),
                extra_args=self.config.extra_args,
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
                sandbox_mode=self.config.sandbox_mode,
                isolate_workdir=self.config.isolate_workdir,
            ),
            reviewer_config=ReviewerConfig(
                model=self.config.resolved_reviewer_model(),
                reasoning_effort=self.config.reviewer_reasoning_effort,
                extra_args=self.config.extra_args or [],
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
                sandbox_mode=self.config.sandbox_mode,
                isolate_workdir=self.config.isolate_workdir,
            ),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str, *, workdir: Path | None = None, seed_thread_id: str | None = None,
            objective_for_skill: str | None = None,
            original_objective: str | None = None,
            scope: str = "") -> LoopOutcome:
        """Run one mission end-to-end.

        ``task`` is the *full* prompt the engineer sees (typically a long
        string with prelude, identity card, and live objective). It is
        the right thing to feed to the engineer because round prompts are
        meant to carry full context.

        ``objective_for_skill`` is the *clean* operator objective, with
        no prelude / boilerplate / identity-card prefix. It is what the
        skill matcher and ``task_history`` should see —
        otherwise we end up indexing skills under "### Memory context"
        boilerplate (literally happened, see commit history).
        Falls back to ``task`` when not supplied for back-compat.
        """
        workdir = Path(workdir) if workdir else Path.cwd()
        run_id = self.config.session_id or f"run-{uuid.uuid4().hex}"
        from .roles.prompts import resolve_role_prompt
        from .roles.prompts.engineer import (
            SKILL_ADAPT,
            SKILL_CREATE,
            mission_request,
            skill_request,
        )
        engineer_prompt_context = resolve_role_prompt(mission_request(workdir))
        active_vertical = engineer_prompt_context.vertical
        engineer_role_banner = engineer_prompt_context.role_banner
        scientist_create_banner = resolve_role_prompt(
            skill_request(
                workdir,
                operation=SKILL_CREATE,
                vertical=active_vertical,
            )
        ).role_banner
        scientist_adaptation_banner = resolve_role_prompt(
            skill_request(
                workdir,
                operation=SKILL_ADAPT,
                vertical=active_vertical,
            )
        ).role_banner
        if self.config.wiki_enabled:
            from .wiki.lifecycle import ensure_project_wiki

            ensure_project_wiki(
                workdir,
                enabled=self.config.auto_init_wiki,
                on_event=self.on_event,
            )
        skill_task = (objective_for_skill or task).strip() or task
        request_anchor = (original_objective or objective_for_skill or task).strip() or task
        self._emit({
            "type": EventType.LOOP_START,
            "text": f"task: {skill_task[:120]}",
        })

        mission = MissionContext(
            workdir=workdir,
            run_id=run_id,
            task=task,
            skill_task=skill_task,
            request_anchor=request_anchor,
            active_vertical=active_vertical,
            engineer_role_banner=engineer_role_banner,
            scientist_create_banner=scientist_create_banner,
            scientist_adaptation_banner=scientist_adaptation_banner,
            seed_thread_id=seed_thread_id,
            scope=scope,
        )

        # Step 1/2: matcher + Scientist distill/adapt + venue/idea research
        # candidate sources (role mission — shared scaffold across all roles).
        state = self._select_and_prepare_skill(mission)

        # Step 3: supervised round-loop. The four small wrappers below adapt
        # this mixin's ``(self, mission, state, ...)`` phase methods to the
        # exact bare-callable signatures ``SupervisedEngineer.run`` expects.
        def build_prompt(next_action: str | None, include_static: bool = True) -> str:
            return self._build_round_prompt(mission, state, next_action, include_static)

        def prepare_review_context() -> None:
            return self._prepare_review_context(mission)

        def capture_reviewed_round(record: RoundRecord) -> None:
            return self._capture_reviewed_round(mission, record)

        status, rounds, final_message, reason, last_thread_id = self.supervised.run(
            objective=task,
            original_objective=request_anchor,
            engineer_prompt_builder=build_prompt,
            supervised_config=SupervisedConfig(
                max_rounds=self.config.max_rounds,
                no_progress_threshold=self.config.no_progress_threshold,
                soft_round_limit=self.config.soft_round_limit,
                hard_escalate_rounds=self.config.hard_escalate_rounds,
                backend_failure_threshold=self.config.backend_failure_threshold,
                backend_failure_backoff_seconds=self.config.backend_failure_backoff_seconds,
                session_id=self.config.session_id,
                checkpoint_path=self.config.checkpoint_path,
                context_packet_path=self.config.context_packet_path,
                engineer_log_path=self.config.engineer_log_path,
            ),
            workdir=workdir,
            on_event=self.on_event,
            seed_thread_id=seed_thread_id,
            scope=scope,
            prepare_review_context=prepare_review_context,
            review_completed_hook=capture_reviewed_round,
            continue_adaptor=None,
            reviewer_skill_block=state.reviewer_skill_block,
        )
        if self.pre_settlement_guard is not None:
            status, final_message, reason = self.pre_settlement_guard(
                mission,
                state,
                status,
                rounds,
                final_message,
                reason,
            )

        # Step 4: learn from the OUTCOME and settle the final LoopOutcome.
        return self._settle_mission_outcome(
            mission, state, status, rounds, final_message, reason, last_thread_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # never let UI errors kill the loop
            log.exception("on_event handler raised")

__all__ = ["SkillLoop", "SkillLoopConfig"]
