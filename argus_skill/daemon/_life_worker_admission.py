"""Daemon admission: host-wide daemon caps, workspace-lease exclusivity, and
process spawn (double-fork / clean-launch / foreground / handoff-child).

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. ``LifeWorker`` stays defined in the
facade module (``life_worker.py``), which imports this module at the top
level, so every reference to ``LifeWorker`` as a runtime value (rather than
a type hint) here uses a call-time lazy import to avoid a circular import.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import portalocker

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - detached daemon is POSIX-only
    _fcntl = None

from ..agent_cli._process_control import windows_hidden_subprocess_kwargs
from ..core import paths as core_paths
from .config import LifeWorkerConfig
from .config import config_payload as _config_payload
from .handoff import (
    _acquire_daemon_lock_with_timeout as _acquire_daemon_lock_with_timeout_impl,
)
from .handoff import run_handoff_child_process
from .process import (
    _WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS,
    run_foreground_process,
    spawn_detached_process,
)

log = logging.getLogger(__name__)

_CLEAN_LAUNCH_TIMEOUT_SECONDS = 15.0
_CLEAN_LAUNCH_WINDOWS_MARGIN_SECONDS = 15.0
_SPAWN_ERROR_MAX_CHARS = 8_000


def run_handoff_child() -> int:
    # Lazy proxy: ``LifeWorker`` stays defined in the facade module, and the
    # facade imports this module at top level, so a top-level import here
    # would be circular. ``_acquire_daemon_lock_with_timeout`` is resolved
    # the same way so `monkeypatch.setattr(life_worker,
    # "_acquire_daemon_lock_with_timeout", ...)` still takes effect even
    # though this function now lives here. Both resolved at call time.
    from .life_worker import LifeWorker, _acquire_daemon_lock_with_timeout

    return run_handoff_child_process(
        worker_factory=LifeWorker,
        acquire_lock=_acquire_daemon_lock_with_timeout,
    )


def _acquire_daemon_lock_with_timeout(pid_path: Path, timeout: float) -> Any:
    # Lazy proxy: resolve ``acquire_global_daemon_lock`` through the facade
    # module's own namespace at call time so
    # `monkeypatch.setattr(life_worker, "acquire_global_daemon_lock", ...)`
    # still takes effect even though this function now lives here.
    from .life_worker import acquire_global_daemon_lock

    return _acquire_daemon_lock_with_timeout_impl(
        pid_path,
        timeout,
        acquire_fn=acquire_global_daemon_lock,
    )



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
    projects = core_paths.session_states_root(root)
    try:
        candidates = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        return None
    own_life_dir = Path(config.life_dir).expanduser().resolve()
    from ..core.session import read_session_meta, resolve_session_workdir

    # Lazy proxy: resolve ``read_daemon_status`` through the facade module's
    # own namespace at call time so `monkeypatch.setattr(life_worker,
    # "read_daemon_status", ...)` still takes effect even though this
    # function now lives here.
    from .life_worker import read_daemon_status

    for life_dir in candidates:
        try:
            if life_dir.resolve() == own_life_dir:
                continue
            status = read_daemon_status(life_dir)
            if not status.alive:
                continue
            if status.project_workdir:
                owner_base = Path(status.project_workdir).expanduser().resolve(
                    strict=True
                )
            else:
                meta = read_session_meta(root, life_dir.name)
                owner_base = resolve_session_workdir(meta, state_dir=life_dir)
            from ..core.campaign_workdir import active_campaign_workdir

            owner_workdir = (
                active_campaign_workdir(life_dir, owner_base) or owner_base
            )
            overlaps = (
                owner_workdir == target
                or owner_workdir in target.parents
                or target in owner_workdir.parents
            )
            if overlaps:
                return {
                    "sid": life_dir.name,
                    "pid": status.pid,
                    "workdir": str(owner_workdir),
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
    if not configured.is_dir():
        return f"configured workdir is not a directory: {configured}"
    root = _daemon_global_root(config)
    from ..core.session import read_session_meta, resolve_session_workdir

    # Lazy proxy: see ``_active_workspace_owner`` above for why this cannot
    # be a top-level import.
    from .life_worker import read_daemon_status

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
    else:
        prior_status = read_daemon_status(config.life_dir)
        prior_raw = str(prior_status.project_workdir or "").strip()
        if prior_raw:
            try:
                prior = Path(prior_raw).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                return (
                    "legacy session's previous workdir is unavailable; "
                    f"restore or explicitly migrate {prior_raw}"
                )
            if prior != configured:
                return (
                    "legacy session workdir changed during daemon startup; "
                    f"persist or retry with {prior}"
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
    # Lazy proxy: see ``_active_workspace_owner`` above for why this cannot
    # be a top-level import.
    from .life_worker import read_daemon_status

    projects = core_paths.session_states_root(_daemon_global_root(config))
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
    root = _daemon_global_root(config)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(root / "daemon-spawn.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        else:
            portalocker.lock(fd, portalocker.LOCK_EX)
    except (OSError, portalocker.exceptions.LockException):
        os.close(fd)
        raise
    return fd


def _release_daemon_spawn_lock(fd: int | None, *, unlock: bool = True) -> None:
    if fd is None:
        return
    if unlock:
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            else:
                portalocker.unlock(fd)
        except (OSError, portalocker.exceptions.LockException):
            pass
    try:
        os.close(fd)
    except OSError:
        pass


def spawn_detached_daemon(config: LifeWorkerConfig, *, quiet: bool = False) -> int:
    # Lazy import: see ``run_handoff_child`` above for why this cannot be a
    # top-level import.
    from .life_worker import LifeWorker

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


def _clean_launch_timeout_seconds() -> float:
    if os.name == "nt":
        # The helper synchronously waits for the Windows worker to publish its
        # pid/status.  Its parent must outlive that inner contract.
        return (
            _WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS
            + _CLEAN_LAUNCH_WINDOWS_MARGIN_SECONDS
        )
    return _CLEAN_LAUNCH_TIMEOUT_SECONDS


def _bounded_spawn_error(detail: str) -> str:
    cleaned = detail.strip()
    if len(cleaned) <= _SPAWN_ERROR_MAX_CHARS:
        return cleaned
    return "[earlier launch output truncated]\n" + cleaned[-_SPAWN_ERROR_MAX_CHARS:]


def _record_spawn_error(config: LifeWorkerConfig, detail: str) -> str:
    bounded = _bounded_spawn_error(detail)
    config.last_spawn_error = bounded
    if bounded:
        log.error("clean daemon launcher failed: %s", bounded)
    return bounded


def _spawn_output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _latest_daemon_log_tail(
    config: LifeWorkerConfig,
    *,
    started_at: float,
) -> str:
    """Read only a log touched by this launch attempt, including on Windows.

    Creating the stable ``daemon.log`` symlink can be unavailable without
    Developer Mode, so inspect the per-boot directory as the authoritative
    fallback.
    """
    candidates: list[Path] = []
    if config.log_path is not None:
        candidates.append(Path(config.log_path))
    life_dir = Path(config.life_dir)
    candidates.append(life_dir / "daemon.log")
    try:
        candidates.extend((life_dir / "daemons").glob("boot-*.log"))
    except OSError:
        pass

    freshest: tuple[float, Path] | None = None
    for candidate in candidates:
        try:
            modified = candidate.stat().st_mtime
        except OSError:
            continue
        if modified < started_at - 2.0:
            continue
        if freshest is None or modified > freshest[0]:
            freshest = (modified, candidate)
    if freshest is None:
        return ""
    try:
        text = freshest[1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _bounded_spawn_error(text[-4_000:])


def _clean_spawn_preflight(config: LifeWorkerConfig) -> tuple[int, str]:
    executable = str(sys.executable or "").strip()
    if not executable:
        return 2, "Python interpreter is unavailable: sys.executable is empty"
    try:
        executable_path = Path(executable).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return 2, f"Python interpreter is unavailable ({executable}): {exc}"
    if not executable_path.is_file():
        return 2, f"Python interpreter is not a file: {executable_path}"

    if config.project_workdir is None:
        return 0, ""
    try:
        workdir = Path(config.project_workdir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return 3, f"configured workdir is unavailable: {exc}"
    if not workdir.is_dir():
        return 3, f"configured workdir is not a directory: {workdir}"
    return 0, ""


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
    config.last_spawn_error = ""
    preflight_rc, preflight_error = _clean_spawn_preflight(config)
    if preflight_error:
        detail = _record_spawn_error(config, preflight_error)
        if not quiet:
            sys.stderr.write(f"argus-skill: {detail}.\n")
        return preflight_rc
    env = os.environ.copy()
    env["ARGUS_BINARY_MODE"] = "cli"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # The helper is framework control-plane code, not project code. Starting it
    # in the project workspace lets a generated ``argus_skill/`` package or
    # ``sitecustomize.py`` shadow the running Argus release during an upgrade
    # restart. Pin both cwd and PYTHONPATH to the package root that loaded this
    # WebAPI process; the daemon receives ``project_workdir`` in its payload and
    # exposes it to agents only after control-plane boot succeeds.
    import_root = Path(__file__).resolve().parents[2]
    pythonpath = [
        str(import_root),
        *[
            part
            for part in env.get("PYTHONPATH", "").split(os.pathsep)
            if part and Path(part).expanduser().resolve() != import_root
        ],
    ]
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["PYTHONSAFEPATH"] = "1"
    started_at = time.time()
    timeout_s = _clean_launch_timeout_seconds()
    try:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "argus_skill.daemon.spawn_helper"],
            input=json.dumps(_config_payload(config)),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(import_root),
            env=env,
            close_fds=True,
            # The source-runtime fallback can be launched from Tauri dev mode;
            # its short-lived spawn helper must be as invisible as the frozen
            # daemon worker.
            **windows_hidden_subprocess_kwargs(),
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = _spawn_output_text(exc.stderr or exc.stdout).strip()
        detail = (
            f"clean daemon launcher timed out after {timeout_s:g}s"
            + (f": {output}" if output else "")
        )
        detail = _record_spawn_error(config, detail)
        if not quiet:
            sys.stderr.write(f"argus-skill: {detail}\n")
        return 2
    except OSError as exc:
        detail = _record_spawn_error(
            config,
            f"could not start Python interpreter {sys.executable}: "
            f"{type(exc).__name__}: {exc}",
        )
        if not quiet:
            sys.stderr.write(f"argus-skill: {detail}\n")
        return 2
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        log_tail = _latest_daemon_log_tail(config, started_at=started_at)
        if log_tail and log_tail not in detail:
            detail = f"{detail}\nDaemon log:\n{log_tail}" if detail else log_tail
        if not detail:
            detail = (
                f"daemon spawn helper exited with rc={completed.returncode} "
                "without diagnostic output"
            )
        detail = _record_spawn_error(config, detail)
        if not quiet:
            sys.stderr.write(detail + "\n")
    return int(completed.returncode)


def _launcher_failure_message(detail: str, returncode: int) -> str:
    """Summarize the helper's stderr without truncating an actionable refusal.

    This used to keep only the last non-empty line. That is the right rule for
    a traceback, where the last line is the exception, and exactly the wrong
    rule for an admission refusal: the workspace-lease message is deliberately
    multi-line (owning pid, session, project, then the three ways out), and
    collapsing it left the operator with "- or start this objective in a
    different directory" and no idea what was holding the directory.

    So anchor on the framework's own ``argus-skill:`` prefix when it is there
    and keep that message whole, and fall back to the last-line rule only for
    output the framework did not format — which in practice means a crash.
    """
    lines = detail.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("argus-skill:")
    ]
    if starts:
        return "\n".join(lines[starts[-1]:]).strip()
    return next(
        (line.strip() for line in reversed(lines) if line.strip()),
        f"clean daemon launcher exited with code {returncode}",
    )


def run_foreground(config: LifeWorkerConfig) -> int:
    # Lazy import: see ``run_handoff_child`` in ``_life_worker_admission.py``
    # (this module) for why this cannot be a top-level import.
    from .life_worker import LifeWorker

    return run_foreground_process(
        config,
        worker_factory=LifeWorker,
        workspace_start_error=_workspace_start_error,
        acquire_workspace_lease=_acquire_daemon_workspace_lease,
        release_workspace_lease=_release_daemon_workspace_lease,
    )
