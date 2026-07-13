"""Daemon continuous configuration, status sidecar, logs, and stop control."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.usage import format_usage_cost
from ..life.supervisor import LifeBudget, global_daily_spend, global_daily_usage_summary

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
_GLOBAL_DAILY_SPEND_IMPL = global_daily_spend
_TEST_ALLOW_MEMORY_CONTINUOUS_ENV = "ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS"
_DRAIN_REQUEST_FILE = "daemon.drain-request.json"


def _truthy_env(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class ContinuousConfigState:
    enabled: bool = False
    objective: str = ""
    done_reason: str = ""
    done_at: str = ""
    generation: int = field(default=0, compare=False)


def continuous_mode_error(backend: str, enabled: bool, objective: str) -> str:
    backend = backend.strip().lower()
    objective = objective.strip()
    if objective and not enabled:
        return "--objective requires --continuous"
    if enabled and not objective:
        return "--continuous requires a non-empty --objective"
    if enabled and backend == "memory" and not _truthy_env(_TEST_ALLOW_MEMORY_CONTINUOUS_ENV, "0"):
        return (
            "--continuous requires a planning-capable life backend; "
            "ARGUS_SKILL_LIFE_BACKEND=memory cannot plan"
        )
    return ""


def _continuous_config_path(life_dir: Path) -> Path:
    return life_dir / "continuous.json"


def _daemon_drain_request_path(life_dir: Path) -> Path:
    return life_dir / _DRAIN_REQUEST_FILE


def request_daemon_drain(life_dir: Path, *, pid: int) -> None:
    """Persist a PID-bound graceful-drain request before sending SIGTERM."""
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _daemon_drain_request_path(life_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"pid": int(pid), "requested_at": time.time()}),
        encoding="utf-8",
    )
    os.replace(str(tmp), str(path))


def daemon_drain_requested(life_dir: Path, *, pid: int) -> bool:
    """Return whether the current drain request targets ``pid``."""
    try:
        payload = json.loads(
            _daemon_drain_request_path(life_dir).read_text(encoding="utf-8")
        )
        return isinstance(payload, dict) and int(payload.get("pid") or 0) == int(pid)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return False


def clear_daemon_drain_request(life_dir: Path, *, pid: int) -> None:
    """Remove the drain request only when it still targets ``pid``."""
    if not daemon_drain_requested(life_dir, pid=pid):
        return
    try:
        _daemon_drain_request_path(life_dir).unlink()
    except FileNotFoundError:
        pass


@contextmanager
def _continuous_config_lock(life_dir: Path):
    life_dir.mkdir(parents=True, exist_ok=True)
    with (life_dir / ".continuous.lock").open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_continuous_state_unlocked(life_dir: Path) -> ContinuousConfigState:
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
            generation=max(0, int(data.get("generation", 0) or 0)),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ContinuousConfigState()


def read_continuous_state(life_dir: Path) -> ContinuousConfigState:
    with _continuous_config_lock(life_dir):
        return _read_continuous_state_unlocked(life_dir)


def read_continuous_config(life_dir: Path) -> tuple[bool, str]:
    state = read_continuous_state(life_dir)
    return state.enabled, state.objective


def write_continuous_config(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    done_reason: str = "",
) -> None:
    objective = objective.strip()
    if enabled and not objective:
        log.warning("refusing to write invalid continuous config to %s", life_dir)
        return
    with _continuous_config_lock(life_dir):
        current = _read_continuous_state_unlocked(life_dir)
        _write_continuous_config_unlocked(
            life_dir,
            enabled=enabled,
            objective=objective,
            done_reason=done_reason,
            generation=current.generation + 1,
        )


def _write_continuous_config_unlocked(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    done_reason: str = "",
    done_at: str = "",
    generation: int,
) -> bool:
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _continuous_config_path(life_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    data = {
        "enabled": enabled,
        "objective": objective,
        "generation": max(0, int(generation)),
    }
    if done_reason:
        data["done_reason"] = done_reason
        data["done_at"] = done_at or datetime.now(timezone.utc).isoformat()
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(str(tmp), str(path))
        return True
    except OSError:
        log.warning("failed to write continuous config to %s", path)
        return False


def compare_and_swap_continuous_config(
    life_dir: Path,
    *,
    expected: ContinuousConfigState,
    enabled: bool,
    objective: str,
    done_reason: str = "",
    before_write: Callable[[], None] | None = None,
) -> bool:
    """Atomically replace continuous state only if no command changed it."""
    objective = objective.strip()
    if enabled and not objective:
        return False
    with _continuous_config_lock(life_dir):
        current = _read_continuous_state_unlocked(life_dir)
        if not _same_continuous_state(current, expected):
            return False
        if before_write is not None:
            before_write()
        return _write_continuous_config_unlocked(
            life_dir,
            enabled=enabled,
            objective=objective,
            done_reason=done_reason,
            generation=current.generation + 1,
        )


def disable_continuous_config(
    life_dir: Path,
    *,
    done_reason: str = "",
) -> ContinuousConfigState:
    """Atomically disable the latest generation while preserving its objective."""
    with _continuous_config_lock(life_dir):
        current = _read_continuous_state_unlocked(life_dir)
        generation = current.generation + 1
        if not _write_continuous_config_unlocked(
            life_dir,
            enabled=False,
            objective=current.objective,
            done_reason=done_reason,
            generation=generation,
        ):
            return current
        return _read_continuous_state_unlocked(life_dir)


def _same_continuous_state(
    left: ContinuousConfigState,
    right: ContinuousConfigState,
) -> bool:
    return (
        left.enabled == right.enabled
        and left.objective == right.objective
        and left.done_reason == right.done_reason
        and left.done_at == right.done_at
        and left.generation == right.generation
    )

def _daemon_pid_path(life_dir: Path) -> Path:
    return life_dir / "daemon.pid"


def _daemon_status_path(life_dir: Path) -> Path:
    return life_dir / "daemon.status.json"


def _new_boot_id() -> str:
    """Per-boot daemon id — UTC timestamp + a short random suffix (collision-free
    even on a sub-second restart). Segments each boot's log so consecutive daemon
    runs on the same project never interleave in one file."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def _daemon_log_path(
    life_dir: Path, override: Path | None = None, boot_id: str | None = None
) -> Path:
    """Per-boot daemon log path. An explicit ``override`` (``config.log_path``)
    always wins. Otherwise each boot gets its OWN file
    ``<life_dir>/daemons/boot-<id>.log``; the stable ``<life_dir>/daemon.log``
    symlink (:func:`_point_active_daemon_log`) points at the current boot for
    back-compat readers / ``tail`` / ``--status``. Identity stays per-PROJECT (one
    daemon per life_dir) — this only segments that one daemon's log by boot."""
    if override is not None:
        return override
    return life_dir / "daemons" / f"boot-{boot_id or _new_boot_id()}.log"


