"""Life-mode 7×24 worker: detached background process that drains the
backlog forever.

This is the substrate behind ``argus-skill --daemon`` and the non-interactive
executor behind the Ink/Web cockpit. Both build the same
:class:`~argus_skill.life.supervisor.LifeSupervisor`
against the current project's split memory bundle, but the worker has
no TTY and exits only on SIGTERM /
SIGINT.

Coordination with the cockpit is provided by the backlog state machine
(:meth:`Backlog.claim_next` is atomic) plus the per-project ``daemon.pid`` lock.

The cockpit can submit and inspect while the daemon drains in the background.
Concurrent clients cannot
double-execute because :meth:`Backlog.claim_next` performs an atomic
CAS pending→running on the on-disk JSONL file.

This module is a thin facade: the actual lifecycle-phase implementations live
in sibling ``_life_worker_*`` modules (identity/vault preflight, runtime
context, boot phases, run phases, admission) so no single module here exceeds
the maintainability line-count target. Every name previously importable from
this module (public or private) remains importable from here via explicit
re-export.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess  # noqa: F401 — re-exported: tests patch life_worker.subprocess.run
import sys  # noqa: F401 — re-exported: tests read life_worker.sys.executable
import threading
import time  # noqa: F401 — re-exported: tests patch life_worker.time.sleep
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from ..core.daemon_lock import (
    DaemonAlreadyRunning,
    acquire_global_daemon_lock,  # noqa: F401 — re-exported, monkeypatch seam
)
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..life.memory import BacklogItem
from ..life.supervisor import (
    LifeSupervisor,  # noqa: F401 — monkeypatch seam, see tests/daemon/test_life_worker.py
    global_daily_spend,
)

# -- re-exports: daemon admission / workspace / spawn ------------------------
from ._life_worker_admission import (  # noqa: F401 — re-exported, see __all__
    _acquire_daemon_lock_with_timeout,
    _acquire_daemon_spawn_lock,
    _acquire_daemon_workspace_lease,
    _active_daemon_count,
    _active_workspace_owner,
    _daemon_global_root,
    _max_active_daemons,
    _release_daemon_spawn_lock,
    _release_daemon_workspace_lease,
    _workspace_start_error,
    run_foreground,
    run_handoff_child,
    spawn_detached_daemon,
    spawn_detached_daemon_clean,
)

# -- lifecycle-phase mixins (boot phases, run phases) ------------------------
from ._life_worker_boot import (
    LifeWorkerBootMixin,
    _RunForeverState,  # noqa: F401 — re-exported, see __all__
)

# -- re-exports: manager-handoff identity + vault/backend preflight ---------
from ._life_worker_identity import (  # noqa: F401 — re-exported, see __all__
    _MANAGER_HANDOFF_IDENTITY_FILE,
    _apply_continuous_suppression,
    _daemon_objective_requires_stage_reset,
    _effective_runner_backend,
    _legacy_manager_handoff_identity,
    _manager_handoff_identity_matches,
    _manager_handoff_identity_path,
    _objective_sha256,
    _preflight_route_on_codex,
    _read_manager_handoff_identity,
    _rearm_operator_drain_for_resume,
    _resume_matches_manager_handoff,
    _worker_vault_preflight_routes,
    _write_manager_handoff_identity,
    required_codex_routes,
)
from ._life_worker_run import LifeWorkerRunMixin

# -- re-exports: runner namespace / runtime context / supervisor config -----
from ._life_worker_runtime_context import (  # noqa: F401 — re-exported, see __all__
    _build_supervisor_config,
    _DaemonSink,
    _runner_namespace,
    _worker_runtime_context,
)
from .config import LifeWorkerConfig
from .config import config_from_payload as _config_from_payload
from .config import config_payload as _config_payload
from .handoff import (
    _HANDOFF_CONFIG_ENV,
    _HANDOFF_LOG_ENV,
    _HANDOFF_READY_ENV,
    _HANDOFF_TOKEN_ENV,
    _spawn_handoff_candidate,
    _strip_git_config_injection,
    _truthy_env,
)
from .state import (
    ContinuousConfigState,
    DaemonStatus,
    _daemon_log_path,
    _daemon_pid_path,
    _daemon_status_path,
    _daemon_status_payload,
    _new_boot_id,
    _point_active_daemon_log,
    _process_alive,
    _redirect_std_to_log,
    continuous_mode_error,
    daemon_drain_requested,
    disable_continuous_config,
    read_continuous_config,
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
    stop_daemon,
    wait_for_daemon_status,
    write_continuous_config,
)
from .state import (
    format_budget_status as _format_budget_status,
)

log = logging.getLogger(__name__)


def format_budget_status(journal: Any, *, status: Any | None = None) -> str:
    """Compatibility wrapper preserving the historical monkeypatch seam."""
    return _format_budget_status(
        journal,
        status=status,
        global_spend_fn=global_daily_spend,
    )

__all__ = [
    "LifeWorkerConfig",
    "LifeWorker",
    "DaemonStatus",
    "ContinuousConfigState",
    "continuous_mode_error",
    "disable_continuous_config",
    "format_budget_status",
    "resolve_effective_budget",
    "read_daemon_status",
    "stop_daemon",
    "wait_for_daemon_status",
    "spawn_detached_daemon",
    "spawn_detached_daemon_clean",
    "run_handoff_child",
    "read_continuous_state",
    "read_continuous_config",
    "write_continuous_config",
    "_process_alive",
    "_redirect_std_to_log",
    "_daemon_log_path",
    "_daemon_pid_path",
    "_daemon_status_path",
    "_daemon_status_payload",
    "_new_boot_id",
    "_point_active_daemon_log",
    "_config_from_payload",
    "_config_payload",
    "_HANDOFF_CONFIG_ENV",
    "_HANDOFF_LOG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_acquire_daemon_lock_with_timeout",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "_truthy_env",
    "DaemonAlreadyRunning",
]


class LifeWorker(LifeWorkerBootMixin, LifeWorkerRunMixin):
    """The 7×24 background worker.

    Construct, then call :meth:`run_forever` from the daemon process.
    Stops cleanly on SIGTERM / SIGINT — the supervisor's tick is one
    mission so there is at most one outstanding ``running`` item when
    the signal lands; the next process startup will reap it via
    :meth:`Backlog.reap_orphans` and mark it ``failed``.
    """

    def __init__(self, config: LifeWorkerConfig) -> None:
        # The host-global daily cap is the only monetary budget.
        budget_global_root = (
            Path(config.global_root).expanduser()
            if config.global_root is not None
            else (
                config.life_dir.parent.parent
                if config.life_dir.parent.name == "projects"
                else config.life_dir
            )
        )
        from ..core.knobs import resolve_budget_caps

        caps = resolve_budget_caps(
            project_state_dir=config.life_dir,
            global_root=budget_global_root,
        )
        config.global_daily_cap_usd = caps.global_daily_cap_usd
        self.config = config
        self._stop = threading.Event()
        self._mission_stop = threading.Event()
        self._operator_stop_requested = False
        self._adopted_continuous_generation: int | None = None
        self._started_at: float | None = None
        self._missions_completed = 0
        self._curator: Any = None  # resident teammate-pool Curator (built in run_forever)

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:  # noqa: ANN401
            log.info("daemon: received signal %s, requesting stop", signum)
            self._operator_stop_requested = True
            self._stop.set()
            if not daemon_drain_requested(
                self.config.life_dir,
                pid=os.getpid(),
            ):
                self._mission_stop.set()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        # Belt-and-suspenders: ``spawn_detached_daemon`` already calls
        # ``setsid`` so SIGHUP from a closing controlling-TTY cannot
        # reach us, but we explicitly ignore SIGHUP anyway so an
        # external operator (or an over-eager process supervisor)
        # cannot accidentally bring the 7×24 worker down by sending
        # one. Operators stop the daemon with SIGTERM / ``--daemon-stop``.
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except (AttributeError, ValueError, OSError):
            # SIGHUP is POSIX-only; on Windows ``signal.SIGHUP`` is
            # missing. Ignoring is a no-op on Windows anyway.
            pass

    @staticmethod
    def _active_manager_objective(
        memory: Any,
        *,
        manager_owned_only: bool = False,
    ) -> str:
        """Return the highest-priority active Manager objective, if one exists.

        Bounded work lives in the backlog rather than ``continuous.json``.  The
        old bootstrap read only ``LifeWorkerConfig.continuous_objective``, so a
        real bounded Manager handoff was invisible and ``AGENTS.md`` silently
        fell back to the generic EMNLP demo objective.  Manager-created backlog
        items preserve the authoritative handoff in ``original_objective``;
        prefer that value and ignore the bootstrap task itself.
        """
        try:
            items = list(memory.backlog.all())
        except Exception:  # noqa: BLE001 — bootstrap remains best-effort
            log.exception("daemon: failed to inspect backlog for Manager objective")
            return ""
        active = [
            item
            for item in items
            if str(getattr(item, "status", "")) in {"pending", "running"}
            and str(getattr(item, "title", "")) != "bootstrap empty project root"
            and (
                not manager_owned_only
                or "manager" in {
                    str(tag).strip().lower()
                    for tag in (getattr(item, "tags", []) or [])
                }
            )
        ]
        active.sort(
            key=lambda item: (
                int(getattr(item, "priority", 100)),
                float(getattr(item, "ts", 0.0) or 0.0),
            )
        )
        for item in active:
            objective = str(
                getattr(item, "original_objective", "")
                or getattr(item, "objective", "")
            ).strip()
            if objective:
                return objective
        return ""

    @staticmethod
    def _manager_owned_goal_text(objective: str) -> str:
        """Render a bootstrap goal without freezing stale runtime authority."""
        snapshot = " ".join(str(objective or "").split())
        authority = (
            "The latest Manager-authored execution objective supplied with the "
            "current mission is authoritative. This generated bootstrap file is "
            "workflow guidance only: do not reinterpret, broaden, or replace that "
            "objective, and do not treat this bootstrap-time snapshot as newer "
            "than a later Manager instruction."
        )
        if snapshot:
            return f"{authority} Bootstrap-time Manager objective: {snapshot}"
        return (
            f"{authority} No Manager-authored objective was active at bootstrap; "
            "wait for the Manager objective instead of inventing a default paper, "
            "benchmark, theorem, or optimization target."
        )

    def _seed_project_agents_and_venv(
        self,
        project_root: Path,
        *,
        objective: str | None = None,
    ) -> None:
        """Seed ``AGENTS.md`` and a per-project ``.venv`` for a daemon-managed
        bootstrap. Existing AGENTS content is preserved except for the small
        Argus-managed runtime block; other artifacts remain create-once.

        The AGENTS contract is chosen by vertical: optimize-family verticals
        (kernelbench / speedrun / nanochat / nanogpt_speedrun) get the lean
        benchmark-optimization template (no paper pipeline, no venue), while
        everything else (research) gets the paper/auto-research template. The
        resolved vertical is also persisted into ``research/PIPELINE_STATE.json``
        so the supervisor starts the mission in the right stage instead of
        inheriting a stale paper stage.
        """
        from ..skills.builtins import builtin_skill_source_path
        from ..skills.vertical_select import (
            _persisted_vertical,
        )
        from ..tools.new_auto_research_project import (
            init_project_venv,
            load_template_text,
            refresh_agents_runtime_contract,
            render_agents_md,
        )

        manager_objective = (
            self.config.continuous_objective
            if objective is None
            else objective
        )
        manager_objective = str(manager_objective or "")
        rendered_objective = self._manager_owned_goal_text(manager_objective)
        # The Manager is the sole vertical authority. Before it persists a
        # decision, bootstrap deliberately has no vertical classification.
        vertical = _persisted_vertical(project_root)
        # The paper/auto-research contract is seeded ONLY on a POSITIVE research
        # signal — a research vertical the Manager has already confirmed, or an
        # operator-configured research profile (ARGUS_SKILL_RESEARCH_PROFILE = the
        # operator explicitly declaring a paper/research workspace). Everything
        # else — the optimize-family verticals AND a not-yet-decided fresh mission
        # — gets the lean benchmark-optimization contract. The harness must NOT
        # DEFAULT an undecided mission into "produce a paper": a paper is a
        # research judgment, so it is seeded only where a research need is
        # actually declared, never as the fallback.
        #
        # "research-kind" here MUST be the Manager's OWN classification
        # (``Manager._kind_for``), not a second, independently-maintained
        # literal check — a second copy is exactly what drifted stale before
        # (see GROUND_TRUTH.md CLASSIFY_BY_VERTICAL §e row 2: a literal
        # ``vertical == "research"`` test does not know that ``_kind_for``
        # already buckets ``"quant"`` into the ``"research"`` kind, so a
        # quant project silently got the lean contract instead of the
        # paper/report one it needs). Reuse the Manager import pattern already
        # used elsewhere in this same function (see the lazy
        # ``from ..manager import Manager`` a few hundred lines below, in the
        # daemon-start division path) so there is only ONE place that decides
        # what counts as "research-kind".
        from ..life.research_profile import load_research_profile
        from ..manager import Manager

        # ``_kind_for`` is typed ``vertical: str`` (no ``None``); guard
        # explicitly rather than relying on the incidental fact that passing
        # ``None`` today happens to fall through both membership checks to
        # "custom" with no exception — that is an implementation detail, not
        # a contractual guarantee of the callee's signature.
        kind = Manager._kind_for(vertical) if vertical is not None else "custom"
        is_research = kind == "research" or load_research_profile() is not None

        agents_path = project_root / "AGENTS.md"
        if not agents_path.exists():
            if not is_research:
                template_path = (
                    builtin_skill_source_path()
                    / "agent-md-optimize-project-template.md"
                )
                template_text = load_template_text(template_path)
                agents_md = render_agents_md(
                    template_text,
                    project_name=project_root.name,
                    objective=rendered_objective,
                    non_goals=(
                        "Do not produce a paper, venue submission, literature "
                        "review, or LaTeX draft. Do not fabricate, hand-edit, or "
                        "hard-code the benchmark metric, and do not weaken or "
                        "bypass the correctness check to inflate the score."
                    ),
                    compute_budget=(
                        "Run the real benchmark/harness on the allocated real "
                        "hardware; every reported metric must be an actual "
                        "measurement from an actual run. Stop and report honestly "
                        "if the benchmark, GPU, or a required dependency is "
                        "unavailable rather than fabricating a score."
                    ),
                    append_harness_map=False,
                )
            else:
                template_text = load_template_text(None)
                agents_md = render_agents_md(
                    template_text,
                    project_name=project_root.name,
                    objective=rendered_objective,
                )
            agents_path.write_text(agents_md, encoding="utf-8")
        refresh_agents_runtime_contract(
            project_root,
            objective=manager_objective,
            vertical=vertical or "",
        )

        if not (project_root / ".venv").exists():
            init_project_venv(project_root)

        # Seed the project workspace's read-only builtin-skill copy
        # (``argus_builtin_skills/``) — vertical-aware, so a non-research mission
        # gets the active vertical's OWN domain skills (real bodies overwrite any
        # builtin pointer stub) alongside the cross-vertical skills. This is the
        # tree the reviewer agent reads when ``stage_check`` prints
        # ``Load skill: argus_builtin_skills/<role>/<name>.md``. Seeding is
        # is additive for common skills and refreshes the active vertical's
        # version-controlled read-only source, so newly shipped vertical skills
        # reach existing projects without replacing unrelated local files.
        from ..skills.builtins import (
            DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
            remove_unmodified_inactive_vertical_skill_seeds,
            seed_builtin_skills,
            seed_builtin_skills_for_vertical,
            seed_vertical_skills,
        )

        skills_target = project_root / DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
        try:
            remove_unmodified_inactive_vertical_skill_seeds(
                skills_target,
                vertical,
            )
            if vertical:
                seed_builtin_skills_for_vertical(skills_target, vertical)
            else:
                seed_builtin_skills(skills_target)
        except Exception:  # noqa: BLE001 — best-effort, never break bootstrap
            log.exception("daemon: failed to seed builtin skills during bootstrap")
        if vertical and self.config.project_fingerprint:
            try:
                from ..skills.layered import shared_vertical_skills_dir

                default_shared_root = (
                    core_paths.shared_skills_root()
                    if self.config.global_root is None
                    else Path(self.config.global_root) / "skills"
                )
                shared_root = Path(os.environ.get(
                    "ARGUS_SKILL_SKILLS_DIR",
                    str(default_shared_root),
                ))
                project_skills = Path(self.config.life_dir) / "skills"
                remove_unmodified_inactive_vertical_skill_seeds(
                    project_skills,
                    None,
                )
                vertical_shared = shared_vertical_skills_dir(shared_root, vertical)
                if vertical_shared is None:
                    raise ValueError(f"invalid vertical Skill namespace: {vertical!r}")
                seed_vertical_skills(
                    vertical_shared,
                    vertical,
                    overwrite=False,
                    overwrite_unidentified=True,
                )
            except Exception:  # noqa: BLE001 — best-effort, never break bootstrap
                log.exception("daemon: failed to seed vertical runtime skills")

    def _refresh_existing_project_contract(self, memory: Any) -> None:
        """Refresh framework-owned project surfaces on every daemon boot."""
        project_root = self.config.project_workdir
        if project_root is None or not (project_root / "AGENTS.md").is_file():
            return
        manager_objective = str(
            self.config.continuous_objective or ""
        ).strip()
        if not manager_objective:
            manager_objective = self._active_manager_objective(
                memory,
                manager_owned_only=True,
            )
        self._seed_project_agents_and_venv(
            project_root,
            objective=manager_objective,
        )

    def _seed_bootstrap_task(
        self,
        memory: Any,
        sink: Any,
        preflight: Any,
    ) -> bool:
        """Enqueue an explicitly requested research bootstrap once."""
        title = "bootstrap empty project root"
        try:
            existing = [
                item
                for item in memory.backlog.all()
                if str(getattr(item, "title", "")) == title
                and str(getattr(item, "status", "")) in {"pending", "running"}
            ]
        except Exception:  # noqa: BLE001
            log.exception("daemon: bootstrap preflight failed to inspect backlog")
            existing = []

        event = {
            "type": "life.project.bootstrap_required",
            "project_root": str(preflight.project_root),
            "missing_artifacts": list(preflight.missing_artifacts),
            "event_text": preflight.event_text,
            "objective": preflight.bootstrap_objective,
            "bootstrap_title": title,
            "queued": not existing,
        }
        try:
            sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("daemon: bootstrap preflight event sink failed")

        if existing:
            return False

        # Seed the reusable GPU/experiment scaffolds into ``code/`` so a
        # daemon-managed project bootstraps with the same starter helpers
        # the standalone launcher provides. overwrite=False never clobbers
        # files the engineer has already written on a re-bootstrap.
        try:
            from ..tools.new_auto_research_project import seed_starter_code
            seed_starter_code(Path(preflight.project_root), overwrite=False)
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to seed starter code during bootstrap")

        # Every daemon-managed project must also get an ``AGENTS.md`` (the
        # engineer prompt instructs the agent to read it — without it the agent
        # burns rounds on ``sed: can't read AGENTS.md``) and a per-project
        # ``.venv`` (so the agent pip-installs experiment deps into an overlay
        # rather than the framework venv). Both are no-ops when already present.
        try:
            manager_objective = self._active_manager_objective(memory)
            if not manager_objective:
                manager_objective = str(
                    self.config.continuous_objective or ""
                ).strip()
            self._seed_project_agents_and_venv(
                Path(preflight.project_root),
                objective=manager_objective,
            )
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to seed AGENTS.md / venv during bootstrap")

        try:
            item = BacklogItem.new(
                title=title,
                objective=preflight.bootstrap_objective,
                priority=0,
                tags=["bootstrap", "project"],
                notes=preflight.event_text,
                iterate=False,
                iteration_max_cycles=1,
            )
            memory.backlog.add(item)
            return True
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to enqueue bootstrap backlog item")
            return False

    # -- main loop ------------------------------------------------------

    def _build_curator(self, runner: Any = None) -> Any:
        """Construct the resident Curator that owns every team campaign's pool,
        or ``None`` when this daemon has no project workspace (no teams without
        one). Lazily imported to keep the daemon free of team deps until needed.
        """
        workdir = self.config.project_workdir
        if workdir is None:
            return None
        from ..team.curator import Curator
        return Curator(
            project_root=Path(workdir),
            default_width=int(os.environ.get("ARGUS_TEAM_DEFAULT_WIDTH", "8")),
            tick_s=float(os.environ.get("ARGUS_TEAM_CURATOR_TICK_S", "5")),
            teammate_timeout_s=float(os.environ.get("ARGUS_TEAMMATE_TIMEOUT_S", "5400")),
            hard_grace_s=float(os.environ.get("ARGUS_TEAMMATE_HARD_GRACE_S", "600")),
            distill_fn=self._curator_distill_fn(runner),
            distill_interval_s=float(os.environ.get("ARGUS_SKILL_CURATOR_DISTILL_INTERVAL_S", "1260")),
            completion_fn=self._team_completion_summary_fn(runner),
            conversation_root=self.config.life_dir,
        )

    def _curator_distill_fn(self, runner: Any) -> Any:
        """Wrap the curator agent backend into a ``(prompt) -> str`` distill
        function, or ``None`` when no runner/backend is available (the Curator
        then runs deterministic-only — fold the board, write no strategy)."""
        backend = getattr(runner, "curator_backend", None) or getattr(runner, "backend", None)
        if backend is None:
            return None
        from ..core.knobs import resolve_role_model

        model = resolve_role_model("curator", role_env="ARGUS_SKILL_CURATOR_MODEL")
        effort = os.environ.get("ARGUS_SKILL_CURATOR_REASONING_EFFORT", "high")
        workdir = str(self.config.project_workdir) if self.config.project_workdir else None

        def _distill(prompt: str) -> str:
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(model=model or None, reasoning_effort=effort,
                                      skip_git_repo_check=True, full_auto=True,
                                      working_dir=workdir),
                run_label="curator.distill",
            )
            return getattr(result, "last_agent_message", "") or ""

        return _distill

    def _team_completion_summary_fn(self, runner: Any) -> Any:
        """Use the Manager backend for one concise Team completion chat summary."""
        backend = getattr(runner, "manager_backend", None) or getattr(runner, "backend", None)
        if backend is None:
            return None
        from ..core.knobs import resolve_role_model

        model = resolve_role_model("manager", role_env="ARGUS_SKILL_MODEL")
        workdir = str(self.config.project_workdir) if self.config.project_workdir else None

        def _summarize(prompt: str) -> str:
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=model or None,
                    reasoning_effort="low",
                    skip_git_repo_check=True,
                    full_auto=True,
                    working_dir=workdir,
                ),
                run_label="manager.team_summary",
            )
            return getattr(result, "last_agent_message", "") or ""

        return _summarize
