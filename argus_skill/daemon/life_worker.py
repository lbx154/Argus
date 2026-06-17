"""Life-mode 7×24 worker: detached background process that drains the
backlog forever.

This is the substrate behind ``argus-skill --daemon``. It is the
non-interactive twin of :func:`argus_skill.apps._life_repl.run_life_chat_loop`:
both build the same :class:`~argus_skill.life.supervisor.LifeSupervisor`
against the current project's split memory bundle, but the worker has
no TTY, no slash commands, and no exit on Ctrl-D — only on SIGTERM /
SIGINT.

Coordination with the REPL is provided by the backlog state machine
(:meth:`Backlog.claim_next` is atomic) plus two distinct PID locks:

* ``<project-root>/repl.pid``    — REPL singleton (per project)
* ``<project-root>/daemon.pid``  — daemon singleton (per project)

The two can run side by side: a REPL session lets you /add and inspect
journal/backlog while the daemon drains in the background. They cannot
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
import uuid
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from ..core.bootstrap import inspect_project_bootstrap
from ..core.daemon_lock import DaemonAlreadyRunning, acquire_global_daemon_lock
from ..life.memory import BacklogItem, GlobalMemory, LifeMemory, MemoryBundle, ProjectMemory
from ..life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

log = logging.getLogger(__name__)

__all__ = [
    "LifeWorkerConfig",
    "LifeWorker",
    "DaemonStatus",
    "ContinuousConfigState",
    "continuous_mode_error",
    "format_budget_status",
    "resolve_effective_budget",
    "read_daemon_status",
    "stop_daemon",
    "spawn_detached_daemon",
    "run_handoff_child",
    "read_continuous_state",
    "read_continuous_config",
    "write_continuous_config",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LifeWorkerConfig:
    """How the worker drains the backlog.

    All durations are seconds. ``poll_interval`` is how long the worker
    sleeps between :meth:`LifeSupervisor.tick` calls when the backlog
    is empty — when work is pending it does not sleep, it just keeps
    ticking.
    """

    life_dir: Path
    global_root: Path | None = None
    project_fingerprint: str = ""
    project_label: str = ""
    backend: str = "codex"  # "codex" | "memory"
    engineer_model: str = "gpt-5.5"
    reviewer_model: str = "gpt-5.5"
    scientist_model: str = "gpt-5.5"
    engineer_reasoning_effort: str = "high"
    reviewer_reasoning_effort: str = "high"
    scientist_reasoning_effort: str = "high"
    per_mission_cap_usd: float = 30.0
    daily_cap_usd: float = 180.0
    planner_task_iteration_max_cycles: int = 6
    planner_task_iteration_budget_usd: float = 30.0
    poll_interval: float = 5.0
    log_path: Path | None = None  # defaults to <life_dir>/daemon.log
    project_workdir: Path | None = None
    continuous: bool = False
    continuous_objective: str = ""
    # When True (the default for the lifetime daemon) the supervisor keeps the
    # mission alive after the planner certifies ``project_done`` instead of
    # hard-stopping. Set False (via ``--bounded``) for a one-shot bounded goal.
    continuous_open_ended: bool = True
    # Post-engineer check commands run by the reviewer agent after each
    # engineer round.  For EMNLP projects these auto-refresh manifest and
    # freshness so the reviewer sees up-to-date validation state without
    # the engineer having to remember to run them manually.
    check_commands: list[str] = field(default_factory=lambda: [
        '{argus_python} -m argus_skill.tools.stage_check --project-root .',
    ])


def _apply_bounded_to_check_commands(commands: list[str], *, bounded: bool) -> list[str]:
    # WHY M0.7: --bounded previously reached supervisor project_done logic
    # but not the reviewer acceptance gate, so stage_check kept blocking
    # train-free diagnostics on paper-pipeline benchmark readiness.
    if not bounded:
        return list(commands)
    out: list[str] = []
    for cmd in commands:
        if "stage_check" in cmd and "--bounded" not in cmd:
            cmd = cmd + " --bounded"
        out.append(cmd)
    return out


_HANDOFF_CONFIG_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG"
_HANDOFF_READY_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_READY"
_HANDOFF_TOKEN_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN"
_HANDOFF_GEN_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_GEN"
_SOURCE_SIGNATURE_ENV = "ARGUS_SKILL_DAEMON_SOURCE_SIGNATURE"
_TEST_SOURCE_SIGNATURE_FILE_ENV = "ARGUS_SKILL_DAEMON_TEST_SOURCE_SIGNATURE_FILE"
_TEST_ALLOW_MEMORY_CONTINUOUS_ENV = "ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS"


# ---------------------------------------------------------------------------
# Disk-based continuous config (hot-reloadable by both daemon + REPL)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuousConfigState:
    enabled: bool = False
    objective: str = ""
    done_reason: str = ""
    done_at: str = ""


def continuous_mode_error(backend: str, enabled: bool, objective: str) -> str:
    """Return the public error string for an invalid continuous-mode request."""
    backend = backend.strip().lower()
    objective = objective.strip()
    if objective and not enabled:
        return "--objective requires --continuous"
    if enabled and not objective:
        return "--continuous requires a non-empty --objective"
    allow_memory_continuous = _truthy_env(_TEST_ALLOW_MEMORY_CONTINUOUS_ENV, "0")
    if enabled and backend == "memory" and not allow_memory_continuous:
        return (
            "--continuous requires a planning-capable life backend; "
            "ARGUS_SKILL_LIFE_BACKEND=memory cannot plan"
        )
    return ""

def _continuous_config_path(life_dir: Path) -> Path:
    return life_dir / "continuous.json"


def read_continuous_state(life_dir: Path) -> ContinuousConfigState:
    """Read the full ``continuous.json`` state blob."""
    path = _continuous_config_path(life_dir)
    if not path.exists():
        return ContinuousConfigState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ContinuousConfigState()
        def _text(value: Any) -> str:
            return "" if value is None else str(value)
        return ContinuousConfigState(
            enabled=bool(data.get("enabled", False)),
            objective=_text(data.get("objective", "")),
            done_reason=_text(data.get("done_reason", "")),
            done_at=_text(data.get("done_at", "")),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ContinuousConfigState()


def read_continuous_config(life_dir: Path) -> tuple[bool, str]:
    """Read ``(enabled, objective)`` from ``<life_dir>/continuous.json``.

    Returns ``(False, "")`` if the file is missing or malformed.
    """
    state = read_continuous_state(life_dir)
    return state.enabled, state.objective


def write_continuous_config(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    done_reason: str = "",
) -> None:
    """Atomically write ``continuous.json`` so the daemon can hot-reload.

    Uses write-to-temp + ``os.replace`` for atomicity.
    """
    objective = objective.strip()
    if enabled and not objective:
        log.warning("refusing to write invalid continuous config to %s", life_dir)
        return
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _continuous_config_path(life_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    data = {
        "enabled": enabled,
        "objective": objective,
    }
    if done_reason:
        data["done_reason"] = done_reason
        data["done_at"] = datetime.now(timezone.utc).isoformat()
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(str(tmp), str(path))
    except OSError:
        log.warning("failed to write continuous config to %s", path)


# ---------------------------------------------------------------------------
# Blue/green self-handoff
# ---------------------------------------------------------------------------

def _truthy_env(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _strip_git_config_injection(env: MutableMapping[str, str]) -> list[str]:
    """Remove the ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_*`` /
    ``GIT_CONFIG_VALUE_*`` env-based config-injection family in place.

    The host seeds a benign ``safe.bareRepository=explicit`` override via
    these vars, but the codex sandbox forwards ``GIT_CONFIG_COUNT`` /
    ``GIT_CONFIG_VALUE_0`` while dropping ``GIT_CONFIG_KEY_0`` — leaving an
    incomplete tuple that makes *every* ``git`` command in the agent's shell
    fail with ``fatal: unable to parse command-line config`` until the agent
    rediscovers an ``env -u`` workaround, burning rounds each mission. The
    agent's project git work does not need this host override, so drop the
    whole family from the env handed to child shells.

    Returns the list of removed keys (for logging/tests).
    """
    removed = [
        k
        for k in list(env)
        if k == "GIT_CONFIG_COUNT"
        or k.startswith("GIT_CONFIG_KEY_")
        or k.startswith("GIT_CONFIG_VALUE_")
    ]
    for k in removed:
        env.pop(k, None)
    return removed


def _auto_handoff_enabled() -> bool:
    return _truthy_env("ARGUS_SKILL_DAEMON_AUTO_RESTART", "0")


def _handoff_min_interval_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("ARGUS_SKILL_DAEMON_HANDOFF_MIN_S", "60")))
    except ValueError:
        return 60.0


def _handoff_generation() -> int:
    try:
        return max(0, int(os.environ.get(_HANDOFF_GEN_ENV, "0")))
    except ValueError:
        return 0


def _handoff_max_generations() -> int:
    try:
        return max(1, int(os.environ.get("ARGUS_SKILL_DAEMON_HANDOFF_MAX_GEN", "10")))
    except ValueError:
        return 10


def _source_signature() -> str:
    """Content hash of runtime files that require a daemon restart.

    Covers both Python runtime code and the behavior-defining built-in skill
    markdown: a skill edit changes how the engineer/reviewer act, so the daemon
    is just as stale against a skill change as against a ``.py`` change and the
    auto-handoff signature must notice it.
    """
    test_signature_path = os.environ.get(_TEST_SOURCE_SIGNATURE_FILE_ENV, "").strip()
    if test_signature_path:
        try:
            return Path(test_signature_path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parent
    paths: list[Path] = sorted(package_root.rglob("*.py"))
    paths += sorted((package_root / "builtin_skills").rglob("*.md"))
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        paths.append(pyproject)
    digest = hashlib.sha256()
    for path in paths:
        parts = set(path.parts)
        if "__pycache__" in parts or ".git" in parts:
            continue
        try:
            rel = path.relative_to(repo_root)
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(str(rel).encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _config_payload(config: LifeWorkerConfig) -> dict[str, Any]:
    return {
        "life_dir": str(config.life_dir),
        "global_root": str(config.global_root) if config.global_root is not None else "",
        "project_fingerprint": config.project_fingerprint,
        "project_label": config.project_label,
        "backend": config.backend,
        "engineer_model": config.engineer_model,
        "reviewer_model": config.reviewer_model,
        "scientist_model": config.scientist_model,
        "engineer_reasoning_effort": config.engineer_reasoning_effort,
        "reviewer_reasoning_effort": config.reviewer_reasoning_effort,
        "scientist_reasoning_effort": config.scientist_reasoning_effort,
        "per_mission_cap_usd": config.per_mission_cap_usd,
        "daily_cap_usd": config.daily_cap_usd,
        "planner_task_iteration_max_cycles": config.planner_task_iteration_max_cycles,
        "planner_task_iteration_budget_usd": config.planner_task_iteration_budget_usd,
        "poll_interval": config.poll_interval,
        "log_path": str(config.log_path) if config.log_path is not None else "",
        "project_workdir": str(config.project_workdir) if config.project_workdir is not None else "",
        "continuous": config.continuous,
        "continuous_objective": config.continuous_objective,
        "continuous_open_ended": config.continuous_open_ended,
    }


def _config_from_payload(data: dict[str, Any]) -> LifeWorkerConfig:
    from ..tools.capability_vault import resolve_route_model

    log_path = str(data.get("log_path") or "")
    global_root = str(data.get("global_root") or "")
    project_workdir = str(data.get("project_workdir") or "")
    return LifeWorkerConfig(
        life_dir=Path(str(data["life_dir"])).expanduser(),
        global_root=Path(global_root).expanduser() if global_root else None,
        project_workdir=Path(project_workdir).expanduser() if project_workdir else None,
        project_fingerprint=str(data.get("project_fingerprint") or ""),
        project_label=str(data.get("project_label") or ""),
        backend=str(data.get("backend") or "codex"),
        engineer_model=str(data.get("engineer_model") or resolve_route_model("engineer")),
        reviewer_model=str(data.get("reviewer_model") or resolve_route_model("reviewer")),
        scientist_model=str(data.get("scientist_model") or resolve_route_model("scientist")),
        engineer_reasoning_effort=str(
            data.get("engineer_reasoning_effort") or "high"
        ),
        reviewer_reasoning_effort=str(
            data.get("reviewer_reasoning_effort") or "high"
        ),
        scientist_reasoning_effort=str(
            data.get("scientist_reasoning_effort") or "high"
        ),
        per_mission_cap_usd=float(data.get("per_mission_cap_usd") or 30.0),
        daily_cap_usd=float(data.get("daily_cap_usd") or 180.0),
        planner_task_iteration_max_cycles=int(
            data.get("planner_task_iteration_max_cycles") or 6
        ),
        planner_task_iteration_budget_usd=float(
            data.get("planner_task_iteration_budget_usd") or 30.0
        ),
        poll_interval=float(data.get("poll_interval") or 5.0),
        log_path=Path(log_path).expanduser() if log_path else None,
        continuous=bool(data.get("continuous")),
        continuous_objective=str(data.get("continuous_objective") or ""),
        continuous_open_ended=bool(data.get("continuous_open_ended", True)),
    )


def _handoff_ready_path(life_dir: Path) -> Path:
    return life_dir / "daemon.handoff.json"


def _handoff_config_path(life_dir: Path, token: str) -> Path:
    return life_dir / f"daemon.handoff.{token}.json"


def _spawn_handoff_candidate(
    config: LifeWorkerConfig,
    *,
    source_signature: str,
    reason: str,
    standby_timeout: float = 30.0,
) -> bool:
    """Start a fresh interpreter and wait until it reaches standby."""
    token = uuid.uuid4().hex
    config.life_dir.mkdir(parents=True, exist_ok=True)
    ready_path = _handoff_ready_path(config.life_dir)
    config_path = _handoff_config_path(config.life_dir, token)
    ready_path.unlink(missing_ok=True)
    payload = {
        "token": token,
        "reason": reason,
        "source_signature": source_signature,
        "config": _config_payload(config),
    }
    try:
        config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("daemon handoff: failed to write config")
        return False
    log_path = _daemon_log_path(config.life_dir, config.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env[_HANDOFF_CONFIG_ENV] = str(config_path)
    env[_HANDOFF_READY_ENV] = str(ready_path)
    env[_HANDOFF_TOKEN_ENV] = token
    env[_SOURCE_SIGNATURE_ENV] = source_signature
    env[_HANDOFF_GEN_ENV] = str(_handoff_generation() + 1)
    cmd = [
        sys.executable,
        "-c",
        (
            "from argus_skill.daemon.life_worker import run_handoff_child; "
            "raise SystemExit(run_handoff_child())"
        ),
    ]
    try:
        with log_path.open("ab") as log_fh:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd="/",
                env=env,
                start_new_session=True,
                close_fds=True,
            )
    except OSError:
        log.exception("daemon handoff: failed to spawn candidate")
        config_path.unlink(missing_ok=True)
        return False

    deadline = time.monotonic() + standby_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.warning("daemon handoff: candidate exited early rc=%s", proc.returncode)
            config_path.unlink(missing_ok=True)
            return False
        try:
            data = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            time.sleep(0.1)
            continue
        if data.get("token") == token and data.get("state") == "standby":
            return True
        time.sleep(0.1)
    log.warning("daemon handoff: candidate did not reach standby in %.1fs", standby_timeout)
    try:
        proc.terminate()
    except OSError:
        pass
    config_path.unlink(missing_ok=True)
    ready_path.unlink(missing_ok=True)
    return False


def _acquire_daemon_lock_with_timeout(pid_path: Path, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    last_exc: DaemonAlreadyRunning | None = None
    while True:
        try:
            return acquire_global_daemon_lock(pid_path=pid_path)
        except DaemonAlreadyRunning as exc:
            last_exc = exc
            if time.monotonic() >= deadline:
                raise last_exc
            time.sleep(0.1)


def run_handoff_child() -> int:
    """Entrypoint for a blue/green handoff candidate."""
    config_env = os.environ.get(_HANDOFF_CONFIG_ENV, "")
    ready_env = os.environ.get(_HANDOFF_READY_ENV, "")
    token = os.environ.get(_HANDOFF_TOKEN_ENV, "")
    if not config_env or not ready_env or not token:
        sys.stderr.write("argus-skill handoff: missing handoff environment\n")
        return 2
    config_path = Path(config_env).expanduser()
    ready_path = Path(ready_env).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        config = _config_from_payload(payload["config"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(f"argus-skill handoff: invalid config: {exc}\n")
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    worker = LifeWorker(config)
    try:
        ready_path.write_text(
            json.dumps({
                "token": token,
                "state": "standby",
                "pid": os.getpid(),
                "ts": time.time(),
            }),
            encoding="utf-8",
        )
    except OSError as exc:
        sys.stderr.write(f"argus-skill handoff: failed to write standby file: {exc}\n")
        return 2

    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        lock = _acquire_daemon_lock_with_timeout(pid_path, timeout=60.0)
    except DaemonAlreadyRunning as exc:
        log.error("handoff candidate could not acquire daemon lock (pid=%s)", exc.pid)
        return 2

    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
        ready_path.unlink(missing_ok=True)
        config_path.unlink(missing_ok=True)
    except OSError:
        log.exception("handoff candidate: failed to publish active status")

    try:
        return worker.run_forever()
    finally:
        lock.release()
        try:
            status_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class LifeWorker:
    """The 7×24 background worker.

    Construct, then call :meth:`run_forever` from the daemon process.
    Stops cleanly on SIGTERM / SIGINT — the supervisor's tick is one
    mission so there is at most one outstanding ``running`` item when
    the signal lands; the next process startup will reap it via
    :meth:`Backlog.reap_orphans` and mark it ``failed``.
    """

    def __init__(self, config: LifeWorkerConfig) -> None:
        self.config = config
        self._stop = threading.Event()
        self._started_at: float | None = None
        self._missions_completed = 0
        self._source_signature = (
            os.environ.get(_SOURCE_SIGNATURE_ENV)
            or (_source_signature() if _auto_handoff_enabled() else "")
        )
        self._failed_handoff_signature = ""
        self._last_handoff_attempt_at = 0.0

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:  # noqa: ANN401
            log.info("daemon: received signal %s, requesting stop", signum)
            self._stop.set()

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
        """
        from ..tools.new_auto_research_project import (
            init_project_venv,
            load_template_text,
            render_agents_md,
        )

        agents_path = project_root / "AGENTS.md"
        if not agents_path.exists():
            objective = (self.config.continuous_objective or "").strip() or None
            template_text = load_template_text(None)
            agents_md = render_agents_md(
                template_text,
                project_name=project_root.name,
                version="v1",
                objective=objective,
            )
            agents_path.write_text(agents_md, encoding="utf-8")

        if not (project_root / ".venv").exists():
            init_project_venv(project_root)

    def _seed_bootstrap_task(
        self,
        memory: Any,
        sink: Any,
        preflight: Any,
    ) -> bool:
        """Enqueue the bootstrap backlog item once per empty project root."""
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

        # Parity with the standalone launcher's create_project: a
        # continuous-mode project must also get an ``AGENTS.md`` (the engineer
        # prompt instructs the agent to read it — without it the agent burns
        # rounds on ``sed: can't read AGENTS.md``) and a per-project ``.venv``
        # (so the agent pip-installs experiment deps into an overlay rather
        # than the framework venv). Both are no-ops when already present.
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

    def run_forever(self) -> int:
        self._install_signal_handlers()
        self._started_at = time.time()

        # Ensure ARGUS_SKILL_PYTHON is set in the process environment so
        # all child processes (engineer codex, reviewer codex, check_commands)
        # can find the argus_skill package.  Without this, shells spawned by
        # codex exec fall back to /usr/bin/python which cannot import
        # argus_skill.
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

        # Build the runner the same way the REPL does. Importing here
        # keeps daemon.life_worker free of CLI-only deps until needed.
        from ..apps._life_repl import LifeStderrSink, build_life_runner
        ns = _runner_namespace(cfg)
        ns.stop_event = self._stop
        runner = build_life_runner(ns)

        # Continuous drain: each LifeSupervisor.run() drains until the
        # backlog goes empty or the budget caps. Then we sleep
        # poll_interval seconds and try again — items may have been
        # /add'd from a coexisting REPL.
        from ..life.activity_log import ActivityLogSink
        from ..life.event_log import JsonlEventSink

        # Telegram live-streaming reporter (daemon thread). Disabled unless
        # ARGUS_SKILL_ENABLE_TELEGRAM=1 so stale bot tokens do not spam chats.
        stream_reporter = None
        try:
            from ..life.notify import TelegramStreamReporter, telegram_enabled
            if telegram_enabled():
                stream_reporter = TelegramStreamReporter(stop_event=self._stop)
                stream_reporter.start()
                log.info("telegram stream reporter started")
            else:
                log.info("telegram stream reporter disabled")
        except Exception:  # noqa: BLE001
            log.debug("telegram stream reporter unavailable; continuing")

        # ActivityLogSink (outermost) renders a concise, high-signal
        # <life_dir>/activity.log for debugging; JsonlEventSink keeps the
        # exhaustive events.jsonl replay surface; _DaemonSink drives the
        # daemon log + telegram. Events flow outermost -> innermost.
        sink = ActivityLogSink(
            JsonlEventSink(
                _DaemonSink(self, stream_reporter=stream_reporter),
                life_dir=runtime_root,
            ),
            life_dir=runtime_root,
        )

        if cfg.project_workdir is not None:
            bootstrap_preflight = inspect_project_bootstrap(
                cfg.project_workdir,
                objective_hint=cfg.continuous_objective,
            )
            if bootstrap_preflight.should_bootstrap:
                self._seed_bootstrap_task(mem, sink, bootstrap_preflight)

        # Build a config provider that reads continuous.json from disk,
        # so the REPL can enable/disable continuous mode while the daemon
        # is running — no daemon restart needed.
        def _continuous_provider() -> tuple[bool, str]:
            enabled, objective = read_continuous_config(runtime_root)
            if continuous_mode_error(cfg.backend, enabled, objective):
                if enabled:
                    write_continuous_config(
                        runtime_root,
                        enabled=False,
                        objective=objective,
                    )
                return False, objective
            return enabled, objective

        # Seed continuous config from disk (or CLI flags).
        init_continuous, init_objective = _continuous_provider()
        if cfg.continuous:
            # CLI flags override disk — also persist them so REPL sees.
            init_continuous = True
            init_objective = cfg.continuous_objective or init_objective
            write_continuous_config(
                runtime_root,
                enabled=True,
                objective=init_objective,
            )

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
            planner_runner=getattr(runner, "backend", None),
        )

        # Vault pre-flight: refuse to start daemon if a required
        # model_api route is misconfigured (e.g. wrong deployment
        # name → 404 on every mission, observed 2026-06-01: 47 min /
        # $2.50 doom loop). The codex backend is the only one that
        # talks to the real model API; memory backend (tests) skips.
        # Operator override via ARGUS_SKILL_SKIP_VAULT_PREFLIGHT=1.
        if (
            cfg.backend == "codex"
            and os.environ.get("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "").strip() not in ("1", "true", "yes")
        ):
            from ..core.vault_preflight import (
                check_routes as _vault_preflight_check,
            )
            from ..core.vault_preflight import (
                format_report as _vault_preflight_format,
            )
            try:
                preflight = _vault_preflight_check()
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
            from ..life.notify import telegram_enabled
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

        while not self._stop.is_set():
            summary: dict = {}
            try:
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
                    write_continuous_config(
                        runtime_root,
                        enabled=False,
                        objective=sup.config.continuous_objective,
                        done_reason="planner declared project done",
                    )
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

        log.info(
            "daemon: stopping cleanly (uptime=%.1fs missions=%d)",
            time.time() - (self._started_at or time.time()),
            self._missions_completed,
        )
        return 0

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

def _runner_namespace(cfg: LifeWorkerConfig) -> Any:
    """Build the argparse-shaped namespace ``build_life_runner`` expects."""
    import argparse
    ns = argparse.Namespace()
    ns.backend = cfg.backend
    ns.engineer_model = cfg.engineer_model
    ns.reviewer_model = cfg.reviewer_model
    ns.scientist_model = cfg.scientist_model
    ns.engineer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        cfg.engineer_reasoning_effort,
    )
    ns.reviewer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
        cfg.reviewer_reasoning_effort,
    )
    ns.scientist_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT",
        cfg.scientist_reasoning_effort,
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
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "500"))
    ns.plan_mode = os.environ.get("ARGUS_SKILL_PLAN_MODE", "auto")
    ns.plan_model = os.environ.get("ARGUS_SKILL_PLAN_MODEL")
    ns.check = []
    # WHY M0.7: bounded daemon missions must pass --bounded into
    # stage_check; otherwise the reviewer loops on irrelevant paper gates.
    ns.check_commands = _apply_bounded_to_check_commands(
        list(cfg.check_commands),
        bounded=not cfg.continuous_open_ended,
    )
    ns.color = None
    ns.verbose = False
    ns.quiet = True
    return ns


