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
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - detached daemon is POSIX-only
    _fcntl = None

_MANAGER_HANDOFF_IDENTITY_FILE = "manager-handoff.json"


def _manager_handoff_identity_path(runtime_root: Path) -> Path:
    return runtime_root / _MANAGER_HANDOFF_IDENTITY_FILE


def _objective_sha256(objective: str) -> str:
    return hashlib.sha256(str(objective).strip().encode("utf-8")).hexdigest()


def _write_manager_handoff_identity(
    runtime_root: Path,
    *,
    objective: str,
    vertical: str,
    continuous_generation: int,
    intent_id: str,
) -> bool:
    path = _manager_handoff_identity_path(runtime_root)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    payload = {
        "version": 1,
        "objective_sha256": _objective_sha256(objective),
        "vertical": str(vertical).strip(),
        "continuous_generation": max(0, int(continuous_generation)),
        "intent_id": str(intent_id),
        "recorded_at": time.time(),
    }
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except OSError:
        log.exception("failed to persist Manager handoff identity: %s", path)
        return False
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_manager_handoff_identity(runtime_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            _manager_handoff_identity_path(runtime_root).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    return payload


def _legacy_manager_handoff_identity(
    runtime_root: Path,
    *,
    objective: str,
    vertical: str,
) -> dict[str, Any] | None:
    """Recover one pre-sidecar Manager handoff from the immutable event tape."""
    handles: list[tuple[float, Any]] = []
    lock_path = runtime_root / "events.lock"
    lock_handle = lock_path.open("a+b")
    try:
        if _fcntl is not None:
            _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_SH)
        for path in runtime_root.glob("events.jsonl*"):
            try:
                handle = path.open("rb")
                metadata = os.fstat(handle.fileno())
            except OSError:
                continue
            handles.append((float(metadata.st_mtime), handle))
    finally:
        if _fcntl is not None:
            _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_UN)
        lock_handle.close()

    def _reverse_lines(handle: Any) -> Iterable[bytes]:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(64 * 1024, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]
            yield from reversed(parts[1:])
        if remainder:
            yield remainder

    try:
        for _mtime, handle in sorted(handles, key=lambda row: row[0], reverse=True):
            lines = _reverse_lines(handle)
            for raw_bytes in lines:
                raw = raw_bytes.decode("utf-8", errors="replace")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "life.manager.intent.completed":
                    continue
                if str(event.get("execution_task") or "").strip() != objective.strip():
                    continue
                if str(event.get("vertical") or "").strip() != vertical.strip():
                    continue
                return {
                    "version": 1,
                    "objective_sha256": _objective_sha256(objective),
                    "vertical": vertical,
                    "continuous_generation": max(
                        0,
                        int(event.get("continuous_generation") or 0),
                    ),
                    "intent_id": str(event.get("intent_id") or "legacy-event"),
                }
        return None
    finally:
        for _mtime, handle in handles:
            if not handle.closed:
                handle.close()


def _manager_handoff_identity_matches(
    identity: dict[str, Any] | None,
    *,
    objective: str,
    vertical: str,
    generation: int,
) -> bool:
    if identity is None:
        return False
    return (
        identity.get("objective_sha256") == _objective_sha256(objective)
        and str(identity.get("vertical") or "") == vertical
        and int(identity.get("continuous_generation") or 0) <= generation
    )


def _resume_matches_manager_handoff(
    *,
    cfg: LifeWorkerConfig,
    runtime_root: Path,
    state: ContinuousConfigState,
    objective: str,
) -> bool:
    if not getattr(cfg, "resume_continuous", False) or not state.enabled:
        return False
    if getattr(cfg, "continuous", False):
        return False
    from ..skills.vertical_select import _persisted_vertical

    vertical = _persisted_vertical(cfg.project_workdir or runtime_root)
    if not vertical:
        return False
    identity = _read_manager_handoff_identity(runtime_root)
    if not _manager_handoff_identity_matches(
        identity,
        objective=objective,
        vertical=vertical,
        generation=state.generation,
    ):
        identity = _legacy_manager_handoff_identity(
            runtime_root,
            objective=objective,
            vertical=vertical,
        )
        if identity is not None:
            _write_manager_handoff_identity(
                runtime_root,
                objective=objective,
                vertical=vertical,
                continuous_generation=int(
                    identity.get("continuous_generation") or 0
                ),
                intent_id=str(identity.get("intent_id") or "legacy-event"),
            )
    return _manager_handoff_identity_matches(
        identity,
        objective=objective,
        vertical=vertical,
        generation=state.generation,
    )

from ..core import paths as core_paths
from ..core.bootstrap import (
    inspect_project_bootstrap,
    structured_research_bootstrap_requested,
)
from ..core.daemon_lock import DaemonAlreadyRunning, acquire_global_daemon_lock
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..life.memory import BacklogItem, GlobalMemory, LifeMemory, MemoryBundle, ProjectMemory
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    global_daily_spend,
)
from .config import LifeWorkerConfig
from .config import config_from_payload as _config_from_payload
from .config import config_payload as _config_payload
from .handoff import (
    _HANDOFF_CONFIG_ENV,
    _HANDOFF_GEN_ENV,
    _HANDOFF_LOG_ENV,
    _HANDOFF_READY_ENV,
    _HANDOFF_TOKEN_ENV,
    _SOURCE_SIGNATURE_ENV,
    _TEST_SOURCE_SIGNATURE_FILE_ENV,
    _auto_handoff_enabled,
    _handoff_generation,
    _handoff_max_generations,
    _handoff_min_interval_seconds,
    _source_signature,
    _spawn_handoff_candidate,
    _strip_git_config_injection,
    _truthy_env,
    run_handoff_child_process,
)
from .handoff import (
    _acquire_daemon_lock_with_timeout as _acquire_daemon_lock_with_timeout_impl,
)
from .process import run_foreground_process, spawn_detached_process
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
    clear_daemon_drain_request,
    compare_and_swap_continuous_config,
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
    "_HANDOFF_GEN_ENV",
    "_HANDOFF_LOG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_SOURCE_SIGNATURE_ENV",
    "_TEST_SOURCE_SIGNATURE_FILE_ENV",
    "_acquire_daemon_lock_with_timeout",
    "_auto_handoff_enabled",
    "_handoff_generation",
    "_handoff_max_generations",
    "_handoff_min_interval_seconds",
    "_source_signature",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "_truthy_env",
    "DaemonAlreadyRunning",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Disk-based continuous config (hot-reloadable by daemon + cockpit)
# ---------------------------------------------------------------------------