def _point_active_daemon_log(life_dir: Path, target: Path) -> None:
    """(Re)point ``<life_dir>/daemon.log`` at the active boot's log file so every
    existing reader / ``tail`` / ``--status`` keeps resolving the live log. A
    pre-existing legacy regular ``daemon.log`` is preserved (renamed aside), not
    clobbered. Best-effort — never breaks daemon startup."""
    link = life_dir / "daemon.log"
    try:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.rename(life_dir / "daemon.log.pre-segment")
        os.symlink(os.path.relpath(target, life_dir), link)
    except OSError:
        log.debug("could not point daemon.log -> %s", target, exc_info=True)


def _redirect_std_to_log(log_path: Path, *, keep_console: bool = False) -> int | None:
    """dup2 stdout+stderr to ``log_path`` (append) so ALL output — Python logs and
    codex subprocess output — lands in the per-boot log. Returns a saved copy of
    the original stderr fd when ``keep_console`` (so the caller can still tee
    Python logs to the terminal / journald), else None."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    saved = os.dup(2) if keep_console else None
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, sys.stdout.fileno())
    os.dup2(fd, sys.stderr.fileno())
    os.close(fd)
    return saved


def _daemon_status_payload(config: Any, *, started_at_iso: str) -> dict[str, Any]:
    # Report the RUNNER backend (what actually executes role turns — codex /
    # claude / copilot), not the life-orchestration backend. Otherwise a
    # copilot-backed run would mislabel itself "codex" in every UI. Resolved the
    # same way the role config is (env → persisted → codex), so it matches the
    # roles panel.
    try:
        from ..agent_cli.runner_backend import normalize_runner_backend
        from ..core.knobs import resolve_role_backend

        backend = normalize_runner_backend(resolve_role_backend("engineer"))
    except Exception:  # noqa: BLE001
        backend = config.backend
    from .protocol import daemon_protocol_metadata

    return {
        "pid": os.getpid(),
        "started_at_iso": started_at_iso,
        "backend": backend,
        "life_dir": str(config.life_dir),
        "per_mission_cap_usd": config.per_mission_cap_usd,
        "daily_cap_usd": config.daily_cap_usd,
        "global_daily_cap_usd": config.global_daily_cap_usd,
        **daemon_protocol_metadata(),
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
    global_daily_cap_usd: float | None = None
    protocol_name: str = ""
    protocol_major: int | None = None
    protocol_minor: int | None = None
    capabilities: tuple[str, ...] = ()
    runtime: dict[str, Any] | None = None
    status_read_error: str = ""
    pid_path: Path | None = None


def _daemon_budget_from_env() -> LifeBudget:
    from ..core.knobs import resolve_budget_caps

    budget = resolve_budget_caps()

    return LifeBudget(
        per_mission_cap_usd=budget.per_mission_cap_usd,
        daily_cap_usd=budget.daily_cap_usd,
        global_daily_cap_usd=budget.global_daily_cap_usd,
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
    global_daily = getattr(status, "global_daily_cap_usd", None)
    try:
        if alive and per_mission is not None and daily is not None:
            return LifeBudget(
                per_mission_cap_usd=float(per_mission),
                daily_cap_usd=float(daily),
                global_daily_cap_usd=float(global_daily or 0.0),
            )
    except (TypeError, ValueError):
        pass
    return _daemon_budget_from_env()


def _status_global_root(status: Any | None) -> Path | None:
    life_dir = getattr(status, "life_dir", None)
    if life_dir is None:
        return None
    try:
        path = Path(life_dir).expanduser()
    except TypeError:
        return None
    parent = path.parent
    if parent.name != "projects":
        return None
    return parent.parent


def format_budget_status(
    journal: Any,
    *,
    status: Any | None = None,
    global_spend_fn: Any = None,
) -> str:
    budget = resolve_effective_budget(status)
    remaining = budget.remaining_today(journal)
    global_root = _status_global_root(status)
    spend_fn = global_spend_fn or global_daily_spend
    if spend_fn is _GLOBAL_DAILY_SPEND_IMPL:
        global_usage = global_daily_usage_summary(
            global_root=global_root,
            now=time.time(),
        )
        global_cost_text = format_usage_cost(global_usage)
    else:
        global_spend = spend_fn(global_root=global_root, now=time.time())
        global_cost_text = f"${global_spend:.2f}"
    tail = " (paused)" if remaining <= 0 else ""
    return (
        "budget   : "
        f"per-mission ${budget.per_mission_cap_usd:.2f} · "
        f"daily ${budget.daily_cap_usd:.2f} · "
        f"global daily ${budget.global_daily_cap_usd:.2f} "
        f"(spent {global_cost_text}) · "
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
    global_daily_cap_usd: float | None = None
    protocol_name = ""
    protocol_major: int | None = None
    protocol_minor: int | None = None
    capabilities: tuple[str, ...] = ()
    runtime: dict[str, Any] | None = None
    status_read_error = ""
    uptime: float | None = None
    sidecar = _daemon_status_path(life_dir)
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            started_iso = data.get("started_at_iso")
            backend = data.get("backend")
            raw_per_mission = data.get("per_mission_cap_usd")
            raw_daily = data.get("daily_cap_usd")
            raw_global_daily = data.get("global_daily_cap_usd")
            if raw_per_mission is not None:
                per_mission_cap_usd = float(raw_per_mission)
            if raw_daily is not None:
                daily_cap_usd = float(raw_daily)
            if raw_global_daily is not None:
                global_daily_cap_usd = float(raw_global_daily)
            protocol = data.get("protocol")
            if isinstance(protocol, dict):
                protocol_name = str(protocol.get("name") or "")
                raw_major = protocol.get("major")
                raw_minor = protocol.get("minor")
                protocol_major = int(raw_major) if raw_major is not None else None
                protocol_minor = int(raw_minor) if raw_minor is not None else None
            raw_capabilities = data.get("capabilities")
            if isinstance(raw_capabilities, list):
                capabilities = tuple(
                    str(item) for item in raw_capabilities if isinstance(item, str)
                )
            raw_runtime = data.get("runtime")
            if isinstance(raw_runtime, dict):
                runtime = dict(raw_runtime)
            if started_iso:
                started_dt = datetime.fromisoformat(started_iso)
                uptime = (datetime.now(timezone.utc) - started_dt).total_seconds()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            status_read_error = f"{type(exc).__name__}: {exc}"[:500]
    return DaemonStatus(
        alive=alive,
        pid=pid if alive else None,
        started_at_iso=started_iso,
        uptime_seconds=uptime,
        life_dir=life_dir,
        backend=backend,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        global_daily_cap_usd=global_daily_cap_usd,
        protocol_name=protocol_name,
        protocol_major=protocol_major,
        protocol_minor=protocol_minor,
        capabilities=capabilities,
        runtime=runtime,
        status_read_error=status_read_error,
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


def stop_daemon(
    life_dir: Path | None = None,
    *,
    timeout: float = 10.0,
    drain: bool = False,
    drain_timeout: float = 1800.0,
    force: bool = False,
) -> int:
    """Stop the running daemon.

    Default (fast SIGTERM): send SIGTERM and wait ``timeout`` (10s) for exit. A
    daemon that is mid-mission will NOT exit in 10s — the supervisor only checks
    its stop flag *between* missions, and the engineer round loop runs to a
    natural boundary — so this returns 2 and (unless ``force``) tells the
    operator to drain or escalate rather than silently leaving the daemon up.

    Drain (``drain=True``): quiesce continuous mode FIRST (so no NEW mission
    starts after the current one), persist a PID-bound drain marker, then send
    SIGTERM. The worker uses that marker to set only the supervisor boundary-stop
    event, not the backend interrupt event, so the CURRENT mission reaches its
    natural reviewed boundary before exit. There is no mid-mission SIGKILL.

    ``force``: if the daemon is still alive when the wait elapses, escalate to
    SIGKILL (which DOES interrupt a running mission) instead of returning 2.

    Returns 0 on graceful stop, 1 if no daemon was running, 2 on timeout.
    """
    status = read_daemon_status(life_dir)
    if not status.alive or status.pid is None:
        sys.stderr.write("argus-skill: no daemon is running for this life-dir.\n")
        return 1
    pid = status.pid
    resolved_dir = status.life_dir

    if drain:
        # Stop NEW missions from starting after the current one finishes,
        # preserving the objective so the operator can resume later. The daemon
        # hot-reloads continuous.json, so this lands without a restart.
        try:
            disable_continuous_config(
                resolved_dir,
                done_reason="operator drain-stop",
            )
        except Exception:  # noqa: BLE001 — quiesce is best-effort
            pass
        try:
            request_daemon_drain(resolved_dir, pid=pid)
        except OSError as exc:
            sys.stderr.write(
                f"argus-skill: failed to persist drain request: {exc}\n"
            )
            return 2
        sys.stdout.write(
            f"argus-skill: draining daemon (pid {pid}) — quiesced continuous mode; "
            "waiting for the current mission to finish at its natural boundary "
            "(no mid-mission SIGKILL)...\n"
        )
        sys.stdout.flush()

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        if drain:
            clear_daemon_drain_request(resolved_dir, pid=pid)
        return 1

    wait_for = drain_timeout if drain else timeout
    deadline = time.monotonic() + wait_for
    next_heartbeat = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        if drain and time.monotonic() >= next_heartbeat:
            elapsed = int(wait_for - (deadline - time.monotonic()))
            sys.stdout.write(
                f"argus-skill: draining... still finishing current mission "
                f"({elapsed}s elapsed).\n"
            )
            sys.stdout.flush()
            next_heartbeat += 30.0
        time.sleep(0.2)

    if force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        if drain:
            clear_daemon_drain_request(resolved_dir, pid=pid)
        sys.stderr.write(
            f"argus-skill: daemon (pid {pid}) did not exit within {wait_for:.0f}s; "
            "sent SIGKILL (--force).\n"
        )
        return 0
    if drain:
        sys.stderr.write(
            f"argus-skill: daemon (pid {pid}) is still finishing its mission after "
            f"{wait_for:.0f}s. It will exit on its own at the next boundary; re-run "
            "with --force to SIGKILL now (interrupts the mission).\n"
        )
    else:
        sys.stderr.write(
            f"argus-skill: daemon (pid {pid}) did not exit within {timeout:.1f}s "
            "(it is mid-mission). Re-run with --drain to wait for a clean boundary, "
            "or --force to SIGKILL now.\n"
        )
    return 2

__all__ = [
    "ContinuousConfigState", "DaemonStatus",
    "continuous_mode_error", "format_budget_status",
    "read_continuous_config", "read_continuous_state",
    "read_daemon_status", "resolve_effective_budget",
    "stop_daemon", "wait_for_daemon_status", "write_continuous_config",
    "_daemon_log_path", "_daemon_pid_path", "_daemon_status_path",
    "_daemon_status_payload", "_new_boot_id", "_point_active_daemon_log",
    "_process_alive", "_redirect_std_to_log",
]