def _worker_runtime_context(cfg: LifeWorkerConfig) -> str:
    """Return static context injected into daemon-driven missions."""
    from ..life.research_profile import render_research_profile_context
    from ..life.special_prompts import render_special_prompts_context
    from ..tools.capability_vault import format_api_context, format_gpu_context

    # Operator directives ("special prompts") are machine-specific house
    # rules; they lead the runtime context so the agent sees them first.
    special_context = render_special_prompts_context()
    research_context = render_research_profile_context()
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
        f"- Budget: ${cfg.per_mission_cap_usd:.0f}/mission, ${cfg.daily_cap_usd:.0f}/day\n"
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
    from ..apps._life_repl import _inbox_drainer_for
    from ..life.telemetry import telemetry_interval_from_env

    return LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=cfg.per_mission_cap_usd,
            daily_cap_usd=cfg.daily_cap_usd,
            max_missions=64,
        ),
        planner_task_iteration_max_cycles=cfg.planner_task_iteration_max_cycles,
        planner_task_iteration_budget_usd=cfg.planner_task_iteration_budget_usd,
        poll_interval_seconds=2.0,
        project_worktree=cfg.project_workdir,
        stop_event=stop_event,
        user_inbox=_inbox_drainer_for(runtime_root),
        runtime_context=_worker_runtime_context(cfg),
        continuous=init_continuous,
        continuous_objective=init_objective,
        open_ended=cfg.continuous_open_ended,
        full_emnlp_gate=cfg.continuous_open_ended,
        continuous_config_provider=continuous_provider,
        planner_runtime_context_provider=planner_runtime_context_provider,
        planner_restart_handler=planner_restart_handler,
        post_mission_hook=post_mission_hook,
        telemetry_dir=runtime_root,
        telemetry_interval_seconds=telemetry_interval_from_env(),
    )


