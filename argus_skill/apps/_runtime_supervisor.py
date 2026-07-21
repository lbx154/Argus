"""Supervisor driver: mission-type/workflow-mode resolution for a project
root, ``run_life_supervisor`` (the non-interactive drain-a-backlog driver),
and ``_invoke_supervisor`` (assemble a runtime context + run the supervisor
for a single backend — used by both ``life run`` and chat-mode free text).

Split out of ``_runtime.py`` so that module stays under the maintainability
line-count target. Every name here is re-exported from ``_runtime.py`` (see
its module docstring and ``__all__``) so external imports are unaffected.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from ..core.knobs import resolve_role_model, resolve_role_reasoning_effort
from ..life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from ._env import env_flag as _env_flag
from ._runtime_construction import _inbox_drainer_for, _pending_question_resolver_for
from ._runtime_helpers import (
    LifeStderrSink,
    _memory_global_root,
    _memory_project_root,
    _SplitMemory,
)

log = logging.getLogger(__name__)


def _paper_mission_for_project_root(project_root: Path | str) -> bool:
    """Return True only for an explicitly resolved paper-shaped vertical.

    Missing/corrupt state is deliberately non-paper.  ``resolve_vertical`` has
    a compatibility fallback to ``research`` for undecided projects; using that
    fallback as a mission-type signal caused ordinary bounded tasks to pay for
    paper idea search and inherit EMNLP guidance. A persisted Manager decision
    is required here.
    """
    try:
        from ..skills.vertical_select import _persisted_vertical
        from ..verticals._base import load_vertical, vertical_completion_gate

        root = Path(project_root).expanduser()
        persisted = _persisted_vertical(root)
        if persisted is None:
            return False
        vertical = persisted
        return vertical_completion_gate(load_vertical(vertical, project_root=root)) == "full_paper"
    except Exception:  # noqa: BLE001 — mission typing must fail safe
        return False


def _independent_review_required_for_project_root(
    project_root: Path | str,
) -> bool:
    """Return the persisted vertical's mandatory independent-review policy."""
    try:
        from ..skills.vertical_select import _persisted_vertical
        from ..verticals._base import (
            load_vertical,
            vertical_requires_independent_review,
        )

        root = Path(project_root).expanduser()
        persisted = _persisted_vertical(root)
        if persisted is None:
            return False
        return vertical_requires_independent_review(load_vertical(persisted, project_root=root))
    except Exception:  # noqa: BLE001 — unresolved projects keep legacy behavior
        return False


def _workflow_mode_for_project_root(project_root: Path | str) -> str:
    """Resolve the Manager-persisted workflow contract; fail safe to staged."""
    try:
        from ..skills.vertical_select import resolve_workflow_mode

        return resolve_workflow_mode(Path(project_root).expanduser())
    except Exception:  # noqa: BLE001
        return "staged"


def _build_supervisor_config(
    *,
    global_daily_cap_usd: float,
    once: bool,
    max_missions: int,
    project_worktree: Path | None,
    stop_event: threading.Event,
    project_root: Path,
    artifact_root: Path | None = None,
    runtime_context: str,
    continuous: bool,
    continuous_objective: str,
    open_ended: bool,
) -> LifeSupervisorConfig:
    from ..life.telemetry import telemetry_interval_from_env

    # Mission type follows a positive Manager-authored vertical decision.  An
    # undecided or malformed project is bounded/non-paper, never implicitly an
    # EMNLP campaign.
    paper_mission = _paper_mission_for_project_root(artifact_root or project_root)

    return LifeSupervisorConfig(
        budget=LifeBudget(
            global_daily_cap_usd=global_daily_cap_usd,
            max_missions=1 if once else max_missions,
        ),
        poll_interval_seconds=2.0,
        project_worktree=(
            Path(project_worktree).expanduser() if project_worktree is not None else None
        ),
        stop_event=stop_event,
        user_inbox=_inbox_drainer_for(
            project_root,
            project_root=artifact_root or project_worktree or project_root,
        ),
        pending_question_resolver=_pending_question_resolver_for(project_root),
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
        full_paper_gate=paper_mission and open_ended,
        paper_mission=paper_mission,
        telemetry_dir=project_root,
        artifact_root=artifact_root or project_root,
        telemetry_interval_seconds=telemetry_interval_from_env(),
    )


