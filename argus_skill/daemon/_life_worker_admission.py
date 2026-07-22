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
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - detached daemon is POSIX-only
    _fcntl = None

from ..core import paths as core_paths
from .config import LifeWorkerConfig
from .config import config_payload as _config_payload
from .handoff import (
    _acquire_daemon_lock_with_timeout as _acquire_daemon_lock_with_timeout_impl,
)
from .handoff import run_handoff_child_process
from .process import run_foreground_process, spawn_detached_process

log = logging.getLogger(__name__)


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
