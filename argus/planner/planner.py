"""Planner agent — inspects the active project and delegates concrete work."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..core.handoff import HandOffActions
from ..core.models import READING_ROLE_UNUSED_COPILOT_TOOLS, RunnerOptions
from ..core.ports import RunnerBackend
from ..core.role_actions import role_actions
from ..core.role_session import (
    RoleSessionCapsule,
    objective_revision,
    role_session_policy,
)
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.runner_errors import (
    is_execution_host_startup_error,
    result_has_missing_resume_target,
)

PLANNER_SUPERSEDED_ERROR = "planner superseded by newer continuous generation"


@dataclass
class PlannerConfig:
    """Knobs the supervisor passes down to a Planner.plan_next() call."""

    model: str | None = None
    reasoning_effort: str | None = "high"
    working_dir: str | None = None
    state_root: str | None = None
    add_dirs: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = True
    full_auto: bool = False
    # Retained for configuration compatibility. Continuous Planner execution
    # is always forced through the read-only tool boundary in ``plan_next``.
    dangerous_yolo: bool = False
    open_ended: bool = False
    external_interrupt_reason_provider: Any = None
    role_session_path: Path | None = None
    on_event: Any = None
    # The stages a plan may move to; empty for a project without stages.
    stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSpec:
    """One concrete task the planner wants the engineering team to tackle next."""

    title: str
    objective: str  # full actionable description for the engineer
    impact_score: int = 0  # model-authored 0-5 priority metadata
    impact_area: str = ""
    evidence: str = ""
    # Mission-quality context. These are Planner-authored working claims, not
    # operator constraints; later evidence may challenge or replace them.
    hypothesis: str = ""
    goal_contribution: str = ""
    expected_regressions: str = ""
    decision_rule: str = ""
    # Project-relative repository root for this task. Empty means the current
    # campaign root; a nested Git root is adopted for subsequent missions.
    execution_workdir: str = ""
    # One decisive completion check plus explicit read-only inputs. These form
    # the canonical Planner→Engineer context packet instead of forcing every
    # fresh session to rediscover the whole project.
    acceptance_check: str = ""
    non_goals: list[str] = field(default_factory=list)
    context_refs: list[dict[str, str]] = field(default_factory=list)
    scope: str = "bounded"
    # A mission expected to satisfy the current-stage gate must receive an
    # independent Reviewer verdict so the Manager gets per-item evidence.
    stage_closing: bool = False
    # --- DAG fields (optional; flat tasks leave both at their defaults) ----
    # ``key`` is this task's *local* reference name, unique within one batch
    # of ``new_tasks``. Sibling tasks point at it via ``deps``. The supervisor
    # maps these local keys to the real backlog item ids when it enqueues the
    # batch (the keys themselves never reach the backlog). Empty ``key`` /
    # empty ``deps`` (the default) ⇒ a plain flat task, scheduled exactly as
    # before the DAG existed.
    key: str = ""
    deps: list[str] = field(default_factory=list)
    authorization_id: str = ""
    authorization_action: str = ""
    require_independent_review: bool = True
    skip_stage_transition: bool = False
    allow_skill_changes: bool = False
    parallel_safe: bool = False
    owns_paths: list[str] = field(default_factory=list)
    # A reviewed interim package for the operator while the campaign goes on;
    # it earns a non-terminal ``stage_package`` delivery, never completion.
    stage_package: bool = False
    # GPUs the task holds while it runs; claimed only when that many are free.
    gpu_count: int = 0
    # CPU cores the task keeps busy; claimed only when that many are free.
    cpu_count: int = 0
    # Mission-level role selected by Planner. Empty inherits the campaign
    # vertical chosen by Manager at the front door.
    vertical: str = ""


@dataclass(frozen=True)
class PlannerVerdict:
    """Planner decision: new work, task retirements, a handoff, or project done."""

    project_done: bool
    summary: str
    new_tasks: list[TaskSpec] = field(default_factory=list)
    raw_text: str = ""
    error: str = ""
    # Who acts next when the plan starts nothing: ``engineer`` -- the work in
    # flight goes on; ``manager`` -- the stage blocks the plan; ``operator`` --
    # only the operator can unblock it, asked ``operator_question`` with
    # ``operator_options``. Empty when tasks start or the goal is met. The
    # Planner is called again when something new is handed to it.
    handoff: str = ""
    operator_question: str = ""
    operator_options: tuple[dict[str, Any], ...] = ()
    advance_to_stage: str = ""
    # Host-visible, non-error notes the Supervisor emits on their own.
    diagnostics: tuple[str, ...] = ()
    retire_tasks: tuple[tuple[str, str], ...] = ()
    # Idle operator instructions this decision handles; the host settles
    # them only when it applies the decision.
    settled_instructions: tuple[int, ...] = ()
    assumptions: tuple[dict[str, str], ...] = ()
    plan_update: str = ""


class Planner:
    """Project-level read-only planning authority."""

    def __init__(
        self,
        runner: RunnerBackend,
        *,
        skill_store: Any | None = None,
        memory_maintenance_enabled: bool | None = None,
    ) -> None:
        self.runner = runner
        # Agent-native library roots; Planner searches and reads them directly.
        self.skill_store = skill_store
        if memory_maintenance_enabled is None:
            from ..core.knobs import learning_enabled

            memory_maintenance_enabled = learning_enabled()
        self.memory_maintenance_enabled = memory_maintenance_enabled
        from ..skills.missions import PlannerMission

        self.mission = PlannerMission(skill_store)

    # ------------------------------------------------------------------
    # Planner role — project-level planning
    # ------------------------------------------------------------------

    def plan_next(
        self,
        *,
        continuous_objective: str,
        journal_tail: str = "",
        research_plan: str = "",
        planning_cycle: int = 0,
        runtime_change_summary: str = "",
        config: PlannerConfig | None = None,
        journal_delta: str | None = None,
        research_plan_unchanged: bool = False,
    ) -> PlannerVerdict:
        """Inspect the active objective and delegate the next concrete work.

        ``journal_delta`` and ``research_plan_unchanged`` describe what this
        role session has already been shown: the delta holds only the journal
        entries that settled after the previous planning turn (empty string
        means nothing new), and the flag marks the research plan as identical
        to the one the session already read. Both apply only when the session
        actually resumes — a fresh or rotated session ignores them and
        receives the full ``journal_tail`` and ``research_plan``, so a stale
        caller-side record can never starve a new thread of context.
        """
        cfg = config or PlannerConfig()
        workdir = Path(cfg.working_dir).resolve() if cfg.working_dir else Path.cwd()
        backend_name = str(getattr(self.runner, "backend", type(self.runner).__name__))
        session_policy = role_session_policy(backend_name)
        session = RoleSessionCapsule.open(
            role="planner",
            policy=session_policy,
            objective_revision=objective_revision(continuous_objective),
            workdir=workdir,
            backend=backend_name,
            model=str(cfg.model or ""),
            checkpoint_path=None,
            path=(
                cfg.role_session_path
                if session_policy != "fresh"
                else None
            ),
        )
        resume_thread_id = session.prepare()

        def render(resumed: bool) -> tuple[str, str]:
            """The prompt the session records, and the one sent to the provider."""
            prompt_builder = (
                self._build_resumed_planner_prompt
                if resumed
                else self._build_planner_prompt
            )
            prompt = prompt_builder(
                continuous_objective=continuous_objective,
                journal_tail=journal_tail,
                research_plan=research_plan,
                planning_cycle=planning_cycle,
                runtime_change_summary=runtime_change_summary,
                mission=self.mission,
                open_ended=cfg.open_ended,
                memory_maintenance_enabled=self.memory_maintenance_enabled,
                project_root=workdir,
                state_root=cfg.state_root,
                journal_delta=journal_delta,
                research_plan_unchanged=research_plan_unchanged,
            )
            if session.prompt_block():
                prompt = session.prompt_block() + "\n\n" + prompt
            full_prompt = prompt
            return full_prompt, session.resume_prompt(full_prompt)

        full_prompt, prompt = render(bool(resume_thread_id))
        libraries = self.mission.libraries()
        # A sandboxed backend reads only what it is given: the Skill libraries
        # the prompt names are read where they lie.
        add_dirs = [*(cfg.add_dirs or ()), *(str(path) for path in libraries.library_roots)]
        planner_options = RunnerOptions(
            model=cfg.model,
            reasoning_effort=cfg.reasoning_effort or "xhigh",
            working_dir=cfg.working_dir,
            add_dirs=add_dirs or None,
            write_dirs=[cfg.state_root] if cfg.state_root else None,
            # Planner chooses and delegates work; it does not execute it. Keep
            # the boundary role-owned so an upstream yolo setting cannot grant
            # shell, network, build, test, or write tools to this call.
            dangerous_yolo=False,
            full_auto=False,
            sandbox_mode="read-only",
            # The global yolo policy lifts the read-only mode above, which
            # would hand this call the CLI's whole tool surface. GitHub/web
            # search and apply_patch stay: the contract sends the Planner to
            # primary sources, and wiki upkeep edits pages.
            lean_tool_surface=True,
            excluded_tools=READING_ROLE_UNUSED_COPILOT_TOOLS,
            skip_git_repo_check=cfg.skip_git_repo_check,
            extra_args=list(cfg.extra_args) if cfg.extra_args else None,
            skill_paths=[str(path) for path in libraries.native_paths],
            external_interrupt_reason_provider=cfg.external_interrupt_reason_provider,
        )
        actions = HandOffActions("plan", check=partial(_plan, open_ended=cfg.open_ended, stages=cfg.stages))
        started_at = time.monotonic()
        try:
            with role_actions(self.runner, planner_options, actions) as bound:
                result = gateway_run_exec(
                    self.runner,
                    prompt=prompt,
                    resume_thread_id=resume_thread_id,
                    options=bound,
                    run_label=f"planner.cycle{planning_cycle}",
                    # A lost thread restarts below with the full prompt.
                    fresh_on_missing_resume=False,
                )
                if resume_thread_id and result_has_missing_resume_target(result):
                    # The provider no longer has the thread (Copilot prunes sessions
                    # idle past its retention window). The resume delta assumes the
                    # contract and older journal are in the session, so a fresh
                    # thread gets the full prompt instead.
                    session.rotate("missing_provider_session")
                    session.prepare()
                    full_prompt, prompt = render(False)
                    result = gateway_run_exec(
                        self.runner,
                        prompt=prompt,
                        resume_thread_id=None,
                        options=bound,
                        run_label=f"planner.cycle{planning_cycle}",
                    )
        except Exception as exc:  # noqa: BLE001
            session.rotate("backend_exception")
            exc_text = f"{type(exc).__name__}: {exc}"
            return PlannerVerdict(
                project_done=False,
                summary="planner backend raised; will retry later",
                raw_text=exc_text,
                error=exc_text,
            )
        text = "\n".join(getattr(result, "agent_messages", None) or [])
        session_metadata_persisted = session.complete(
            result, decisive_output=text, prompt=full_prompt
        )
        failed = (
            int(getattr(result, "exit_code", 0) or 0) != 0
            or bool(getattr(result, "fatal_error", None))
        )
        stderr_tail = "\n".join(
            str(line) for line in (getattr(result, "stderr_lines", None) or [])[-20:]
        )
        fatal = str(getattr(result, "fatal_error", "") or "").strip()
        details = "\n".join(part for part in (fatal, stderr_tail) if part).strip()
        if failed:
            session.rotate("backend_failure")
        verdict = None if failed else _verdict(actions.handoff, text)
        if callable(cfg.on_event):
            cfg.on_event({
                "type": EventType.ROLE_SESSION_TURN,
                "role": "planner",
                **({"ends_work": True} if verdict is not None and verdict.project_done else {}),
                "policy": session.policy,
                "action": session.action,
                "rotation_reason": session.rotation_reason,
                "planning_cycle": planning_cycle,
                "session_id": str(getattr(result, "thread_id", "") or ""),
                "turns_on_session": session.turns,
                "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
                "cached_input_tokens": int(
                    getattr(result, "cached_input_tokens", 0) or 0
                ),
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "prompt_chars": len(prompt),
                "prompt_estimated_tokens": (len(prompt) + 3) // 4,
                "capsule_path": str(session.path or ""),
                "metadata_persisted": session_metadata_persisted,
                "persistence_warning": session.persistence_error,
                "operator_context_revision": int(
                    getattr(result, "operator_context_revision", 0) or 0
                ),
            })
        if failed:
            if is_execution_host_startup_error(fatal):
                return PlannerVerdict(
                    project_done=False,
                    summary="Planner execution host is unavailable; repair it before retrying.",
                        raw_text=text or details,
                    error=fatal,
                )
            if PLANNER_SUPERSEDED_ERROR in details:
                return PlannerVerdict(
                    project_done=False,
                    summary=PLANNER_SUPERSEDED_ERROR,
                        raw_text=text or details,
                    error=PLANNER_SUPERSEDED_ERROR,
                )
            return PlannerVerdict(
                project_done=False,
                summary="planner backend failed before producing output; will retry later",
                raw_text=text or details,
                error=details
                or f"planner backend exit {getattr(result, 'exit_code', 'unknown')}",
            )
        assert verdict is not None
        if cfg.stages and verdict.new_tasks and not verdict.advance_to_stage:
            return replace(verdict, diagnostics=("advance_to_stage missing; holding current stage",))
        return verdict

    @staticmethod
    def _build_resumed_planner_prompt(
        *,
        continuous_objective: str,
        journal_tail: str,
        research_plan: str = "",
        planning_cycle: int,
        runtime_change_summary: str = "",
        mission: Any | None = None,
        open_ended: bool = False,  # noqa: ARG004 - protocol parity with full prompt
        memory_maintenance_enabled: bool = True,  # noqa: ARG004 - same contract
        project_root: Path | str | None = None,
        state_root: Path | str | None = None,
        journal_delta: str | None = None,
        research_plan_unchanged: bool = False,
    ) -> str:
        from ..roles.prompts.planner import build_continuous_resume_prompt

        journal_is_delta = journal_delta is not None
        return build_continuous_resume_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_delta if journal_is_delta else journal_tail,
            research_plan=research_plan,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=mission,
            project_root=project_root,
            state_root=state_root,
            journal_is_delta=journal_is_delta,
            research_plan_unchanged=research_plan_unchanged,
        )

    @staticmethod
    def _build_planner_prompt(
        *,
        continuous_objective: str,
        journal_tail: str,
        research_plan: str = "",
        planning_cycle: int,
        runtime_change_summary: str = "",
        mission: Any | None = None,
        open_ended: bool = False,
        memory_maintenance_enabled: bool = True,
        project_root: Path | str | None = None,
        state_root: Path | str | None = None,
        journal_delta: str | None = None,  # noqa: ARG004 - resume-only context
        research_plan_unchanged: bool = False,  # noqa: ARG004 - resume-only context
    ) -> str:
        from ..roles.prompts.planner import build_continuous_prompt

        return build_continuous_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            research_plan=research_plan,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=mission,
            open_ended=open_ended,
            memory_maintenance_enabled=memory_maintenance_enabled,
            project_root=project_root,
            state_root=state_root,
        )

def _verdict(handoff: Any, text: str) -> PlannerVerdict:
    """What the Planner's last accepted call decided; no call is no decision."""
    if handoff is None:
        return PlannerVerdict(
            project_done=False, summary="planner ended without handing off; will plan again",
            raw_text=text, error="planner ended without handing off",
        )
    return replace(handoff.value, raw_text=text)