def run_life_supervisor(
    *,
    mem: _SplitMemory,
    runner: Any,
    engineer_model: str,
    reviewer_model: str,
    once: bool,
    max_missions: int,
    global_daily_cap_usd: float,
    project_worktree: Path | None = None,
    artifact_root: Path | None = None,
    quiet: bool = False,
    runtime_context: str = "",
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
) -> dict[str, Any]:
    """Run ``LifeSupervisor`` with proper signal-handler save/restore.

    Restoring previous SIGINT/SIGTERM handlers on exit keeps the foreground
    caller's Ctrl-C semantics after a run finishes.
    """
    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:  # noqa: ANN401
        print(f"\nlife: received signal {signum}, requesting stop", file=sys.stderr)
        stop_event.set()

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        from ..life.event_log import JsonlEventSink

        stderr_sink = LifeStderrSink(quiet=quiet)
        project_root = _memory_project_root(mem)
        sink = JsonlEventSink(stderr_sink, life_dir=project_root)
        # Manager divides the Task first: classify the vertical, split into its
        # Stage template, and COMMIT the choice. The supervisor below then TRUSTS
        # the persisted vertical (life/supervisor/_core.py:2460) and won't
        # re-classify. Missing execution handoffs fail closed so raw operator
        # routing/presentation instructions never reach Planner/Engineer.
        if continuous and str(continuous_objective).strip():
            try:
                # Prefer the runner's single Manager instance (manager backend);
                # fall back to an ad-hoc Manager only when the runner has none
                # (e.g. the memory runner used in tests).
                mgr = getattr(runner, "manager", None)
                if mgr is None:
                    from ..manager import Manager

                    mgr = Manager(
                        project_root=artifact_root or project_root,
                        runner=getattr(runner, "manager_backend", None)
                        or getattr(runner, "backend", None),
                        skill_store=getattr(runner, "_manager_skill_store", None),
                    )
                division = mgr.divide(
                    continuous_objective,
                    ask_on_new_domain=_env_flag("ARGUS_SKILL_DOMAIN_ASK", False),
                )
                # Headless driver: there is no live operator turn here, so an
                # ask-mode proposal cannot be confirmed interactively. Commit it
                # with a notice rather than discarding the authored domain. An
                # interactive front-end instead surfaces ``proposed_domain`` and
                # calls ``mgr.commit_domain`` after the operator confirms.
                if (
                    getattr(division, "pending_confirmation", False)
                    and getattr(division, "proposed_domain", None) is not None
                ):
                    if not quiet:
                        print(
                            "[manager] ARGUS_SKILL_DOMAIN_ASK set but no interactive "
                            f"turn here — committing proposed domain `{division.vertical}`",
                            file=sys.stderr,
                        )
                    division = mgr.commit_domain(
                        division.task,
                        division.proposed_domain,
                        execution_task=division.execution_task,
                        workflow_mode=division.workflow_mode,
                    )
                from ..manager.front_door import require_manager_execution_task

                continuous_objective = require_manager_execution_task(division)
                if not quiet:
                    print(division.headline(), file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 — fail closed, preserve bounded work
                log.error(
                    "Manager handoff failed; continuous objective not dispatched: %s",
                    exc,
                )
                continuous = False
                continuous_objective = ""
        cfg = _build_supervisor_config(
            global_daily_cap_usd=global_daily_cap_usd,
            once=once,
            max_missions=max_missions,
            project_worktree=project_worktree,
            stop_event=stop_event,
            project_root=project_root,
            artifact_root=artifact_root,
            runtime_context=runtime_context,
            continuous=continuous,
            continuous_objective=continuous_objective,
            open_ended=open_ended,
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=cfg,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            planner_runner=getattr(runner, "planner_backend", None)
            or getattr(runner, "backend", None),
        )
        return sup.run()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _invoke_supervisor(
    *,
    mem: _SplitMemory,
    backend: str,
    once: bool,
    max_missions: int,
    global_daily_cap_usd: float,
    quiet: bool = False,
    seed_thread_id: str | None = None,
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> tuple[dict[str, Any], str | None]:
    ns = argparse.Namespace()
    ns.backend = backend
    ns.engineer_model = resolve_role_model(
        "engineer",
        role_env="ARGUS_SKILL_ENGINEER_MODEL",
    )
    ns.reviewer_model = resolve_role_model(
        "reviewer",
        role_env="ARGUS_SKILL_REVIEWER_MODEL",
    )
    ns.engineer_reasoning_effort = resolve_role_reasoning_effort(
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    )
    ns.reviewer_reasoning_effort = resolve_role_reasoning_effort(
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    )
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(_memory_global_root(mem) / "skills"),
    )
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
    os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(_memory_project_root(mem) / "events.jsonl")
    try:
        ns.manager_session_root = str(_memory_project_root(mem))
        ns.project_state_dir = str(_memory_project_root(mem))
    except Exception:  # noqa: BLE001
        ns.manager_session_root = None
        ns.project_state_dir = None
    # Life-mode default: 500 engineer rounds. The earlier low cap was
    # too small for "implement + test + polish" tasks that need many
    # tool calls. Override via ARGUS_SKILL_MAX_ROUNDS.
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "500"))

    # Runtime context injected into every mission prelude so the agent
    # knows its own backend, models, and budget constraints at runtime.
    runner_backend = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or backend
    mode_label = "continuous" if continuous else "single-shot"
    runtime_context = (
        f"## Runtime info\n"
        f"- Life backend: {backend}\n"
        f"- Runner backend: {runner_backend}\n"
        f"- Engineer model: {ns.engineer_model}\n"
        f"- Reviewer model: {ns.reviewer_model}\n"
        f"- Engineer reasoning effort: {ns.engineer_reasoning_effort or '(default)'}\n"
        f"- Reviewer reasoning effort: {ns.reviewer_reasoning_effort or '(default)'}\n"
        f"- Max rounds per mission: {ns.max_rounds}\n"
        f"- Host-global daily budget cap: ${global_daily_cap_usd:.2f}\n"
        f"- Mode: {mode_label}\n"
        f"- Command workdir: {Path.cwd()}\n"
        f"- Harness artifact root: {_memory_project_root(mem)}\n"
        "- Keep pipeline/checklist/domain/audit artifacts in the harness artifact "
        "root; do not reuse stale `research/` state from the command workdir.\n"
    )
    from ..life.research_profile import render_research_profile_context

    research_context = render_research_profile_context()
    if research_context:
        runtime_context = runtime_context + "\n---\n\n" + research_context

    # Lazy proxy: ``build_life_runner`` is monkeypatched directly on the
    # ``_runtime`` facade module by tests (e.g.
    # tests/daemon/test_life_worker.py,
    # tests/manager/test_front_door_workspace.py). Resolving it here at
    # call time — rather than via a top-level import into this module —
    # keeps that monkeypatch effective even though this function now lives
    # in a sibling module.
    from ._runtime import build_life_runner

    runner = build_life_runner(ns, seed_thread_id=seed_thread_id)
    # Chat fast-path is operator-front-door-only: only human free text sent to the
    # cockpit is eligible. Planner / backlog / daemon missions keep the
    # runner default (False) so the harness never classifies agent work.
    if hasattr(runner, "_allow_chat_fast_path"):
        runner._allow_chat_fast_path = bool(allow_chat_fast_path)
    summary = run_life_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=ns.engineer_model,
        reviewer_model=ns.reviewer_model,
        once=once,
        max_missions=max_missions,
        global_daily_cap_usd=global_daily_cap_usd,
        project_worktree=getattr(mem, "project_worktree", None) or Path.cwd(),
        artifact_root=_memory_project_root(mem),
        quiet=quiet,
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
    )
    final_thread_id = getattr(runner, "last_thread_id", None)
    return summary, final_thread_id