def _preflight_route_on_codex(route: str) -> bool:
    """Will this preflight route actually run on the codex/Azure backend?

    EN: Uses the SAME canonical resolution as the role runners
    (``core.knobs.resolve_role_backend``: ``ARGUS_SKILL_{ROLE}_BACKEND`` →
    ``ARGUS_SKILL_RUNNER_BACKEND`` → ``ARGUS_SKILL_LIFE_BACKEND`` → persisted
    knob store → codex). A role pinned to copilot/claude authenticates through
    its OWN CLI (the copilot subscription / claude), NOT the ``model_api`` vault
    — so probing its Azure route is a FALSE gate. Reading the resolver (not raw
    ``os.environ``) is load-bearing: a non-interactive launcher (the web
    autostart, a bare ``tmux`` exec) never sources the operator's ``.bashrc``,
    so a copilot choice that lives only in an interactive-shell export would be
    invisible here and the daemon would wrongly probe — and fail on — the codex
    vault. The persisted ``/backend`` switch is honoured for exactly this case.
    Unknown/typo'd values fall back to codex so the safety probe is preserved.
    中文：与角色 runner 用同一套规范解析（``resolve_role_backend``：角色 env →
    RUNNER_BACKEND → LIFE_BACKEND → 持久化 knob → codex）。读解析后的后端而非裸
    ``os.environ`` 是关键：web/tmux 这类非交互启动器不 source ``.bashrc``，只写在
    交互 shell 里的 copilot 选择在这里就看不见，daemon 会误探并崩在 codex 金库上；
    持久化的 ``/backend`` 切换正是为这种情况兜底。未知值回退 codex 保留安全探测。
    """
    from ..agent_cli.runner_backend import BACKEND_CODEX, normalize_runner_backend
    from ..core.knobs import resolve_role_backend

    role = route if route in ("engineer", "reviewer", "planner", "manager", "curator") else ""
    chosen = resolve_role_backend(role)
    if not chosen:
        return True  # nothing resolved → codex default → probe it
    try:
        return normalize_runner_backend(chosen) == BACKEND_CODEX
    except Exception:  # noqa: BLE001 — unknown value: keep the safety probe
        return True


def required_codex_routes(required: Iterable[str] | None = None) -> list[str]:
    """The subset of preflight routes that will hit the codex/Azure model_api.

    Roles routed to copilot/claude are excluded (they never touch the Azure
    vault). When this returns ``[]`` the daemon can skip the vault preflight
    entirely — e.g. a fully copilot-backed run needs no Azure routes at all.
    返回真正会打到 codex/Azure model_api 的预检路由子集；copilot/claude 的角色被排除。
    返回 ``[]`` 时可整体跳过 vault 预检（如全 copilot 运行无需任何 Azure 路由）。
    """
    from ..core.vault_preflight import DEFAULT_REQUIRED_ROUTES

    routes = list(required) if required is not None else list(DEFAULT_REQUIRED_ROUTES)
    return [r for r in routes if _preflight_route_on_codex(r)]


def _worker_vault_preflight_routes(worker_backend: str) -> list[str]:
    """Return Codex routes to probe for this worker; memory never uses providers."""
    if str(worker_backend or "").strip().lower() == "memory":
        return []
    return required_codex_routes()


def _apply_continuous_suppression(
    state: dict,
    enabled: bool,
    objective: str,
    *,
    generation: int | None = None,
) -> tuple[bool, str]:
    """Gate a persisted continuous read against a fresh-daemon suppression.

    A generation-aware caller lifts suppression on every explicit rewrite,
    including re-arming the same objective. ``generation=None`` retains the
    legacy value-based behavior for compatibility callers.
    """
    if state.get("active"):
        same_generation = (
            generation == state.get("generation")
            if generation is not None
            else (objective or "").strip() == state.get("objective", "")
        )
        if enabled and same_generation:
            return False, objective
        state["active"] = False
    return enabled, objective