def _plan(payload: dict[str, Any], *, open_ended: bool, stages: tuple[str, ...]) -> PlannerVerdict:
    """The plan form's own rules (``core.handoff``): what the host can act on."""
    to, tasks = payload["to"], payload.get("tasks", [])
    if to == "done" and tasks:
        raise ValueError("to=done starts no tasks: the goal is met, or the tasks go to=engineer.")
    if to == "done" and open_ended:
        raise ValueError(
            "This goal is ongoing and never done: start the next distinct task, "
            "or hand to engineer and let the work in flight decide."
        )
    stage = payload.get("advance_to_stage")
    if stage and stage not in stages:
        raise ValueError(f"advance_to_stage: one of {', '.join(stages)}." if stages else "advance_to_stage: this project has no stages.")
    keys = {task.get("key") for task in tasks}
    for task in tasks:
        if not keys.issuperset(task.get("deps", ())):
            raise ValueError(f"tasks.deps of {task['title']!r}: name only keys of tasks in this call.")
        if task.get("parallel_safe") and not task.get("owns_paths"):
            raise ValueError(f"tasks of {task['title']!r}: parallel_safe needs the owns_paths it writes.")
    ask = payload.get("ask", {})
    return PlannerVerdict(
        project_done=to == "done",
        summary=payload["summary"],
        new_tasks=[TaskSpec(**task) for task in tasks],
        handoff="" if to == "done" or (to == "engineer" and tasks) else to,
        operator_question=ask.get("question", ""),
        operator_options=tuple(ask.get("options", ())),
        advance_to_stage=stage or "",
        retire_tasks=tuple((row["id"], row["reason"]) for row in payload.get("retire", ())),
        settled_instructions=tuple(dict.fromkeys(payload.get("settle_instructions", ()))),
        assumptions=tuple(payload["assumptions"]),
        plan_update=payload.get("plan_update", ""),
    )