class _DaemonSink:
    """Minimal sink: counts mission completions, forwards progress to
    the Telegram live-streaming reporter, logs everything else."""

    def __init__(self, worker: LifeWorker, stream_reporter: Any = None) -> None:
        self._worker = worker
        self._stream_reporter = stream_reporter

    def handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type") or event.get("kind") or ""
        if kind in (
            "life.mission.done",
            "life.mission.completed",
            "life.mission.failed",
            "life.mission.skipped",
        ):
            self._worker._missions_completed += 1
        # Forward to Telegram live-streaming reporter (non-blocking)
        if self._stream_reporter is not None:
            try:
                self._stream_reporter.on_event(event)
            except Exception:  # noqa: BLE001
                pass
        log.debug("daemon event: %s %s", kind, event)


# ---------------------------------------------------------------------------
# PID lock + status
# ---------------------------------------------------------------------------

def _daemon_pid_path(life_dir: Path) -> Path:
    return life_dir / "daemon.pid"


def _daemon_status_path(life_dir: Path) -> Path:
    return life_dir / "daemon.status.json"


def _daemon_log_path(life_dir: Path, override: Path | None = None) -> Path:
    return override if override is not None else life_dir / "daemon.log"


def _daemon_status_payload(config: LifeWorkerConfig, *, started_at_iso: str) -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "started_at_iso": started_at_iso,
        "backend": config.backend,
        "life_dir": str(config.life_dir),
        "per_mission_cap_usd": config.per_mission_cap_usd,
        "daily_cap_usd": config.daily_cap_usd,
    }


