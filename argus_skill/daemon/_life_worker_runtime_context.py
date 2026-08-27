"""Runner-namespace construction and ``LifeSupervisorConfig`` assembly for the
7x24 daemon worker.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. Used only by the boot/run lifecycle-phase
mixins (``_life_worker_boot.py`` / ``_life_worker_run.py``); no test
monkeypatches any name in this module directly.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import paths as core_paths
from ..life.supervisor import LifeBudget, LifeSupervisorConfig
from .config import LifeWorkerConfig

if TYPE_CHECKING:
    from .life_worker import LifeWorker

log = logging.getLogger(__name__)


def _runner_namespace(cfg: LifeWorkerConfig) -> Any:
    """Build the argparse-shaped namespace ``build_life_runner`` expects."""
    import argparse

    ns = argparse.Namespace()
    ns.backend = cfg.backend
    ns.engineer_model = cfg.engineer_model
    ns.reviewer_model = cfg.reviewer_model
    ns.engineer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        cfg.engineer_reasoning_effort,
    )
    ns.reviewer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
        cfg.reviewer_reasoning_effort,
    )
    default_skills_dir = (
        core_paths.shared_skills_root()
        if cfg.global_root is None
        else Path(cfg.global_root) / "skills"
    )
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(default_skills_dir),
    )
    ns.workdir = (
        str(cfg.project_workdir)
        if cfg.project_workdir is not None
        else os.environ.get("ARGUS_SKILL_WORKDIR")
    )
    ns.manager_session_root = str(cfg.life_dir)
    ns.global_root = str(cfg.global_root or core_paths.global_root())
    # Canonical per-session state directory for checkpoint + execution log.
    # This must not be re-derived by hashing project_workdir.
    ns.project_state_dir = str(cfg.life_dir)
    # This is the ONE runner construction that actually drives real mission
    # rounds 7×24, so it is the only one that should ever consume a pending
    # running-item abort request (see
    # ``apps/_runtime.py:_SkillLoopRunner._stop_reason``) — the front-door
    # quick-reply runner never
    # sets this, so the Manager's own SELF-turn can never abort itself.
    ns.enable_mission_abort_signal = True
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "0"))
    ns.plan_mode = os.environ.get("ARGUS_SKILL_PLAN_MODE", "auto")
    ns.plan_model = os.environ.get("ARGUS_SKILL_PLAN_MODEL")
    ns.color = None
    ns.verbose = False
    ns.quiet = True
    # Propagate campaign lifetime metadata so execute() can pass open_ended and
    # continuous_objective to _decide_stage_transition via SkillLoopConfig.
    # Without this the Manager stage hook defaults to open_ended=False, which
    # causes final_stage_completion_decision to overwrite the Manager's own
    # structured rollback verdict with a bounded completion.
    ns.open_ended = cfg.continuous_open_ended
    ns.continuous_objective = cfg.continuous_objective
    return ns


def _worker_runtime_context(
    cfg: LifeWorkerConfig,
    *,
    paper_mission: bool | None = None,
) -> str:
    """Return static context injected into daemon-driven missions.

    ``paper_mission`` is the already-resolved vertical signal.  It scopes
    operator prompts and suppresses a configured research profile for bounded
    work without guessing from objective prose. ``None`` preserves the legacy
    all-context view for diagnostics and direct callers.
    """
    from ..life.research_profile import render_research_profile_context
    from ..life.special_prompts import render_special_prompts_context
    from ..tools.capability_vault import format_api_context, format_gpu_context

    # Operator directives ("special prompts") are machine-specific house
    # rules; they lead the runtime context so the agent sees them first.
    special_context = render_special_prompts_context(paper_mission=paper_mission)
    research_context = render_research_profile_context() if paper_mission is not False else ""
    if not research_context:
        return special_context
    argus_python = os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable
    gpu_context = format_gpu_context()
    runtime_context = (
        "## Agent Architecture (3-layer)\n"
        "Planner → Engineer → Reviewer. No Critic, no Scientist.\n"
        "\n"
        "### Engineer (you)\n"
        "- Do ALL work: code, experiments, LaTeX, figures, compilation.\n"
        "- Read the **stage checklist** the Reviewer will evaluate (injected\n"
        "  near the top of every round's prompt) and produce the artifacts\n"
        "  each unchecked item names. There is no `validate-*` CLI any more —\n"
        "  read files directly when you need to confirm state.\n"
        "- Focus on producing artifacts. Do not verify your own output; the\n"
        "  Reviewer is responsible for that.\n"
        "\n"
        "### Reviewer (automatic after each round)\n"
        "- Runs stage-aware checklist (only checks relevant to current pipeline stage)\n"
        "- Decides done/continue/blocked based on evidence\n"
        "- If continue: gives you a specific next_action\n"
        "\n"
        "## Runtime info\n"
        f"- Engineer model: {cfg.engineer_model}\n"
        f"- Reviewer model: {cfg.reviewer_model}\n"
        f"- Host-global daily budget: ${cfg.global_daily_cap_usd:.0f}\n"
        "\n"
        "## Python environments (CRITICAL)\n"
        f"- argus-skill commands: `{argus_python}`\n"
        "- ML/training/inference: use the PROJECT venv at `.venv/bin/python`\n"
        "- If project .venv does not exist, CREATE IT FIRST:\n"
        "  `python3 -m venv .venv --system-site-packages && "
        ".venv/bin/pip install torch diffusers transformers accelerate peft safetensors`\n"
        "- NEVER install torch/diffusers in the argus-skill venv\n"
        "- See skill: project-environment-management\n"
        "\n"
        "## Sub-agents for GPU tasks (CRITICAL — do NOT block on long tasks)\n"
        "- ANY command >30s (training, inference, evaluation) MUST use subagent:\n"
        "  `python -m argus_skill.tools.subagent submit --task-id <id> "
        "--description '<desc>' --command '.venv/bin/python code/train.py ...'`\n"
        "- After submitting, continue other work (write code, prepare analysis, draft paper sections)\n"
        "- Check status: `python -m argus_skill.tools.subagent status --task-id <id>`\n"
        "- List all: `python -m argus_skill.tools.subagent list`\n"
        "- You are NEVER blocked waiting for GPU experiments — submit and move on\n"
    )
    if gpu_context:
        runtime_context += "\n" + gpu_context + "\n"
    api_context = format_api_context()
    if api_context:
        runtime_context += "\n" + api_context + "\n"
    body = runtime_context + "\n---\n\n" + research_context
    if special_context:
        return special_context + "\n\n---\n\n" + body
    return body


def _build_supervisor_config(
    cfg: LifeWorkerConfig,
    *,
    runtime_root: Path,
    stop_event: Any,
    init_continuous: bool,
    init_objective: str,
    continuous_provider: Any,
    post_mission_hook: Any,
) -> LifeSupervisorConfig:
    from ..apps._runtime import (
        _final_certification_for_project_root,
        _inbox_drainer_for,
        _paper_mission_for_project_root,
        _pending_question_resolver_for,
    )
    from ..manager._session_ops import manager_pipeline_yield_requested

    paper_mission = _paper_mission_for_project_root(runtime_root)
    final_certification = _final_certification_for_project_root(runtime_root)

    return LifeSupervisorConfig(
        budget=LifeBudget(
            global_daily_cap_usd=cfg.global_daily_cap_usd,
            max_missions=0,
        ),
        planner_task_iteration_max_cycles=cfg.planner_task_iteration_max_cycles,
        subagent_family_failure_streak_limit=cfg.subagent_family_failure_streak_limit,
        subagent_family_failure_window_hours=cfg.subagent_family_failure_window_hours,
        poll_interval_seconds=2.0,
        project_worktree=cfg.project_workdir,
        stop_event=stop_event,
        user_inbox=_inbox_drainer_for(
            runtime_root,
            project_root=cfg.project_workdir or runtime_root,
        ),
        pending_question_resolver=_pending_question_resolver_for(runtime_root),
        runtime_context=_worker_runtime_context(cfg, paper_mission=paper_mission),
        continuous=init_continuous,
        continuous_objective=init_objective,
        open_ended=cfg.continuous_open_ended,
        paper_mission=paper_mission,
        final_certification_gate=(
            final_certification and cfg.continuous_open_ended
        ),
        continuous_config_provider=continuous_provider,
        manager_pipeline_yield_provider=(lambda: manager_pipeline_yield_requested(runtime_root)),
        post_mission_hook=post_mission_hook,
        project_state_dir=runtime_root,
        artifact_root=runtime_root,
    )


class _DaemonSink:
    """Minimal sink: count mission completions and log daemon events."""

    def __init__(self, worker: LifeWorker, health_tracker: Any = None) -> None:
        self._worker = worker
        self.health_tracker = health_tracker
        self.self_maintenance: Any = None

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.health_tracker is not None:
            try:
                self.health_tracker.observe(event)
            except Exception:  # noqa: BLE001 - health telemetry is non-critical
                log.exception("daemon: health telemetry update failed")
        if self.self_maintenance is not None:
            try:
                self.self_maintenance.observe(event)
            except Exception:  # noqa: BLE001 - observation never blocks work
                log.exception("daemon: self-maintenance observation failed")
        kind = event.get("type") or event.get("kind") or ""
        if kind in (
            "life.mission.done",
            "life.mission.completed",
            "life.mission.failed",
            "life.mission.skipped",
        ):
            self._worker._missions_completed += 1
        log.debug("daemon event: %s %s", kind, event)


# ---------------------------------------------------------------------------
# PID lock + status
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Detach (POSIX double-fork)
# ---------------------------------------------------------------------------
