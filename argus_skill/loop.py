"""SkillLoop — agent-native Skill discovery plus supervised engineering.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore``: path-only access to agent-readable Skill libraries.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that accepts decisive Engineer self-verification for
    bounded work or otherwise supervises until the Reviewer is satisfied.

Skill and wiki memory normally use independent review. For a bounded mission,
the Engineer may explicitly self-verify and waive Reviewer; if it also identifies
durable skill learning, the same Engineer thread is resumed once to author the
create or update semantic Skill documents directly in the project library.

End-to-end shape:

    task → Skill-library paths → engineer round-loop (engineer → reviewer)
            outcome → preserve Agent-authored semantic memory edits
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
from .skills.loop_skill_library import SkillLibraryMixin
from .skills.loop_state import MissionContext
from .skills.missions import EngineerMission
from .skills.store import SkillStore

log = logging.getLogger(__name__)

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
    reviewer_model: str | None = None  # default: same as engineer
    # Direct/bounded work starts at high. A Reviewer-requested second round
    # escalates to ``engineer_reasoning_effort`` (xhigh by default). Staged and
    # paper missions retain xhigh from round one.
    engineer_initial_reasoning_effort: str | None = "high"
    engineer_reasoning_effort: str | None = "xhigh"
    reviewer_reasoning_effort: str = "high"
    # Completed tasks may retain durable learning when the Agent judges it useful.
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
    # Shared declarative knowledge wiki. Roles edit pages directly.
    wiki_enabled: bool = False
    # Bootstrap one project wiki before the first mission.
    # Library callers remain opt-in; the daemon runtime enables this by default.
    auto_init_wiki: bool = False
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    extra_args: list[str] | None = None
    session_id: str | None = None
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

    def resolved_initial_engineer_effort(self) -> str | None:
        if self.workflow_mode != "direct" or self.paper_mission:
            return self.engineer_reasoning_effort
        env = os.environ.get(
            "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", ""
        ).strip()
        return env or self.engineer_initial_reasoning_effort


class SkillLoop(
    SkillLibraryMixin,
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

        self.skill_store = skill_store or SkillStore(self.skills_dir)
        self.engineer_mission = EngineerMission(
            self.skill_store, on_event=self.on_event
        )
        self.reviewer = Reviewer(
            self.reviewer_runner,
            skill_store=self.skill_store,
            memory_maintenance_enabled=self.config.require_post_task_learning,
        )
        # Skill discovery and maintenance are agent-native.  Roles receive the
        # library roots and use their own file tools; there is no matcher/router
        # decision layer and no runtime Skill mutation channel.
        self.skill_router = None
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

        ``objective_for_skill`` remains a clean objective label for event and
        role context compatibility. Skill discovery itself is performed by the
        Agents directly from the library paths.
        """
        workdir = Path(workdir) if workdir else Path.cwd()
        run_id = self.config.session_id or f"run-{uuid.uuid4().hex}"
        from .roles.prompts import resolve_role_prompt
        from .roles.prompts.engineer import mission_request
        engineer_prompt_context = resolve_role_prompt(mission_request(workdir))
        active_vertical = engineer_prompt_context.vertical
        engineer_role_banner = engineer_prompt_context.role_banner
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
            seed_thread_id=seed_thread_id,
            scope=scope,
        )

        # Step 1/2: expose Skill-library paths and prepare optional research
        # sources. No Skill is parsed, matched, adapted, or injected.
        state = self._prepare_skill_libraries(mission)

        # Step 3: supervised round-loop. The four small wrappers below adapt
        # this mixin's ``(self, mission, state, ...)`` phase methods to the
        # exact bare-callable signatures ``SupervisedEngineer.run`` expects.
        def build_prompt(next_action: str | None, include_static: bool = True) -> str:
            return self._build_round_prompt(mission, state, next_action, include_static)

        def prepare_review_context() -> None:
            return self._prepare_review_context(mission)

        def capture_reviewed_round(record: RoundRecord) -> None:
            return self._capture_reviewed_round(mission, record)

        def adapt_after_rejections(rounds: list[RoundRecord]) -> str:
            return self._adapt_after_rejections(mission, state, rounds)

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
            continue_adaptor=adapt_after_rejections,
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