@dataclass
class DaemonStatus:
    alive: bool
    pid: int | None
    started_at_iso: str | None
    uptime_seconds: float | None
    life_dir: Path
    backend: str | None = None
    per_mission_cap_usd: float | None = None
    daily_cap_usd: float | None = None
    pid_path: Path | None = None


def _daemon_budget_from_env() -> LifeBudget:
    return LifeBudget(
        per_mission_cap_usd=float(
            os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")
        ),
        daily_cap_usd=float(
            os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")
        ),
    )


def resolve_effective_budget(status: Any | None = None) -> LifeBudget:
    """Return the live budget caps for operator surfaces.

    When the daemon has published caps in its status sidecar, use those
    exact values. Otherwise fall back to the current env/default caps so
    stopped-daemon status commands still show what a new launch would
    enforce.
    """
    alive = bool(getattr(status, "alive", False))
    per_mission = getattr(status, "per_mission_cap_usd", None)
    daily = getattr(status, "daily_cap_usd", None)
    try:
        if alive and per_mission is not None and daily is not None:
            return LifeBudget(
                per_mission_cap_usd=float(per_mission),
                daily_cap_usd=float(daily),
            )
    except (TypeError, ValueError):
        pass
    return _daemon_budget_from_env()


def format_budget_status(journal: Any, *, status: Any | None = None) -> str:
    budget = resolve_effective_budget(status)
    remaining = budget.remaining_today(journal)
    tail = " (paused)" if remaining <= 0 else ""
    return (
        "budget   : "
        f"per-mission ${budget.per_mission_cap_usd:.2f} · "
        f"daily ${budget.daily_cap_usd:.2f} · "
        f"remaining ${remaining:.2f}{tail}"
    )


