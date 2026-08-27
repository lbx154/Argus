"""Daemon continuous configuration, status sidecar, logs, and stop control."""

from __future__ import annotations

import errno
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.daemon_lock import WINDOWS_DAEMON_LOCK_OFFSET, is_pid_running
from ..core.usage import format_usage_cost
from ..life.supervisor import LifeBudget, global_daily_spend, global_daily_usage_summary

# Stopping the process is not ending the campaign. Both reasons below mean an
# operator halted this daemon -- to drain it, or to restart it onto new code --
# so ``--resume-continuous`` re-arms either one. Every other disabled reason
# (planner-declared completion, an operator hold) is a statement about the work
# and stays authoritative. These live together because they drifted apart once:
# only the drain reason was resumable, so every SIGTERM restart silently retired
# the campaign while leaving a healthy-looking daemon behind.
DRAIN_STOP_REASON = "operator drain-stop"
GRACEFUL_STOP_REASON = "operator stop (graceful SIGTERM/SIGINT — clock out)"
RESUMABLE_STOP_REASONS = frozenset({DRAIN_STOP_REASON, GRACEFUL_STOP_REASON})

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
_GLOBAL_DAILY_SPEND_IMPL = global_daily_spend
_TEST_ALLOW_MEMORY_CONTINUOUS_ENV = "ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS"
_DRAIN_REQUEST_FILE = "daemon.drain-request.json"
_STOP_REQUEST_FILE = "daemon.stop-request.json"
DAEMON_UPGRADE_REQUEST_FILE = "daemon.upgrade-request.json"
_WINDOWS_LOCK_POLL_SECONDS = 0.05
_CONTINUOUS_RESERVE_NAMES = (
    ".continuous.reserve",
    ".continuous.reserve.spare",
)
_CONTINUOUS_RESERVE_MIN_BYTES = 64 * 1024
_CONTINUOUS_RESERVE_MAX_BYTES = 1024 * 1024


class ContinuousConfigWriteAfterReplaceError(RuntimeError):
    """Raised when continuous.json was replaced but durability failed."""


class ContinuousConfigCommitError(RuntimeError):
    """Raised when a durable pre-replace callback prevents a false CAS result."""


class _AtomicWritePostReplaceError(OSError):
    """Internal marker for an error observed after the target was replaced."""


class _AtomicWriteAfterCallbackError(OSError):
    """Internal marker for a replace failure after the callback committed."""