class LifeWorker:
    """The 7×24 background worker.

    Construct, then call :meth:`run_forever` from the daemon process.
    Stops cleanly on SIGTERM / SIGINT — the supervisor's tick is one
    mission so there is at most one outstanding ``running`` item when
    the signal lands; the next process startup will reap it via
    :meth:`Backlog.reap_orphans` and mark it ``failed``.
    """

    def __init__(self, config: LifeWorkerConfig) -> None:
        # budget.json is authoritative even when a handoff payload carries stale
        # in-memory caps from the previous process.
        from ..core.project_budget import (
            GlobalBudget,
            ProjectBudget,
            budget_path,
            global_budget_path,
            read_project_budget,
            write_global_budget,
            write_project_budget,
        )

        if budget_path(config.life_dir).exists():
            read_project_budget(config.life_dir)
        else:
            write_project_budget(
                config.life_dir,
                ProjectBudget(
                    per_mission_cap_usd=config.per_mission_cap_usd,
                    daily_cap_usd=config.daily_cap_usd,
                ),
            )
        budget_global_root = (
            Path(config.global_root).expanduser()
            if config.global_root is not None
            else (
                config.life_dir.parent.parent
                if config.life_dir.parent.name == "projects"
                else config.life_dir
            )
        )
        if not global_budget_path(budget_global_root).exists():
            write_global_budget(
                budget_global_root,
                GlobalBudget(config.global_daily_cap_usd),
            )
        from ..core.knobs import resolve_budget_caps

        caps = resolve_budget_caps(
            project_state_dir=config.life_dir,
            global_root=budget_global_root,
        )
        config.per_mission_cap_usd = caps.per_mission_cap_usd
        config.daily_cap_usd = caps.daily_cap_usd
        config.global_daily_cap_usd = caps.global_daily_cap_usd
        self.config = config
        self._stop = threading.Event()
        self._mission_stop = threading.Event()
        self._operator_stop_requested = False
        self._adopted_continuous_generation: int | None = None
        self._started_at: float | None = None
        self._missions_completed = 0
        self._source_signature = (
            os.environ.get(_SOURCE_SIGNATURE_ENV)
            or (_source_signature() if _auto_handoff_enabled() else "")
        )
        self._failed_handoff_signature = ""
        self._last_handoff_attempt_at = 0.0
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

    def _seed_project_agents_and_venv(self, project_root: Path) -> None:
        """Seed ``AGENTS.md`` and a per-project ``.venv`` for a continuous-mode
        bootstrap, matching the standalone launcher. Idempotent: skips each
        artifact that already exists so a re-bootstrap never clobbers operator
        or engineer edits.

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
            render_agents_md,
        )

        objective = (self.config.continuous_objective or "").strip()
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
            objective_arg = objective or None
            if not is_research:
                template_path = (
                    builtin_skill_source_path()
                    / "agent-md-optimize-project-template.md"
                )
                template_text = load_template_text(template_path)
                agents_md = render_agents_md(
                    template_text,
                    project_name=project_root.name,
                    objective=objective_arg,
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
                    objective=objective_arg,
                )
            agents_path.write_text(agents_md, encoding="utf-8")

        if not (project_root / ".venv").exists():
            init_project_venv(project_root)

        # Seed the project workspace's read-only builtin-skill copy
        # (``argus_builtin_skills/``) — vertical-aware, so a non-research mission
        # gets the active vertical's OWN domain skills (real bodies overwrite any
        # builtin pointer stub) alongside the cross-vertical skills. This is the
        # tree the reviewer agent reads when ``stage_check`` prints
        # ``Load skill: argus_builtin_skills/<role>/<name>.md``. Idempotent:
        # only seeded when the dir is absent, so a re-bootstrap never clobbers
        # operator/engineer edits.
        from ..skills.builtins import (
            DEFAULT_PROJECT_BUILTIN_SKILLS_DIR,
            seed_builtin_skills,
            seed_builtin_skills_for_vertical,
        )

        skills_target = project_root / DEFAULT_PROJECT_BUILTIN_SKILLS_DIR
        if not skills_target.exists():
            try:
                if vertical and vertical != "research":
                    seed_builtin_skills_for_vertical(skills_target, vertical)
                else:
                    seed_builtin_skills(skills_target)
            except Exception:  # noqa: BLE001 — best-effort, never break bootstrap
                log.exception("daemon: failed to seed builtin skills during bootstrap")

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
        # continuous-mode project bootstraps with the same starter helpers
        # the standalone launcher provides. overwrite=False never clobbers
        # files the engineer has already written on a re-bootstrap.
        try:
            from ..tools.new_auto_research_project import seed_starter_code
            seed_starter_code(Path(preflight.project_root), overwrite=False)
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to seed starter code during bootstrap")

        # Every continuous-mode project must also get an ``AGENTS.md`` (the
        # engineer prompt instructs the agent to read it — without it the agent
        # burns rounds on ``sed: can't read AGENTS.md``) and a per-project
        # ``.venv`` (so the agent pip-installs experiment deps into an overlay
        # rather than the framework venv). Both are no-ops when already present.
        try:
            self._seed_project_agents_and_venv(Path(preflight.project_root))
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to seed AGENTS.md / venv during bootstrap")

        try:
            item = BacklogItem.new(
                title=title,
                objective=preflight.bootstrap_objective,
                priority=0,
                max_cost_usd=5.0,
                tags=["bootstrap", "project"],
                notes=preflight.event_text,
                iterate=False,
                iteration_max_cycles=1,
                iteration_budget_usd=5.0,
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

    def run_forever(self) -> int:
        self._install_signal_handlers()
        self._started_at = time.time()

        # Ensure ARGUS_SKILL_PYTHON is set in the process environment so
        # all child processes can find the argus_skill package. Without this,
        # shells spawned by codex exec fall back to /usr/bin/python which cannot
        # import argus_skill.
        _argus_python = os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable
        os.environ.setdefault("ARGUS_SKILL_PYTHON", _argus_python)
        # Also prepend the venv bin dir to PATH so bare `python` resolves
        # to the venv interpreter in child shells.
        _venv_bin = str(Path(_argus_python).resolve().parent)
        _current_path = os.environ.get("PATH", "")
        if _venv_bin not in _current_path:
            os.environ["PATH"] = f"{_venv_bin}:{_current_path}"

        # Make the project's ``code/`` importable in every child shell so inline
        # scripts and ``code/*.py`` helpers can ``import benchmark_loaders`` /
        # ``import gpu_env`` without per-command ``PYTHONPATH=$PWD/code``
        # gymnastics — a recurring source of wasted engineer rounds. Appended
        # (not prepended) so it never shadows argus_skill or stdlib modules.
        if self.config.project_workdir is not None:
            _code_dir = str((self.config.project_workdir / "code").resolve())
            _pp_parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
            if _code_dir not in _pp_parts:
                _pp_parts.append(_code_dir)
                os.environ["PYTHONPATH"] = os.pathsep.join(_pp_parts)
            # Expose the project root so the in-process reviewer/planner (and the
            # engineer subprocess, which inherits this env) resolve the SAME root
            # for the per-project harness overlay (.argus/harness/). The daemon
            # itself runs at cwd=/, so bare Path.cwd() would be wrong here.
            os.environ["ARGUS_SKILL_PROJECT_ROOT"] = str(self.config.project_workdir.resolve())

        # Strip the env-based ``GIT_CONFIG_*`` config-injection family from the
        # env handed to child codex shells. The codex sandbox forwards an
        # incomplete tuple (drops ``GIT_CONFIG_KEY_0``) that breaks *every*
        # ``git`` command in the agent's shell. See _strip_git_config_injection.
        _strip_git_config_injection(os.environ)

        # Set CUDA_VISIBLE_DEVICES from GPU resource allocation
        from ..tools.capability_vault import gpu_env_vars
        for k, v in gpu_env_vars().items():
            os.environ[k] = v

        cfg = self.config
        split_memory = bool(cfg.global_root and cfg.project_fingerprint)
        mem: MemoryBundle | LifeMemory
        if split_memory:
            global_mem = GlobalMemory.open(cfg.global_root)
            project_mem = ProjectMemory.open(
                cfg.project_fingerprint,
                label=cfg.project_label or cfg.project_fingerprint,
                global_root=cfg.global_root,
            )
            mem = MemoryBundle(
                global_mem=global_mem,
                project=project_mem,
                project_worktree=cfg.project_workdir,
            )
            runtime_root = mem.project.root
        else:
            mem = LifeMemory.open(cfg.life_dir)
            runtime_root = cfg.life_dir
        mem.init()
        if split_memory:
            os.environ["ARGUS_SKILL_SESSION_ID"] = cfg.project_fingerprint
        os.environ["ARGUS_SKILL_SESSION_ROOT"] = str(runtime_root)
        os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(runtime_root / "events.jsonl")

        # Build the runner through the shared runtime composition root. Importing here
        # keeps daemon.life_worker free of CLI-only deps until needed.
        from ..apps._runtime import LifeStderrSink, build_life_runner
        ns = _runner_namespace(cfg)
        ns.stop_event = self._mission_stop
        runner = build_life_runner(ns)

        # Continuous drain: each LifeSupervisor.run() drains until the
        # backlog goes empty or the budget caps. Then we sleep
        # poll_interval seconds and try again — items may have been
        # submitted from a coexisting cockpit.
        from ..life.event_log import JsonlEventSink

        # events.jsonl is the single persistent timeline.
        sink = JsonlEventSink(
            _DaemonSink(self),
            life_dir=runtime_root,
            verbosity=getattr(cfg, "event_log_verbosity", "signal"),
        )

        # Capture an empty-root RESEARCH bootstrap candidate before Manager.divide
        # writes PIPELINE_STATE, but do not enqueue it yet. After divide, only a
        # structured research signal (persisted research/quant vertical or an
        # explicit research profile) may activate it. Custom domains such as
        # composition own their workspace shape and never receive a Python
        # package bootstrap from the harness.
        bootstrap_preflight_pending = None
        if cfg.project_workdir is not None:
            bootstrap_preflight = inspect_project_bootstrap(
                cfg.project_workdir,
                objective_hint=cfg.continuous_objective,
                research_requested=True,
            )
            if bootstrap_preflight.should_bootstrap:
                bootstrap_preflight_pending = bootstrap_preflight

        # A fresh (non-resume) daemon must NOT adopt the project's persisted
        # continuous campaign — the operator manages daemons, and a daemon that
        # was not asked to resume has no business silently continuing a campaign
        # an earlier launch armed. Suppress a stale enabled-at-boot campaign
        # (leaving its on-disk state intact) unless this launch opted to resume
        # (--continuous / --resume-continuous) or the operator re-arms it live.
        resume_intent = bool(cfg.continuous or getattr(cfg, "resume_continuous", False))
        _boot = read_continuous_state(runtime_root)
        _suppress = {
            "active": bool(_boot.enabled) and not resume_intent,
            "objective": (_boot.objective or "").strip(),
            "generation": _boot.generation,
        }
        if _suppress["active"]:
            log.warning(
                "daemon: NOT resuming this project's persisted continuous campaign "
                "(objective=%r) — this launch did not opt in. Use --resume-continuous "
                "to auto-resume, or --continuous --objective to re-arm. Campaign "
                "state left intact.",
                _suppress["objective"][:80],
            )

        # Build a config provider that reads continuous.json from disk,
        # so the cockpit can enable/disable continuous mode while the daemon
        # is running — no daemon restart needed. A suppressed stale-boot
        # campaign stays off until the operator re-arms it (any change from the
        # boot state lifts the suppression and is then honored live).
        _latest_continuous_state = _boot

        def _continuous_provider() -> tuple[bool, str]:
            nonlocal _latest_continuous_state
            current = read_continuous_state(runtime_root)
            _latest_continuous_state = current
            enabled, objective = current.enabled, current.objective
            enabled, objective = _apply_continuous_suppression(
                _suppress,
                enabled,
                objective,
                generation=current.generation,
            )
            if continuous_mode_error(cfg.backend, enabled, objective):
                if enabled:
                    write_continuous_config(
                        runtime_root,
                        enabled=False,
                        objective=objective,
                    )
                return False, objective
            if not self._operator_stop_requested:
                self._adopted_continuous_generation = (
                    current.generation if enabled else None
                )
            return enabled, objective

        # Seed continuous config from disk (or CLI flags).
        init_continuous, init_objective = _continuous_provider()
        init_source_state = _latest_continuous_state
        if cfg.continuous:
            # CLI flags override disk. Persist only after Manager has produced a
            # role-clean execution handoff.
            init_continuous = True
            init_objective = cfg.continuous_objective or init_objective

        # ``resume_continuous`` adopts a campaign only when its objective and
        # vertical match a durable Manager handoff identity. This avoids a fresh
        # provider dependency on every crash recovery / upgrade without trusting
        # a torn or unrelated PIPELINE_STATE write.
        resume_has_manager_handoff = (
            init_continuous
            and _resume_matches_manager_handoff(
                cfg=cfg,
                runtime_root=runtime_root,
                state=init_source_state,
                objective=str(init_objective or ""),
            )
        )
        if resume_has_manager_handoff:
            log.info(
                "daemon boot: adopting persisted Manager handoff for "
                "continuous generation %d",
                init_source_state.generation,
            )

        # New daemon = fresh isolation generation: drop the Manager's persistent
        # codex session so it does NOT resume the PRIOR daemon's accumulated
        # conversation. Runs BEFORE the boot divide() so even boot classification
        # starts clean. Fail-open. / 新 daemon = 全新隔离代际：清掉 Manager 的常驻
        # codex 会话，不 resume 上一个 daemon 的累积对话；放在 boot divide() 之前，
        # 连启动分类也从干净会话开始；失败也不阻塞启动。
        try:
            from ..manager import reset_manager_session as _reset_mgr_session

            _mgr_session_root = runtime_root
            if _mgr_session_root and _reset_mgr_session(_mgr_session_root):
                log.info(
                    "daemon boot: cleared prior Manager codex session at %s",
                    _mgr_session_root,
                )
        except Exception:  # noqa: BLE001 — never block daemon start on session reset
            pass
        # Manager divides the task before the supervisor starts — same as the
        # foreground path (apps/_runtime.run_life_supervisor): classify the vertical,
        # split into Stages, and commit it so the supervisor trusts the persisted
        # vertical. A missing handoff fails closed: raw operator text never reaches
        # Planner/Engineer.
        if (
            init_continuous
            and str(init_objective or "").strip()
            and not _suppress["active"]
            and not resume_has_manager_handoff
        ):
            source_objective = str(init_objective).strip()
            expected_state = init_source_state
            intent_id = f"intent-daemon-{time.time_ns()}"
            sink.append({
                "type": "life.manager.intent.started",
                "agent_layer": "manager",
                "intent_id": intent_id,
                "source": "daemon_boot",
                "objective": source_objective,
                "text": "manager interpreting daemon objective",
            })
            try:
                # Prefer the runner's single Manager instance (manager backend);
                # fall back to an ad-hoc Manager only when the runner has none
                # (e.g. the memory runner in tests).
                mgr = getattr(runner, "manager", None)
                if mgr is None:
                    from ..manager import Manager

                    mgr = Manager(
                        project_root=cfg.project_workdir or runtime_root,
                        runner=getattr(runner, "manager_backend", None)
                        or getattr(runner, "backend", None),
                        manager_session_root=runtime_root,
                    )
                from ..manager.front_door import require_manager_execution_task

                decision = mgr.decide_vertical(source_objective)
                execution_task = require_manager_execution_task(decision)
                if self._operator_stop_requested:
                    raise RuntimeError("operator stop requested during Manager handoff")
                target_enabled = True if cfg.continuous else expected_state.enabled
                committed: dict[str, Any] = {}

                def _commit_decision() -> None:
                    committed["division"] = mgr.commit_vertical_decision(
                        source_objective,
                        decision,
                        ask_on_new_domain=False,
                        _lock_held=True,
                    )

                lock_factory = getattr(mgr, "pipeline_lock", None)
                pipeline_lock = (
                    lock_factory() if callable(lock_factory) else nullcontext()
                )
                with pipeline_lock:
                    swapped = compare_and_swap_continuous_config(
                        runtime_root,
                        expected=expected_state,
                        enabled=target_enabled,
                        objective=execution_task,
                        before_write=_commit_decision,
                    )
                if swapped:
                    division = committed["division"]
                    init_continuous = target_enabled
                    init_objective = execution_task
                    if not self._operator_stop_requested:
                        self._adopted_continuous_generation = (
                            expected_state.generation + 1
                            if target_enabled
                            else None
                        )
                    sink.append({
                        "type": "life.manager.intent.completed",
                        "agent_layer": "manager",
                        "intent_id": intent_id,
                        "source": "daemon_boot",
                        "continuous_generation": expected_state.generation + 1,
                        "execution_task": execution_task,
                        "vertical": getattr(division, "vertical", ""),
                        "kind": getattr(division, "kind", ""),
                        "stages": list(getattr(division, "stages", []) or []),
                        "text": "manager completed daemon objective handoff",
                    })
                    _write_manager_handoff_identity(
                        runtime_root,
                        objective=execution_task,
                        vertical=str(getattr(division, "vertical", "") or ""),
                        continuous_generation=expected_state.generation + 1,
                        intent_id=intent_id,
                    )
                else:
                    if (
                        read_continuous_state(runtime_root).generation
                        == expected_state.generation
                    ):
                        init_continuous, init_objective = False, ""
                        if expected_state.enabled:
                            _suppress.update({
                                "active": True,
                                "objective": expected_state.objective,
                                "generation": expected_state.generation,
                            })
                        sink.append({
                            "type": "life.manager.intent.failed",
                            "agent_layer": "manager",
                            "intent_id": intent_id,
                            "source": "daemon_boot",
                            "error": "failed to persist Manager execution handoff",
                            "text": "manager daemon objective handoff was not persisted",
                        })
                    else:
                        init_continuous, init_objective = _continuous_provider()
                        sink.append({
                            "type": "life.manager.intent.superseded",
                            "agent_layer": "manager",
                            "intent_id": intent_id,
                            "source": "daemon_boot",
                            "text": "daemon objective changed during Manager handoff",
                        })
                cfg.continuous_objective = init_objective
            except Exception as exc:  # noqa: BLE001 — fail closed, keep daemon available
                current_state = read_continuous_state(runtime_root)
                if current_state.generation == expected_state.generation:
                    init_continuous = False
                    init_objective = current_state.objective
                    if current_state.enabled:
                        _suppress.update({
                            "active": True,
                            "objective": current_state.objective,
                            "generation": current_state.generation,
                        })
                else:
                    init_continuous, init_objective = _continuous_provider()
                cfg.continuous_objective = init_objective
                log.error("daemon Manager handoff failed; objective not dispatched: %s", exc)
                sink.append({
                    "type": "life.manager.intent.failed",
                    "agent_layer": "manager",
                    "intent_id": intent_id,
                    "source": "daemon_boot",
                    "objective": source_objective,
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": "manager daemon objective handoff failed",
                })

        # Now that the Manager's divide() above has had its chance to resolve
        # and persist the real vertical, perform the previously-deferred
        # bootstrap seed. ``_seed_project_agents_and_venv`` re-reads the
        # persisted vertical itself, so this ordering is what actually closes
        # the race — no vertical is threaded through by hand here.
        if (
            bootstrap_preflight_pending is not None
            and structured_research_bootstrap_requested(
                Path(bootstrap_preflight_pending.project_root)
            )
        ):
            self._seed_bootstrap_task(mem, sink, bootstrap_preflight_pending)

        # Build supervisor policy only AFTER Manager.divide() has persisted the
        # vertical.  Mission typing is fail-safe (non-paper until a
        # ``full_paper`` vertical is positively resolved), so constructing this
        # before divide would incorrectly leave a brand-new paper campaign in
        # bounded mode for its whole daemon lifetime.
        sup_cfg = _build_supervisor_config(
            cfg,
            runtime_root=runtime_root,
            stop_event=self._stop,
            init_continuous=init_continuous,
            init_objective=init_objective,
            continuous_provider=_continuous_provider,
            planner_runtime_context_provider=self._planner_runtime_context,
            planner_restart_handler=self._planner_restart_handler,
            post_mission_hook=self._post_mission_hook,
        )

        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=sup_cfg,
            engineer_model=cfg.engineer_model,
            reviewer_model=cfg.reviewer_model,
            planner_runner=getattr(runner, "planner_backend", None)
            or getattr(runner, "backend", None),
        )

        # Vault pre-flight: refuse to start daemon if a required
        # model_api route is misconfigured (e.g. wrong deployment
        # name → 404 on every mission, observed 2026-06-01: 47 min /
        # $2.50 doom loop). Only routes that actually run on the codex/Azure
        # backend are probed — roles pinned to copilot/claude authenticate via
        # their own CLI (copilot subscription / claude), not the model_api vault,
        # so a fully copilot-backed run needs NO Azure routes and skips this.
        # memory backend (tests) skips. Override: ARGUS_SKILL_SKIP_VAULT_PREFLIGHT=1.
        # 只探测真正跑在 codex/Azure 后端的路由；固定到 copilot/claude 的角色用自己的
        # CLI 认证，不走 model_api vault，故全 copilot 运行无需 Azure 路由、直接跳过。
        if os.environ.get(
            "ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", ""
        ).strip() not in ("1", "true", "yes"):
            codex_routes = _worker_vault_preflight_routes(cfg.backend)
            if not codex_routes:
                log.info(
                    "vault preflight skipped: no required route runs on the codex "
                    "backend (roles on copilot/claude authenticate via their own CLI)"
                )
            else:
                from ..core.vault_preflight import (
                    check_routes as _vault_preflight_check,
                )
                from ..core.vault_preflight import (
                    format_report as _vault_preflight_format,
                )
                try:
                    preflight = _vault_preflight_check(required=codex_routes)
                except Exception as exc:  # noqa: BLE001 - never fail-start on preflight infra bug
                    log.warning("vault preflight infra failed; proceeding: %s", exc)
                    preflight = None
                if preflight is not None and not preflight.ok:
                    sys.stderr.write(_vault_preflight_format(preflight) + "\n")
                    sys.stderr.write(
                        "argus-skill: daemon refused to start due to vault preflight "
                        "failure. Fix the routes above, or set "
                        "ARGUS_SKILL_SKIP_VAULT_PREFLIGHT=1 to bypass.\n"
                    )
                    return 2

        log.info(
            "daemon: ready (life_dir=%s backend=%s pid=%d)",
            runtime_root, cfg.backend, os.getpid(),
        )
        # Use the LifeStderrSink shape only inside ``run`` if verbose
        # debug ever needed; default sink emits to log.
        del LifeStderrSink

        # Start the Telegram inbound command poller only when explicitly enabled.
        try:
            from ..life.telegram_bot import telegram_enabled
            if telegram_enabled():
                from ..life.telegram_bot import TelegramPoller
                tg_poller = TelegramPoller(
                    life_dir=runtime_root, stop_event=self._stop,
                )
                tg_poller.start()
            else:
                log.info("telegram poller disabled")
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to start telegram poller; continuing")

        # Start the resident Curator: it keeps each active team campaign's pool
        # in flight and is the single reaper (the lead drops .argus/team campaign
        # markers under project_workdir, which the Curator watches). Stopped in
        # the finally below so a clean shutdown reaps every teammate it owns.
        self._curator = self._build_curator(runner)
        if self._curator is not None:
            self._curator.start()

        try:
            while not self._stop.is_set():
                summary: dict = {}
                try:
                    from ..manager._core import manager_pipeline_yield_requested

                    if manager_pipeline_yield_requested(runtime_root):
                        self._stop.wait(0.2)
                        continue
                    manager = getattr(runner, "manager", None)
                    lock_factory = getattr(manager, "pipeline_lock", None)
                    pipeline_lock = (
                        lock_factory() if callable(lock_factory) else nullcontext()
                    )
                    with pipeline_lock:
                        summary = sup.run()
                    test_signature_path = os.environ.get(
                        _TEST_SOURCE_SIGNATURE_FILE_ENV, ""
                    ).strip()
                    if test_signature_path and self._source_signature:
                        current_signature = _source_signature()
                        if current_signature and current_signature != self._source_signature:
                            self._maybe_handoff_after_source_change(
                                planner_reason=(
                                    "test-controlled source signature changed"
                                )
                            )
                    # When planner declares project done, persist to disk
                    # so we don't re-plan the same objective next loop.
                    if summary.get("stopped_by") == "project_done":
                        current = read_continuous_state(runtime_root)
                        if (
                            current.enabled
                            and self._adopted_continuous_generation is not None
                            and current.generation
                            == self._adopted_continuous_generation
                            and compare_and_swap_continuous_config(
                                runtime_root,
                                expected=current,
                                enabled=False,
                                objective=current.objective,
                                done_reason="planner declared project done",
                            )
                        ):
                            self._adopted_continuous_generation = None
                    # Idle auto-exit: the supervisor judged the project idle past
                    # the cap. Exit the loop so the process shuts down cleanly
                    # (the shutdown distillation below runs) — the session model
                    # respawns this daemon on the operator's next --resume.
                    if summary.get("stopped_by") == "idle_timeout":
                        log.info(
                            "daemon: idle-timeout reached; exiting cleanly "
                            "(resume to continue)"
                        )
                        break
                except Exception:  # noqa: BLE001
                    log.exception("daemon: drain pass raised; sleeping and retrying")
                # Reset per-run counters so future drain passes work.
                sup._missions_started = 0
                sup._planning_cycles = 0
                if self._stop.is_set():
                    break
                # Honor the supervisor's suggested backoff (escalating while it is
                # idle awaiting an external dependency). The sleep is wakeable: it returns
                # early on stop, or when the user inbox grows — so /add and /nudge
                # stay responsive even during a long await-external backoff.
                try:
                    suggested = float(summary.get("suggested_sleep") or 0.0)
                except Exception:  # noqa: BLE001
                    suggested = 0.0
                self._wakeable_sleep(
                    max(float(cfg.poll_interval), suggested),
                    cfg.poll_interval,
                    runtime_root,
                )
        finally:
            if self._curator is not None:
                self._curator.stop()
            clear_daemon_drain_request(
                self.config.life_dir,
                pid=os.getpid(),
            )

        # Operator clock-out (别干了): a graceful stop (SIGTERM/SIGINT set
        # self._stop — including a bare ``kill`` and ``--daemon-stop``) quiesces
        # continuous mode so the campaign does NOT silently resurrect on the next
        # daemon launch. A crash (SIGKILL / power loss) never reaches here, so
        # continuous stays enabled and the campaign auto-resumes — the intended
        # crash-recovery.
        if self._operator_stop_requested:
            self._quiesce_continuous_on_operator_stop(
                runtime_root,
                self._adopted_continuous_generation,
            )

        log.info(
            "daemon: stopping cleanly (uptime=%.1fs missions=%d)",
            time.time() - (self._started_at or time.time()),
            self._missions_completed,
        )
        self._distill_on_shutdown(sup)
        return 0

    def _quiesce_continuous_on_operator_stop(
        self,
        runtime_root: Path,
        adopted_generation: int | None,
    ) -> None:
        """Operator clock-out (别干了): disable continuous mode on a graceful stop.

        When the daemon is asked to stop (SIGTERM/SIGINT — including a bare
        ``kill`` and ``--daemon-stop``), it "clocks out": it flips
        ``continuous.json`` to ``enabled=false`` so the campaign stays stopped
        and does NOT silently resurrect when a fresh daemon is later launched on
        this project. The objective is preserved so the operator can re-arm.

        A crash (SIGKILL / OOM / power loss) never runs this path, so continuous
        stays enabled and the campaign auto-resumes — the intended crash
        recovery. No-op for a non-continuous daemon. Best-effort: never blocks
        shutdown.
        """
        if adopted_generation is None:
            return
        try:
            current = read_continuous_state(runtime_root)
            if not current.enabled or current.generation != adopted_generation:
                return
            if not compare_and_swap_continuous_config(
                runtime_root,
                expected=current,
                enabled=False,
                objective=current.objective,
                done_reason="operator stop (graceful SIGTERM/SIGINT — clock out)",
            ):
                return
            log.info("daemon: quiesced continuous mode on operator stop (clock out)")
        except Exception:  # noqa: BLE001 — quiesce is best-effort
            log.exception("daemon: failed to quiesce continuous on operator stop")

    def _distill_on_shutdown(self, sup: Any) -> None:
        """Final skill-distillation pass when the daemon stops cleanly.

        This is where a daemon's accumulated lessons get promoted into the
        argus source tree on death — it reuses the existing Manager skill gate
        (``tidy_after_mission``), inventing no new judgement, and is fully
        fail-soft. Skipped for the ``memory`` backend: distillation needs a real
        LLM runner to classify placement, which the in-memory test/cheap backend
        does not provide (and it would otherwise reach the global skill store).
        """
        # Source-tree promotion is a deliberate operator action. It used to run
        # on every clean shutdown even when auto-commit was disabled, leaving
        # hundreds of untracked ``-2``/``-3`` skills which were seeded back on
        # the next boot. Default OFF; runtime skills remain safely persisted.
        if not _truthy_env("ARGUS_SKILL_PROMOTE_SKILLS_ON_SHUTDOWN", "0"):
            return
        if str(getattr(self.config, "backend", "") or "").lower() == "memory":
            return
        # Process-lesson distillation is retired. Reusable behavior should be
        # captured as explicit skill_ops or derived offline from events.jsonl,
        # not as an extra reviewer-written explanatory text stream.
        try:
            from ..manager.skill_tidy import tidy_after_mission

            counts = tidy_after_mission(
                sup._project_workdir(),
                sup.runner,
                project_state_dir=getattr(
                    getattr(sup, "memory", None), "project_root", None
                ),
            )
        except Exception:  # noqa: BLE001 — shutdown distillation is best-effort
            log.exception("daemon: shutdown distillation failed; non-critical")
            return
        promoted = (counts or {}).get("to_builtin", 0) + (counts or {}).get(
            "to_vertical", 0
        )
        if promoted:
            log.info(
                "daemon: shutdown distillation promoted %d skill(s) to source",
                promoted,
            )

    def _wakeable_sleep(
        self,
        total_seconds: float,
        poll_interval: float,
        runtime_root: Path,
    ) -> None:
        """Sleep up to ``total_seconds``, waking early on stop or new inbox input.

        The sleep is chunked into ``poll_interval`` slices so a stop request or
        a freshly ``/add``'d / ``/nudge``'d message (which appends to the
        project ``inbox.jsonl``) interrupts a long backoff promptly.
        """
        if total_seconds <= 0:
            return
        chunk = max(0.5, float(poll_interval))
        inbox = Path(runtime_root) / "inbox.jsonl"

        def _inbox_size() -> int:
            try:
                return inbox.stat().st_size
            except OSError:
                return 0

        baseline = _inbox_size()
        remaining = float(total_seconds)
        while remaining > 0 and not self._stop.is_set():
            self._stop.wait(timeout=min(chunk, remaining))
            if self._stop.is_set():
                return
            if _inbox_size() != baseline:
                return  # new user input — re-drain immediately
            remaining -= chunk

    def _planner_runtime_context(self) -> str:
        if not _auto_handoff_enabled() or not self._source_signature:
            return ""
        current = _source_signature()
        if not current or current == self._source_signature:
            return ""
        if current == self._failed_handoff_signature:
            return (
                "Runtime source changed since daemon start, but the latest "
                "blue/green handoff attempt for this signature failed. Set "
                "restart_daemon=false unless new evidence shows retrying is necessary."
            )
        return (
            "Runtime source changed since daemon start. A blue/green daemon "
            "handoff is available if and only if a fresh daemon process is "
            "needed to load or validate the new code. Set restart_daemon=true "
            "for daemon/CLI/lifecycle changes, substantial runtime refactors, "
            "or verification that requires the installed daemon to restart; "
            "otherwise set restart_daemon=false."
        )

    def _planner_restart_handler(self, reason: str) -> bool:
        return self._maybe_handoff_after_source_change(
            planner_reason=reason or "planner requested daemon restart",
        )

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        """Trigger blue/green reload after self-architecture changes.

        Engineers may legitimately modify daemon/reviewer/planner/tooling code
        while solving a research mission. The incumbent process cannot import
        those runtime changes, so check at every mission boundary and hand off
        to a fresh daemon as soon as the new process can stand by.
        """
        del outcome
        if self._maybe_handoff_after_source_change(
            planner_reason=(
                "runtime source changed after mission completion; "
                "blue/green reload needed for self-architecture update"
            )
        ):
            return "daemon_handoff"
        return ""

    def _maybe_handoff_after_source_change(self, *, planner_reason: str) -> bool:
        if not _auto_handoff_enabled() or not self._source_signature:
            return False
        current = _source_signature()
        if not current or current == self._source_signature:
            return False
        if current == self._failed_handoff_signature:
            return False
        min_interval = _handoff_min_interval_seconds()
        now = time.monotonic()
        if (
            self._last_handoff_attempt_at
            and now - self._last_handoff_attempt_at < min_interval
        ):
            return False
        self._last_handoff_attempt_at = now
        if _handoff_generation() >= _handoff_max_generations():
            log.warning("daemon handoff disabled: generation cap reached")
            return False
        if _spawn_handoff_candidate(
            self.config,
            source_signature=current,
            reason=planner_reason,
        ):
            log.info(
                "daemon handoff candidate ready; stopping incumbent (planner_reason=%s)",
                planner_reason,
            )
            self._stop.set()
            return True
        self._failed_handoff_signature = current
        log.warning("daemon handoff failed for signature=%s; incumbent continues", current)
        return False

def run_handoff_child() -> int:
    return run_handoff_child_process(
        worker_factory=LifeWorker,
        acquire_lock=_acquire_daemon_lock_with_timeout,
    )


def _acquire_daemon_lock_with_timeout(pid_path: Path, timeout: float) -> Any:
    return _acquire_daemon_lock_with_timeout_impl(
        pid_path,
        timeout,
        acquire_fn=acquire_global_daemon_lock,
    )


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
        core_paths.skills_global_root()
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
    # mission-abort request (see ``apps/_runtime.py:_SkillLoopRunner._stop_reason``
    # and ``tools.mission_control``) — the front-door quick-reply runner never
    # sets this, so the Manager's own SELF-turn can never abort itself.
    ns.enable_mission_abort_signal = True
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "500"))
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
    cfg: LifeWorkerConfig, *, paper_mission: bool | None = None,
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
    research_context = (
        render_research_profile_context() if paper_mission is not False else ""
    )
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
        f"- Budget: ${cfg.per_mission_cap_usd:.0f}/mission, ${cfg.daily_cap_usd:.0f}/day, "
        f"${cfg.global_daily_cap_usd:.0f}/day global\n"
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
    planner_runtime_context_provider: Any,
    planner_restart_handler: Any,
    post_mission_hook: Any,
) -> LifeSupervisorConfig:
    from ..apps._runtime import (
        _inbox_drainer_for,
        _paper_mission_for_project_root,
    )
    from ..life.telemetry import telemetry_interval_from_env

    paper_mission = _paper_mission_for_project_root(
        cfg.project_workdir or runtime_root
    )

    return LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=cfg.per_mission_cap_usd,
            daily_cap_usd=cfg.daily_cap_usd,
            global_daily_cap_usd=cfg.global_daily_cap_usd,
            max_missions=64,
        ),
        planner_task_iteration_max_cycles=cfg.planner_task_iteration_max_cycles,
        planner_task_iteration_budget_usd=cfg.planner_task_iteration_budget_usd,
        subagent_family_failure_streak_limit=cfg.subagent_family_failure_streak_limit,
        subagent_family_failure_window_hours=cfg.subagent_family_failure_window_hours,
        poll_interval_seconds=2.0,
        project_worktree=cfg.project_workdir,
        stop_event=stop_event,
        user_inbox=_inbox_drainer_for(runtime_root),
        runtime_context=_worker_runtime_context(cfg, paper_mission=paper_mission),
        continuous=init_continuous,
        continuous_objective=init_objective,
        open_ended=cfg.continuous_open_ended,
        paper_mission=paper_mission,
        full_paper_gate=paper_mission and cfg.continuous_open_ended,
        continuous_config_provider=continuous_provider,
        planner_runtime_context_provider=planner_runtime_context_provider,
        planner_restart_handler=planner_restart_handler,
        post_mission_hook=post_mission_hook,
        telemetry_dir=runtime_root,
        artifact_root=cfg.project_workdir or runtime_root,
        telemetry_interval_seconds=telemetry_interval_from_env(),
    )


class _DaemonSink:
    """Minimal sink: count mission completions and log daemon events."""

    def __init__(self, worker: LifeWorker) -> None:
        self._worker = worker

    def handle_event(self, event: dict[str, Any]) -> None:
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

def _max_active_daemons(config: LifeWorkerConfig) -> int:
    """Host-wide daemon cap; provider guards separately control call concurrency."""
    try:
        from ..core.knobs import DEFAULT_MAX_ACTIVE_DAEMONS, resolve_knob

        raw = resolve_knob(
            "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
            str(DEFAULT_MAX_ACTIVE_DAEMONS),
        )
        return max(0, int(raw.value))
    except Exception:  # noqa: BLE001
        from ..core.knobs import DEFAULT_MAX_ACTIVE_DAEMONS

        return DEFAULT_MAX_ACTIVE_DAEMONS


def _daemon_global_root(config: LifeWorkerConfig) -> Path:
    return (
        Path(config.global_root).expanduser()
        if config.global_root is not None
        else core_paths.global_root()
    )


def _active_workspace_owner(
    config: LifeWorkerConfig,
    *,
    target_workdir: Path | None = None,
) -> dict[str, Any] | None:
    """Return another live daemon that owns the canonical workdir."""
    raw_target = target_workdir or config.project_workdir
    if raw_target is None:
        return None
    try:
        target = Path(raw_target).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    root = _daemon_global_root(config)
    projects = root / "projects"
    try:
        candidates = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        return None
    own_life_dir = Path(config.life_dir).expanduser().resolve()
    from ..core.session import read_session_meta, resolve_session_workdir

    for life_dir in candidates:
        try:
            if life_dir.resolve() == own_life_dir:
                continue
            status = read_daemon_status(life_dir)
            if not status.alive:
                continue
            if status.project_workdir:
                owner_workdir = Path(status.project_workdir).expanduser().resolve(
                    strict=True
                )
            else:
                meta = read_session_meta(root, life_dir.name)
                owner_workdir = resolve_session_workdir(meta, state_dir=life_dir)
            if owner_workdir == target:
                return {
                    "sid": life_dir.name,
                    "pid": status.pid,
                    "workdir": str(target),
                }
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _workspace_start_error(config: LifeWorkerConfig) -> str:
    """Validate workdir SSOT and exclusivity while holding spawn admission."""
    if config.project_workdir is None:
        return ""
    try:
        configured = Path(config.project_workdir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return f"configured workdir is unavailable: {exc}"
    root = _daemon_global_root(config)
    from ..core.session import read_session_meta, resolve_session_workdir

    meta = read_session_meta(root, Path(config.life_dir).name)
    if meta is not None:
        try:
            authoritative = resolve_session_workdir(meta, state_dir=config.life_dir)
        except (OSError, RuntimeError) as exc:
            return f"persisted workdir is unavailable: {exc}"
        if authoritative != configured:
            return (
                "session workdir changed during daemon startup; retry with "
                f"{authoritative}"
            )
    owner = _active_workspace_owner(config, target_workdir=configured)
    if owner is not None:
        return (
            f"workdir {configured} is already owned by active session "
            f"{owner['sid']} (pid {owner['pid']})"
        )
    return ""


def _acquire_daemon_workspace_lease(config: LifeWorkerConfig) -> int | None:
    if config.project_workdir is None:
        return None
    from ..core.workspace_lease import acquire_workspace_lease

    return acquire_workspace_lease(
        config.project_workdir,
        owner={
            "sid": str(config.project_fingerprint or Path(config.life_dir).name),
            "life_dir": str(config.life_dir),
        },
    )


def _release_daemon_workspace_lease(
    fd: int | None,
    *,
    unlock: bool = True,
) -> None:
    from ..core.workspace_lease import release_workspace_lease

    release_workspace_lease(fd, unlock=unlock)


def _active_daemon_count(config: LifeWorkerConfig) -> int:
    projects = _daemon_global_root(config) / "projects"
    try:
        dirs = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        return 0
    count = 0
    for path in dirs:
        try:
            if read_daemon_status(path).alive:
                count += 1
        except Exception:  # noqa: BLE001
            continue
    return count


def _acquire_daemon_spawn_lock(config: LifeWorkerConfig) -> int | None:
    """Serialize host-wide daemon admission through fork + pid publication."""
    if _fcntl is None:
        return None
    root = _daemon_global_root(config)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(root / "daemon-spawn.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        raise
    return fd


def _release_daemon_spawn_lock(fd: int | None, *, unlock: bool = True) -> None:
    if fd is None:
        return
    if unlock and _fcntl is not None:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        os.close(fd)
    except OSError:
        pass


def spawn_detached_daemon(config: LifeWorkerConfig, *, quiet: bool = False) -> int:
    return spawn_detached_process(
        config,
        worker_factory=LifeWorker,
        acquire_spawn_lock=_acquire_daemon_spawn_lock,
        release_spawn_lock=_release_daemon_spawn_lock,
        max_active_daemons=_max_active_daemons,
        active_daemon_count=_active_daemon_count,
        workspace_start_error=_workspace_start_error,
        acquire_workspace_lease=_acquire_daemon_workspace_lease,
        release_workspace_lease=_release_daemon_workspace_lease,
        quiet=quiet,
    )


def spawn_detached_daemon_clean(
    config: LifeWorkerConfig,
    *,
    quiet: bool = False,
) -> int:
    """Spawn through a fresh interpreter before the POSIX double-fork.

    WebAPI is multi-threaded. Forking it directly can inherit Python locks in a
    permanently locked state even after inherited file descriptors are closed.
    A short-lived exec helper starts from a clean interpreter and performs the
    existing admission-checked double-fork there.
    """
    if getattr(sys, "frozen", False):
        return spawn_detached_daemon(config, quiet=quiet)
    env = os.environ.copy()
    env["ARGUS_BINARY_MODE"] = "cli"
    try:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "argus_skill.daemon.spawn_helper"],
            input=json.dumps(_config_payload(config)),
            text=True,
            capture_output=True,
            cwd=str(config.project_workdir or Path.cwd()),
            env=env,
            close_fds=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if not quiet:
            sys.stderr.write(f"argus-skill: clean daemon launcher failed: {exc}\n")
        return 2
    if completed.returncode != 0 and not quiet:
        detail = (completed.stderr or completed.stdout or "").strip()
        if detail:
            sys.stderr.write(detail + "\n")
    return int(completed.returncode)


def run_foreground(config: LifeWorkerConfig) -> int:
    return run_foreground_process(
        config,
        worker_factory=LifeWorker,
        workspace_start_error=_workspace_start_error,
        acquire_workspace_lease=_acquire_daemon_workspace_lease,
        release_workspace_lease=_release_daemon_workspace_lease,
    )