def read_daemon_status(life_dir: Path | None = None) -> DaemonStatus:
    """Read the daemon's pid file and return a structured status.

    ``alive=True`` only if both the pid file exists AND the process is
    still running (verified via ``os.kill(pid, 0)``). A stale pid file
    from a hard kill returns ``alive=False`` so callers know the lock
    is reclaimable.
    """
    if life_dir is None:
        from ..core import paths as core_paths
        life_dir = core_paths.global_root()
    else:
        life_dir = Path(life_dir).expanduser()
    pid_path = _daemon_pid_path(life_dir)
    if not pid_path.exists():
        return DaemonStatus(
            alive=False, pid=None, started_at_iso=None,
            uptime_seconds=None, life_dir=life_dir, pid_path=pid_path,
        )
    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return DaemonStatus(
            alive=False, pid=None, started_at_iso=None,
            uptime_seconds=None, life_dir=life_dir, pid_path=pid_path,
        )
    alive = _process_alive(pid)
    started_iso: str | None = None
    backend: str | None = None
    per_mission_cap_usd: float | None = None
    daily_cap_usd: float | None = None
    uptime: float | None = None
    sidecar = _daemon_status_path(life_dir)
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            started_iso = data.get("started_at_iso")
            backend = data.get("backend")
            raw_per_mission = data.get("per_mission_cap_usd")
            raw_daily = data.get("daily_cap_usd")
            if raw_per_mission is not None:
                per_mission_cap_usd = float(raw_per_mission)
            if raw_daily is not None:
                daily_cap_usd = float(raw_daily)
            if started_iso:
                started_dt = datetime.fromisoformat(started_iso)
                uptime = (datetime.now(timezone.utc) - started_dt).total_seconds()
        except Exception:  # noqa: BLE001
            pass
    return DaemonStatus(
        alive=alive,
        pid=pid if alive else None,
        started_at_iso=started_iso,
        uptime_seconds=uptime,
        life_dir=life_dir,
        backend=backend,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        pid_path=pid_path,
    )