def _truthy_env(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class ContinuousConfigState:
    enabled: bool = False
    objective: str = ""
    open_ended: bool = True
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


def _daemon_stop_request_path(life_dir: Path) -> Path:
    return life_dir / _STOP_REQUEST_FILE


@dataclass(frozen=True)
class DaemonStopRequest:
    """One exact daemon instance's out-of-band stop request."""

    pid: int
    started_at_iso: str
    drain: bool
    requested_at: float


def request_daemon_control_stop(
    life_dir: Path,
    *,
    pid: int,
    started_at_iso: str,
    drain: bool,
) -> None:
    """Persist a stop request consumable without Windows console signals.

    Both PID and the daemon boot timestamp are required.  A stale request can
    therefore never stop a later daemon after Windows reuses the numeric PID.
    """
    started = str(started_at_iso or "").strip()
    if int(pid) <= 0 or not started:
        raise ValueError("daemon control stop requires an exact process identity")
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _daemon_stop_request_path(life_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": int(pid),
                "started_at_iso": started,
                "drain": bool(drain),
                "requested_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    os.replace(str(tmp), str(path))


def read_daemon_control_stop(
    life_dir: Path,
    *,
    pid: int,
    started_at_iso: str,
) -> DaemonStopRequest | None:
    """Return a request only when it targets this exact daemon boot."""
    try:
        payload = json.loads(
            _daemon_stop_request_path(life_dir).read_text(encoding="utf-8")
        )
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        requested_pid = int(payload.get("pid") or 0)
        requested_started = str(payload.get("started_at_iso") or "").strip()
        if requested_pid != int(pid) or requested_started != str(started_at_iso or ""):
            return None
        return DaemonStopRequest(
            pid=requested_pid,
            started_at_iso=requested_started,
            drain=bool(payload.get("drain")),
            requested_at=float(payload.get("requested_at") or 0.0),
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def clear_daemon_control_stop(
    life_dir: Path,
    *,
    pid: int,
    started_at_iso: str,
) -> None:
    """Remove only the request still owned by this exact daemon boot."""
    if read_daemon_control_stop(
        life_dir,
        pid=pid,
        started_at_iso=started_at_iso,
    ) is None:
        return
    try:
        _daemon_stop_request_path(life_dir).unlink()
    except FileNotFoundError:
        pass


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


def daemon_drain_pid(life_dir: Path) -> int | None:
    """Return the PID still owning a persisted graceful-drain request."""
    try:
        payload = json.loads(
            _daemon_drain_request_path(life_dir).read_text(encoding="utf-8")
        )
        pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
        return pid if pid > 0 else None
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


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
        fd = handle.fileno()
        if os.name == "nt":
            if msvcrt is None:  # pragma: no cover - broken Windows runtime
                raise RuntimeError(
                    "continuous config locking requires msvcrt on Windows"
                )
            try:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
            except OSError:
                pass
        if os.name == "nt":
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(_WINDOWS_LOCK_POLL_SECONDS)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)


def _is_quota_error(exc: OSError) -> bool:
    return getattr(exc, "errno", None) in {errno.ENOSPC, errno.EDQUOT}


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_existing_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError:
        return None


def _atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        before_bytes = _read_existing_bytes(path)
        callback_committed = False
        if before_replace is not None:
            try:
                before_replace()
            except OSError as exc:
                raise ContinuousConfigCommitError(
                    f"continuous config precommit failed for {path}"
                ) from exc
            callback_committed = True
        try:
            os.replace(str(tmp), str(path))
        except OSError as exc:
            after_replace = _read_existing_bytes(path)
            if after_replace == data or (
                before_bytes is not None and after_replace != before_bytes
            ):
                raise _AtomicWritePostReplaceError(str(exc)) from exc
            if callback_committed:
                raise _AtomicWriteAfterCallbackError(str(exc)) from exc
            raise
        try:
            _fsync_directory(path.parent)
        except OSError as exc:
            raise _AtomicWritePostReplaceError(str(exc)) from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _atomic_write_text(
    path: Path,
    text: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    _atomic_write_bytes(
        path,
        text.encode("utf-8"),
        before_replace=before_replace,
    )


def _continuous_reserve_paths(life_dir: Path) -> tuple[Path, ...]:
    return tuple(life_dir / name for name in _CONTINUOUS_RESERVE_NAMES)


def _continuous_reserve_size(text: str) -> int:
    needed = len(text.encode("utf-8")) + 4096
    return max(
        _CONTINUOUS_RESERVE_MIN_BYTES,
        min(_CONTINUOUS_RESERVE_MAX_BYTES, needed),
    )


def _continuous_state_reserve_text(state: ContinuousConfigState) -> str:
    data = {
        "enabled": state.enabled,
        "objective": state.objective,
        "open_ended": state.open_ended,
        "generation": state.generation,
    }
    if state.done_reason:
        data["done_reason"] = state.done_reason
        data["done_at"] = state.done_at
    return json.dumps(data, ensure_ascii=False, indent=2)


def _ensure_continuous_reserve_unlocked(life_dir: Path, text: str) -> None:
    size = _continuous_reserve_size(text)
    payload = b"\0" * size
    for reserve in _continuous_reserve_paths(life_dir):
        try:
            if reserve.stat().st_size >= size:
                continue
        except OSError:
            pass
        try:
            _atomic_write_bytes(reserve, payload)
        except _AtomicWritePostReplaceError:
            pass
        except OSError:
            log.debug("failed to refresh continuous config reserve %s", reserve)


def _release_one_continuous_reserve_unlocked(life_dir: Path) -> bool:
    reserves = []
    for reserve in _continuous_reserve_paths(life_dir):
        try:
            reserves.append((reserve.stat().st_size, reserve))
        except OSError:
            continue
    for _size, reserve in sorted(reserves, key=lambda item: item[0], reverse=True):
        try:
            reserve.unlink()
        except OSError:
            continue
        try:
            _fsync_directory(life_dir)
        except OSError:
            pass
        return True
    return False


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
            open_ended=bool(data.get("open_ended", True)),
            done_reason=_text(data.get("done_reason", "")),
            done_at=_text(data.get("done_at", "")),
            generation=max(0, int(data.get("generation", 0) or 0)),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ContinuousConfigState()


def read_continuous_state(life_dir: Path) -> ContinuousConfigState:
    with _continuous_config_lock(life_dir):
        state = _read_continuous_state_unlocked(life_dir)
        if _continuous_config_path(life_dir).exists():
            _ensure_continuous_reserve_unlocked(
                life_dir,
                _continuous_state_reserve_text(state),
            )
        return state


def read_continuous_config(life_dir: Path) -> tuple[bool, str]:
    state = read_continuous_state(life_dir)
    return state.enabled, state.objective


def write_continuous_config(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    open_ended: bool | None = None,
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
            open_ended=(current.open_ended if open_ended is None else open_ended),
            done_reason=done_reason,
            generation=current.generation + 1,
        )


def _write_continuous_config_unlocked(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    open_ended: bool,
    done_reason: str = "",
    done_at: str = "",
    generation: int,
    before_replace: Callable[[], None] | None = None,
) -> bool:
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _continuous_config_path(life_dir)
    data = {
        "enabled": enabled,
        "objective": objective,
        "open_ended": bool(open_ended),
        "generation": max(0, int(generation)),
    }
    if done_reason:
        data["done_reason"] = done_reason
        data["done_at"] = done_at or datetime.now(timezone.utc).isoformat()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path.exists():
        _ensure_continuous_reserve_unlocked(life_dir, text)

    def _write_once() -> None:
        if before_replace is None:
            _atomic_write_text(path, text)
        else:
            _atomic_write_text(
                path,
                text,
                before_replace=before_replace,
            )

    try:
        _write_once()
    except _AtomicWritePostReplaceError as exc:
        raise ContinuousConfigWriteAfterReplaceError(
            f"continuous config was replaced but durability failed for {path}"
        ) from exc
    except _AtomicWriteAfterCallbackError as exc:
        raise ContinuousConfigCommitError(
            f"continuous config replace failed after precommit for {path}"
        ) from exc
    except OSError as exc:
        if _is_quota_error(exc) and _release_one_continuous_reserve_unlocked(life_dir):
            try:
                _write_once()
            except _AtomicWritePostReplaceError as retry_exc:
                raise ContinuousConfigWriteAfterReplaceError(
                    f"continuous config was replaced but durability failed for {path}"
                ) from retry_exc
            except _AtomicWriteAfterCallbackError as retry_exc:
                raise ContinuousConfigCommitError(
                    f"continuous config replace failed after precommit for {path}"
                ) from retry_exc
            except OSError:
                log.warning("failed to write continuous config to %s", path)
                return False
            _ensure_continuous_reserve_unlocked(life_dir, text)
            return True
        log.warning("failed to write continuous config to %s", path)
        return False
    else:
        _ensure_continuous_reserve_unlocked(life_dir, text)
        return True


def compare_and_swap_continuous_config(
    life_dir: Path,
    *,
    expected: ContinuousConfigState,
    enabled: bool,
    objective: str,
    open_ended: bool | None = None,
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
        return _write_continuous_config_unlocked(
            life_dir,
            enabled=enabled,
            objective=objective,
            open_ended=(current.open_ended if open_ended is None else open_ended),
            done_reason=done_reason,
            generation=current.generation + 1,
            before_replace=before_write,
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
            open_ended=current.open_ended,
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
        and left.open_ended == right.open_ended
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
    alias (:func:`_point_active_daemon_log`) exposes the current boot for
    back-compat readers / ``tail`` / ``--status``. Identity stays per-PROJECT (one
    daemon per life_dir) — this only segments that one daemon's log by boot."""
    if override is not None:
        return override
    return life_dir / "daemons" / f"boot-{boot_id or _new_boot_id()}.log"


def _point_active_daemon_log(life_dir: Path, target: Path) -> None:
    """Expose the active boot through stable ``<life_dir>/daemon.log``.

    POSIX uses a relative symlink. Windows uses a no-privilege NTFS hard link,
    so existing readers / ``tail`` / ``--status`` still see a normal live file.
    A pre-existing legacy regular ``daemon.log`` is preserved (renamed aside), not
    clobbered. Best-effort — never breaks daemon startup."""
    link = life_dir / "daemon.log"
    try:
        if os.path.normcase(str(link.absolute())) == os.path.normcase(
            str(target.absolute())
        ):
            return
    except OSError:
        pass
    if os.name == "nt":
        _point_active_daemon_log_windows(life_dir, target, link)
        return
    try:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.rename(life_dir / "daemon.log.pre-segment")
        os.symlink(os.path.relpath(target, life_dir), link)
    except OSError:
        log.debug("could not point daemon.log -> %s", target, exc_info=True)


def _windows_managed_log_alias(life_dir: Path, link: Path) -> bool:
    """Whether a regular ``daemon.log`` is one of our per-boot hard links."""
    try:
        candidates = (life_dir / "daemons").glob("boot-*.log")
    except OSError:
        return False
    for candidate in candidates:
        try:
            if candidate.is_file() and link.samefile(candidate):
                return True
        except OSError:
            continue
    return False


def _point_active_daemon_log_windows(
    life_dir: Path,
    target: Path,
    link: Path,
) -> None:
    """Install a no-privilege stable hard link to the active Windows boot log."""
    temporary = life_dir / f".daemon.log.link-{os.getpid()}-{uuid.uuid4().hex}.tmp"
    try:
        life_dir.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        # A hard link needs an existing source. The daemon subsequently opens
        # this same inode in append mode, so daemon.log observes every write.
        target.touch(exist_ok=True)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            try:
                if link.samefile(target):
                    return
            except OSError:
                pass
            if not _windows_managed_log_alias(life_dir, link):
                legacy = life_dir / "daemon.log.pre-segment"
                if not legacy.exists():
                    link.rename(legacy)
                else:
                    link.rename(
                        life_dir
                        / f"daemon.log.pre-segment-{int(time.time())}-{uuid.uuid4().hex[:6]}"
                    )
        os.link(target, temporary)
        os.replace(temporary, link)
    except OSError:
        log.debug("could not hard-link daemon.log -> %s", target, exc_info=True)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.debug("could not clean temporary daemon log link %s", temporary)


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
        from ..agent_cli.runner_backend import resolve_available_runner
        from ..core.knobs import resolve_role_backend

        # default=config.backend: this payload REPORTS the daemon's backend,
        # so when no knob overrides it the honest answer is the backend the
        # daemon was launched with — the same value the `except` below already
        # falls back to.
        requested = resolve_role_backend(
            "engineer", default=str(config.backend or "") or None
        )
        configured = (
            os.environ.get("ARGUS_SKILL_ENGINEER_RUNNER_BIN", "").strip()
            or os.environ.get("ARGUS_SKILL_RUNNER_BIN", "").strip()
        )
        backend, _runner_bin = resolve_available_runner(
            requested,
            configured or None,
        )
    except Exception:  # noqa: BLE001
        backend = config.backend
    from .protocol import daemon_protocol_metadata

    return {
        "pid": os.getpid(),
        "started_at_iso": started_at_iso,
        "backend": backend,
        "life_backend": str(config.backend or ""),
        "life_dir": str(config.life_dir),
        "project_workdir": (
            str(config.project_workdir)
            if getattr(config, "project_workdir", None) is not None
            else ""
        ),
        "global_daily_cap_usd": config.global_daily_cap_usd,
        "mission_width": int(getattr(config, "mission_width", 1)),
        **daemon_protocol_metadata(),
    }


@dataclass
class DaemonStatus:
    alive: bool
    pid: int | None
    started_at_iso: str | None
    uptime_seconds: float | None
    life_dir: Path
    project_workdir: str = ""
    backend: str | None = None
    life_backend: str | None = None
    global_daily_cap_usd: float | None = None
    mission_width: int | None = None
    protocol_name: str = ""
    protocol_major: int | None = None
    protocol_minor: int | None = None
    capabilities: tuple[str, ...] = ()
    runtime: dict[str, Any] | None = None
    status_read_error: str = ""
    pid_path: Path | None = None
    health_state: str = "unknown"
    stalled: bool = False
    last_progress_at: float | None = None
    last_progress_event: str = ""
    seconds_since_progress: float | None = None


def _daemon_budget_from_project(
    project_state_dir: Path | str | None,
    global_root: Path | str | None = None,
) -> LifeBudget:
    from ..core.knobs import resolve_budget_caps

    budget = resolve_budget_caps(
        project_state_dir=project_state_dir,
        global_root=global_root,
    )

    return LifeBudget(
        global_daily_cap_usd=budget.global_daily_cap_usd,
    )


def resolve_effective_budget(status: Any | None = None) -> LifeBudget:
    """Return the live budget caps for operator surfaces.

    When the daemon has published caps in its status sidecar, use those
    exact values. Otherwise read the project and global budget files so a
    stopped-daemon status command shows what the next launch will enforce.
    """
    alive = bool(getattr(status, "alive", False))
    global_daily = getattr(status, "global_daily_cap_usd", None)
    try:
        if alive and global_daily is not None:
            return LifeBudget(
                global_daily_cap_usd=float(global_daily or 0.0),
            )
    except (TypeError, ValueError):
        pass
    return _daemon_budget_from_project(
        getattr(status, "life_dir", None),
        _status_global_root(status),
    )


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
    global_root = _status_global_root(status)
    spend_fn = global_spend_fn or global_daily_spend
    if spend_fn is _GLOBAL_DAILY_SPEND_IMPL:
        global_usage = global_daily_usage_summary(
            global_root=global_root,
            now=time.time(),
        )
        global_spend = global_usage.known_cost_usd
        global_cost_text = format_usage_cost(global_usage)
    else:
        global_spend = spend_fn(global_root=global_root, now=time.time())
        global_cost_text = f"${global_spend:.2f}"
    if budget.global_daily_cap_usd <= 0:
        return f"budget   : global daily disabled (spent {global_cost_text})"
    remaining = max(0.0, budget.global_daily_cap_usd - global_spend)
    tail = " (paused)" if remaining <= 0 else ""
    return (
        "budget   : "
        f"global daily ${budget.global_daily_cap_usd:.2f} "
        f"(spent {global_cost_text}) · "
        f"remaining ${remaining:.2f}{tail}"
    )


def read_daemon_status(life_dir: Path | None = None) -> DaemonStatus:
    """Read the daemon's pid file and return a structured status.

    ``alive=True`` only if the recorded process exists and still holds the
    daemon pid-file lock. Checking the lock prevents a stale PID from being
    mistaken for a daemon after the OS reuses that PID for another process.
    """
    if life_dir is None:
        from ..core import paths as core_paths
        life_dir = core_paths.global_root()
    else:
        life_dir = Path(life_dir).expanduser()
    from .health import read_daemon_health

    pid_path = _daemon_pid_path(life_dir)
    draining_owner = False
    try:
        pid = int(pid_path.read_text().strip())
    except (FileNotFoundError, OSError, ValueError):
        pid = daemon_drain_pid(life_dir) or 0
        draining_owner = bool(pid and _process_alive(pid))
        if not draining_owner:
            return DaemonStatus(
                alive=False, pid=None, started_at_iso=None,
                uptime_seconds=None, life_dir=life_dir, pid_path=pid_path,
                health_state="stopped",
            )
    alive = _process_alive(pid)
    if alive and not draining_owner and _daemon_pid_lock_held(pid_path) is False:
        alive = False
    started_iso: str | None = None
    backend: str | None = None
    life_backend: str | None = None
    project_workdir = ""
    global_daily_cap_usd: float | None = None
    mission_width: int | None = None
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
            raw_status_pid = data.get("pid")
            if raw_status_pid is not None and int(raw_status_pid) != pid:
                raise ValueError(
                    f"status pid {raw_status_pid!r} does not match lock pid {pid}"
                )
            started_iso = data.get("started_at_iso")
            backend = data.get("backend")
            life_backend = data.get("life_backend")
            project_workdir = str(data.get("project_workdir") or "")
            raw_global_daily = data.get("global_daily_cap_usd")
            if raw_global_daily is not None:
                global_daily_cap_usd = float(raw_global_daily)
            raw_mission_width = data.get("mission_width")
            if raw_mission_width is not None:
                mission_width = int(raw_mission_width)
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
    health = read_daemon_health(
        life_dir,
        pid=pid if alive else None,
        alive=alive,
    )
    return DaemonStatus(
        alive=alive,
        pid=pid if alive else None,
        started_at_iso=started_iso,
        uptime_seconds=uptime,
        life_dir=life_dir,
        project_workdir=project_workdir,
        backend=backend,
        life_backend=life_backend,
        global_daily_cap_usd=global_daily_cap_usd,
        mission_width=mission_width,
        protocol_name=protocol_name,
        protocol_major=protocol_major,
        protocol_minor=protocol_minor,
        capabilities=capabilities,
        runtime=runtime,
        status_read_error=status_read_error,
        pid_path=pid_path,
        health_state=str(health["state"]),
        stalled=bool(health["stalled"]),
        last_progress_at=health["last_progress_at"],
        last_progress_event=str(health["last_progress_event"]),
        seconds_since_progress=health["seconds_since_progress"],
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
    return is_pid_running(pid)


def _windows_process_parent_pairs() -> tuple[tuple[int, int], ...]:
    """Snapshot ``(pid, parent_pid)`` pairs using the native Toolhelp API."""
    if os.name != "nt":
        return ()
    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
        process_next.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or int(snapshot) == invalid_handle:
            return ()
        pairs: list[tuple[int, int]] = []
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = bool(process_first(snapshot, ctypes.byref(entry)))
            while ok:
                pairs.append(
                    (int(entry.th32ProcessID), int(entry.th32ParentProcessID))
                )
                ok = bool(process_next(snapshot, ctypes.byref(entry)))
        finally:
            close_handle(snapshot)
        return tuple(pairs)
    except (AttributeError, OSError, TypeError, ValueError):
        log.debug("could not snapshot Windows process tree", exc_info=True)
        return ()


def _descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Return current descendants, deepest first, using the host process table.

    Provider CLIs commonly create their own process groups/sessions, so killing
    only the daemon PID does not contain a forced stop.  A snapshot of the
    parent relation is sufficient here because force-stop immediately signals
    every captured PID before killing the daemon itself.
    """
    children: dict[int, list[int]] = {}
    if os.name == "nt":
        for pid, parent in _windows_process_parent_pairs():
            if parent > 0:
                children.setdefault(parent, []).append(pid)
        entries: list[Path] = []
    else:
        try:
            entries = list(Path("/proc").iterdir())
        except OSError:
            entries = []
    if os.name != "nt" and entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue
            parent = 0
            for line in status.splitlines():
                if line.startswith("PPid:"):
                    try:
                        parent = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        parent = 0
                    break
            if parent > 0:
                children.setdefault(parent, []).append(int(entry.name))
    elif os.name != "nt":
        ps = "/bin/ps" if Path("/bin/ps").is_file() else "/usr/bin/ps"
        try:
            result = subprocess.run(
                [ps, "-axo", "pid=,ppid="],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                try:
                    pid_text, parent_text = line.split()
                    pid = int(pid_text)
                    parent = int(parent_text)
                except (TypeError, ValueError):
                    continue
                if parent > 0:
                    children.setdefault(parent, []).append(pid)

    found: list[tuple[int, int]] = []
    stack = [(int(root_pid), 0)]
    seen = {int(root_pid)}
    while stack:
        parent, depth = stack.pop()
        for child in children.get(parent, ()):
            if child in seen:
                continue
            seen.add(child)
            found.append((depth + 1, child))
            stack.append((child, depth + 1))
    found.sort(reverse=True)
    return tuple(pid for _depth, pid in found)


def _terminate_windows_process_tree(
    root_pid: int,
    *,
    identity_check: Callable[[], bool],
) -> bool:
    """Force-stop one verified Windows process and all of its descendants.

    The root handle is opened before identity is revalidated, pinning that
    process object so its PID cannot be reused under us.  Descendants are
    captured through Toolhelp, opened, and revalidated against a second tree
    snapshot before termination.  No console control event is broadcast.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        terminate_process = kernel32.TerminateProcess
        terminate_process.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate_process.restype = wintypes.BOOL
        wait_for_single = kernel32.WaitForSingleObject
        wait_for_single.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single.restype = wintypes.DWORD
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        access = 0x0001 | 0x00100000  # PROCESS_TERMINATE | SYNCHRONIZE
        root_handle = open_process(access, False, int(root_pid))
        if not root_handle:
            return not _process_alive(root_pid)
        child_handles: dict[int, Any] = {}
        unowned_descendants: set[int] = set()
        try:
            if not identity_check():
                return False

            captured = _descendant_pids(root_pid)
            for child_pid in captured:
                handle = open_process(access, False, int(child_pid))
                if handle:
                    child_handles[child_pid] = handle
                elif _process_alive(child_pid):
                    unowned_descendants.add(child_pid)

            # An exited child could have its PID reused between the Toolhelp
            # snapshot and OpenProcess.  Holding the handle pins the new object;
            # require it to remain in a fresh descendant graph before touching it.
            current_descendants = set(_descendant_pids(root_pid))
            for child_pid in tuple(child_handles):
                if child_pid in current_descendants:
                    continue
                close_handle(child_handles.pop(child_pid))

            if not identity_check():
                return False
            root_terminated = bool(terminate_process(root_handle, 1))

            for child_pid in captured:
                handle = child_handles.get(child_pid)
                if handle:
                    if not terminate_process(handle, 1):
                        unowned_descendants.add(child_pid)

            # Close the tiny spawn race between the last snapshot and root
            # termination. Creator-PID links remain queryable after parent exit.
            for _attempt in range(3):
                found_new = False
                for child_pid in _descendant_pids(root_pid):
                    if child_pid in child_handles:
                        continue
                    handle = open_process(access, False, int(child_pid))
                    if not handle:
                        if _process_alive(child_pid):
                            unowned_descendants.add(child_pid)
                        continue
                    child_handles[child_pid] = handle
                    if not terminate_process(handle, 1):
                        unowned_descendants.add(child_pid)
                    found_new = True
                if not found_new:
                    break
                time.sleep(0.02)

            root_stopped = wait_for_single(root_handle, 5_000) == 0
            children_stopped = all(
                wait_for_single(handle, 1_000) == 0
                for handle in child_handles.values()
            )
            unowned_stopped = not any(
                _process_alive(child_pid) for child_pid in unowned_descendants
            )
            return (
                (root_terminated or root_stopped)
                and root_stopped
                and children_stopped
                and unowned_stopped
            )
        finally:
            for handle in child_handles.values():
                close_handle(handle)
            close_handle(root_handle)
    except (AttributeError, OSError, TypeError, ValueError):
        log.exception("failed to terminate Windows daemon process tree pid=%s", root_pid)
        return False


def _teammate_process_group_ids(pids: Iterable[int]) -> tuple[int, ...]:
    """Return verified POSIX process groups led by Team teammate entries."""
    if os.name == "nt":
        return ()
    groups: list[int] = []
    for pid in pids:
        try:
            argv = [
                value.decode("utf-8", "replace")
                for value in Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
                if value
            ]
            pgid = os.getpgid(int(pid))
        except (OSError, ProcessLookupError, ValueError):
            continue
        if (
            "argus_skill.team.teammate_entry" in argv
            and pgid == int(pid)
            and pgid > 1
        ):
            groups.append(pgid)
    return tuple(dict.fromkeys(groups))


def _terminate_captured_descendants(pids: Iterable[int]) -> None:
    """Terminate descendants captured while they still belonged to a daemon."""
    ordered = tuple(dict.fromkeys(int(pid) for pid in pids if int(pid) > 1))
    teammate_groups = _teammate_process_group_ids(ordered)
    for group in teammate_groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    for child in ordered:
        try:
            os.kill(child, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not any(_process_alive(child) for child in ordered):
            return
        time.sleep(0.05)
    for child in ordered:
        if not _process_alive(child):
            continue
        try:
            os.kill(child, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            continue
    for group in teammate_groups:
        try:
            os.killpg(
                group,
                getattr(signal, "SIGKILL", signal.SIGTERM),
            )
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _daemon_pid_lock_held(pid_path: Path) -> bool | None:
    """Return whether another open file description holds the daemon lock.

    ``None`` means the platform or filesystem could not answer reliably; the
    caller then keeps the conservative PID-only fallback.
    """
    if os.name == "nt":
        if msvcrt is None:  # pragma: no cover - Windows safety net
            return None
        try:
            fd = os.open(str(pid_path), os.O_RDWR)
        except OSError:
            return None
        try:
            try:
                os.lseek(fd, WINDOWS_DAEMON_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            try:
                os.lseek(fd, WINDOWS_DAEMON_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return False
        finally:
            os.close(fd)
    if fcntl is None:  # pragma: no cover - safety net
        return None
    try:
        fd = os.open(str(pid_path), os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


def _same_daemon_alive(life_dir: Path, pid: int) -> bool:
    current = read_daemon_status(life_dir)
    return bool(current.alive and current.pid == pid)


def _same_daemon_instance_alive(
    life_dir: Path,
    pid: int,
    started_at_iso: str,
) -> bool:
    current = read_daemon_status(life_dir)
    return bool(
        current.alive
        and current.pid == pid
        and current.started_at_iso == started_at_iso
    )


def request_daemon_stop(life_dir: Path | None = None) -> tuple[bool, int | None]:
    """Request an immediate graceful stop without waiting for daemon exit.

    This is the non-blocking control-plane primitive used by conversational
    pause.  A PID + boot-timestamp-bound request interrupts the active mission.
    POSIX additionally sends SIGTERM for immediate wake-up; Windows relies on
    the worker's control watcher because ``os.kill(..., SIGTERM)`` there is a
    hard TerminateProcess call, not a catchable signal.
    """
    status = read_daemon_status(life_dir)
    resolved_dir = status.life_dir
    try:
        (resolved_dir / DAEMON_UPGRADE_REQUEST_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    if not status.alive or status.pid is None:
        return False, None
    pid = status.pid
    started_at_iso = str(getattr(status, "started_at_iso", "") or "")
    identity_alive = (
        _same_daemon_instance_alive(resolved_dir, pid, started_at_iso)
        if started_at_iso
        else os.name != "nt" and _same_daemon_alive(resolved_dir, pid)
    )
    if not identity_alive:
        return False, None
    try:
        if started_at_iso:
            request_daemon_control_stop(
                resolved_dir,
                pid=pid,
                started_at_iso=started_at_iso,
                drain=False,
            )
        if os.name != "nt":
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError, ValueError):
        return False, pid
    return True, pid


def stop_daemon(
    life_dir: Path | None = None,
    *,
    timeout: float = 10.0,
    drain: bool = False,
    drain_timeout: float | None = None,
    force: bool = False,
    preserve_upgrade_request: bool = False,
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
    resolved_dir = status.life_dir
    if not preserve_upgrade_request:
        (resolved_dir / DAEMON_UPGRADE_REQUEST_FILE).unlink(missing_ok=True)
    if not status.alive or status.pid is None:
        sys.stderr.write("argus-skill: no daemon is running for this life-dir.\n")
        return 1
    pid = status.pid
    started_at_iso = str(status.started_at_iso or "")
    if not started_at_iso and os.name == "nt":
        sys.stderr.write(
            "argus-skill: daemon status has no boot identity; refusing an "
            "unsafe stop. Restart or use an installation that publishes "
            "started_at_iso.\n"
        )
        return 2
    forced_descendants: set[int] = (
        set(_descendant_pids(pid)) if force and os.name != "nt" else set()
    )

    if drain:
        # Stop NEW missions from starting after the current one finishes,
        # preserving the objective so the operator can resume later. The daemon
        # hot-reloads continuous.json, so this lands without a restart.
        try:
            disable_continuous_config(
                resolved_dir,
                done_reason=DRAIN_STOP_REASON,
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

    def _instance_alive() -> bool:
        if started_at_iso:
            return _same_daemon_instance_alive(resolved_dir, pid, started_at_iso)
        return os.name != "nt" and _same_daemon_alive(resolved_dir, pid)

    def _clear_control_request() -> None:
        if started_at_iso:
            clear_daemon_control_stop(
                resolved_dir,
                pid=pid,
                started_at_iso=started_at_iso,
            )

    if not _instance_alive():
        if drain:
            clear_daemon_drain_request(resolved_dir, pid=pid)
        return 1
    try:
        if started_at_iso:
            request_daemon_control_stop(
                resolved_dir,
                pid=pid,
                started_at_iso=started_at_iso,
                drain=drain,
            )
        if os.name != "nt":
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError, ValueError):
        if drain:
            clear_daemon_drain_request(resolved_dir, pid=pid)
        return 1

    wait_for = drain_timeout if drain else timeout
    wait_started = time.monotonic()
    deadline = wait_started + wait_for if wait_for is not None else None
    next_heartbeat = wait_started + 30.0
    while deadline is None or time.monotonic() < deadline:
        if not _instance_alive():
            if force:
                _terminate_captured_descendants(forced_descendants)
            _clear_control_request()
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        if drain and time.monotonic() >= next_heartbeat:
            elapsed = int(time.monotonic() - wait_started)
            sys.stdout.write(
                f"argus-skill: draining... still finishing current mission "
                f"({elapsed}s elapsed).\n"
            )
            sys.stdout.flush()
            next_heartbeat += 30.0
        time.sleep(0.2)

    if force:
        if not _instance_alive():
            _terminate_captured_descendants(forced_descendants)
            _clear_control_request()
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        if os.name == "nt":
            terminated = _terminate_windows_process_tree(
                pid,
                identity_check=_instance_alive,
            )
            if not terminated:
                sys.stderr.write(
                    f"argus-skill: daemon (pid {pid}) could not be force-stopped "
                    "because its process identity changed or Windows denied access.\n"
                )
                return 2
            _clear_control_request()
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stderr.write(
                f"argus-skill: daemon (pid {pid}) did not exit within "
                f"{wait_for:.0f}s; force-stopped its verified Windows process tree.\n"
            )
            return 0
        # Capture again at the escalation boundary so children started after
        # the initial SIGTERM cannot escape by being reparented to PID 1. Stop
        # the daemon first so its Curator cannot spawn between this final
        # snapshot and root termination.
        try:
            os.kill(pid, signal.SIGSTOP)
        except ProcessLookupError:
            _clear_control_request()
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            return 0
        except (PermissionError, OSError):
            sys.stderr.write(
                f"argus-skill: daemon (pid {pid}) could not be frozen before "
                "forced descendant cleanup.\n"
            )
            return 2
        forced_descendants.update(_descendant_pids(pid))
        _terminate_captured_descendants(forced_descendants)
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except ProcessLookupError:
            _clear_control_request()
            if drain:
                clear_daemon_drain_request(resolved_dir, pid=pid)
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        if drain:
            clear_daemon_drain_request(resolved_dir, pid=pid)
        _clear_control_request()
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
    "DAEMON_UPGRADE_REQUEST_FILE",
    "ContinuousConfigState", "ContinuousConfigWriteAfterReplaceError",
    "DaemonStatus", "DaemonStopRequest",
    "clear_daemon_control_stop",
    "continuous_mode_error", "format_budget_status",
    "read_daemon_control_stop",
    "read_continuous_config", "read_continuous_state",
    "read_daemon_status", "resolve_effective_budget",
    "request_daemon_control_stop", "request_daemon_stop",
    "stop_daemon", "wait_for_daemon_status",
    "write_continuous_config",
    "_daemon_log_path", "_daemon_pid_path", "_daemon_status_path",
    "_daemon_status_payload", "_new_boot_id", "_point_active_daemon_log",
    "_process_alive", "_redirect_std_to_log",
]