def wait_for_daemon_status(
    life_dir: Path | None = None,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> DaemonStatus | None:
    """Wait briefly for the daemon pid/status sidecars to become readable."""
    deadline = time.monotonic() + max(0.0, timeout)
    last: DaemonStatus | None = None
    while True:
        status = read_daemon_status(life_dir)
        last = status
        if status.alive and status.pid is not None:
            return status
        if time.monotonic() >= deadline:
            return last
        time.sleep(max(0.0, poll_interval))


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_daemon(life_dir: Path | None = None, *, timeout: float = 10.0) -> int:
    """Send SIGTERM to the running daemon and wait up to ``timeout`` for exit.

    Returns 0 on graceful stop, 1 if no daemon was running, 2 on timeout.
    """
    status = read_daemon_status(life_dir)
    if not status.alive or status.pid is None:
        sys.stderr.write("argus-skill: no daemon is running for this life-dir.\n")
        return 1
    pid = status.pid
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        time.sleep(0.1)
    sys.stderr.write(
        f"argus-skill: daemon (pid {pid}) did not exit within {timeout:.1f}s. "
        "Send SIGKILL manually if needed.\n"
    )
    return 2


# ---------------------------------------------------------------------------
# Detach (POSIX double-fork)
# ---------------------------------------------------------------------------

def spawn_detached_daemon(config: LifeWorkerConfig) -> int:
    """Fork a detached background process running the worker, then exit.

    Returns 0 on successful spawn, 2 if a daemon is already running.

    Uses the standard double-fork idiom to fully detach from the
    controlling terminal and become a session leader. The grandchild
    inherits no fds we care about, redirects std{in,out,err} to the
    log file, acquires the daemon pid lock, writes the status sidecar,
    and finally enters :meth:`LifeWorker.run_forever`.
    """
    # Pre-flight: refuse to spawn if a live daemon is already there.
    existing = read_daemon_status(config.life_dir)
    if existing.alive and existing.pid is not None:
        sys.stderr.write(
            f"argus-skill: daemon already running for this life-dir "
            f"(pid={existing.pid}, lock={existing.pid_path}).\n"
        )
        return 2
    config.life_dir.mkdir(parents=True, exist_ok=True)
    log_path = _daemon_log_path(config.life_dir, config.log_path)
    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)

    # First fork.
    pid = os.fork()
    if pid > 0:
        # Parent waits briefly so we can confirm the daemon really came
        # up before printing success.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pid_path.exists() and status_path.exists():
                try:
                    written_pid = int(pid_path.read_text().strip())
                except (OSError, ValueError):
                    written_pid = 0
                if written_pid and _process_alive(written_pid):
                    sys.stdout.write(
                        f"argus-skill: daemon started (pid {written_pid}, "
                        f"life_dir={config.life_dir}, log={log_path}).\n"
                    )
                    return 0
            time.sleep(0.1)
        sys.stderr.write(
            "argus-skill: daemon fork succeeded but child did not write its "
            f"pid file within 5s. Check {log_path} for errors.\n"
        )
        return 2

    # First child — become session leader.
    try:
        os.setsid()
    except OSError:
        pass

    # Second fork — guarantee no controlling TTY can be reacquired.
    try:
        pid2 = os.fork()
    except OSError:
        pid2 = -1
    if pid2 > 0:
        os._exit(0)

    # Grandchild: this is the daemon. Redirect std fds to the log file.
    os.chdir("/")
    os.umask(0o077)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull_fd, sys.stdin.fileno())
    os.close(devnull_fd)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Acquire the daemon pid lock. If a competing daemon raced us and
    # got it first we exit cleanly — the parent's pre-flight is just
    # an optimization, the lock is the real guarantee.
    try:
        lock = acquire_global_daemon_lock(pid_path=pid_path)
    except DaemonAlreadyRunning as exc:
        log.error("daemon: another daemon already holds the lock (pid=%s)", exc.pid)
        os._exit(2)

    # Write the status sidecar so ``read_daemon_status`` knows when we
    # started + which backend we're on.
    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
    except OSError:
        log.exception("daemon: failed to write status sidecar")

    try:
        worker = LifeWorker(config)
        rc = worker.run_forever()
    except Exception:  # noqa: BLE001
        log.exception("daemon: fatal error")
        rc = 1
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to release lock")
        try:
            status_path.unlink()
        except OSError:
            pass

    os._exit(rc)


def run_foreground(config: LifeWorkerConfig) -> int:
    """Run the worker in the foreground (for systemd / debugging).

    Same lock + status sidecar as the detached path, but logs go to
    stderr and SIGINT/SIGTERM stop the process directly.
    """
    config.life_dir.mkdir(parents=True, exist_ok=True)
    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        lock = acquire_global_daemon_lock(pid_path=pid_path)
    except DaemonAlreadyRunning as exc:
        sys.stderr.write(
            f"argus-skill: daemon already running for this life-dir "
            f"(pid={exc.pid}, lock={exc.lock_path}).\n"
        )
        return 2

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
    except OSError:
        log.exception("daemon-fg: failed to write status sidecar")

    try:
        worker = LifeWorker(config)
        return worker.run_forever()
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            log.exception("daemon-fg: failed to release lock")
        try:
            status_path.unlink()
        except OSError:
            pass
