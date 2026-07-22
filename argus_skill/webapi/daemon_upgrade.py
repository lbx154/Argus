"""Daemon upgrade scheduling and reconciliation for the webapi server.

Extracted from ``server.py`` / ``daemon_lifecycle.py`` as part of a
behavior-preserving decomposition. Public/private names remain re-exported
from ``server`` for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from . import project_state

log = logging.getLogger(__name__)

_global_root = project_state.resolve_global_root
project_life_dir = project_state.project_life_dir

_SCHEDULED_DAEMON_UPGRADES: set[str] = set()
_SCHEDULED_DAEMON_UPGRADES_LOCK = threading.Lock()


def _srv():
    """Lazily resolve the ``server`` module so tests that monkeypatch
    ``server.<dep>`` (e.g. ``read_daemon_status``, ``stop_daemon``,
    ``runtime_identity``, ``daemon_command_execution_lock``) still take
    effect for this module's internal calls, matching pre-split monkeypatch
    semantics.
    """
    from . import server

    return server


def upgrade_project_daemon(
    sid: str,
    *,
    global_root: Path | str | None = None,
    drain_timeout: float = 1800.0,
) -> dict[str, Any] | None:
    """Restart one executor without blocking on a long active mission."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    status = _srv().read_daemon_status(life_dir)
    if not status.alive or status.pid is None:
        started = _srv().start_project_daemon(
            sid,
            global_root=global_root,
            resume_continuous=True,
        )
        return None if started is None else {**started, "upgraded": True}

    root = _global_root(global_root)
    continuous = _srv().read_continuous_state(life_dir)
    stop_rc = _srv().stop_daemon(
        life_dir,
        drain=True,
        drain_timeout=0.0,
        force=False,
    )
    if stop_rc == 2:
        _write_daemon_upgrade_request(
            life_dir,
            {
                "schema_version": 1,
                "sid": sid,
                "expected_pid": status.pid,
                "source_root": str(_srv().runtime_identity().get("source_root") or ""),
                "resume_continuous": bool(continuous.enabled),
                "objective": str(continuous.objective or ""),
                "reason": "operator requested current-release restart",
                "requested_at": time.time(),
                "legacy_drain_timeout": float(drain_timeout),
            },
        )
        scheduled = _srv().schedule_project_daemon_upgrade(
            sid,
            global_root=global_root,
        )
        return scheduled or {
            "rc": 2,
            "error": "daemon restart could not be scheduled",
        }
    if stop_rc not in {0, 1}:
        return {
            "rc": 2,
            "error": "daemon is still draining active work; retry upgrade after it exits",
        }
    if continuous.enabled:
        _srv().write_continuous_config(
            life_dir,
            enabled=True,
            objective=continuous.objective,
        )
    started = _srv().start_project_daemon(
        sid,
        global_root=root,
        resume_continuous=continuous.enabled,
    )
    return None if started is None else {**started, "upgraded": True}


def _daemon_upgrade_request_path(life_dir: Path) -> Path:
    return life_dir / project_state.DAEMON_UPGRADE_REQUEST_FILE


def _read_daemon_upgrade_request(life_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(_daemon_upgrade_request_path(life_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("schema_version") == 1 else None


def _write_daemon_upgrade_request(
    life_dir: Path,
    payload: dict[str, Any],
) -> None:
    life_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".daemon-upgrade-", dir=str(life_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, _daemon_upgrade_request_path(life_dir))
    finally:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _upgrade_request_matches_current_source(request: dict[str, Any]) -> bool:
    requested = str(request.get("source_root") or "").strip()
    current = str(_srv().runtime_identity().get("source_root") or "").strip()
    if not requested or not current:
        return False
    try:
        return Path(requested).expanduser().resolve() == Path(current).expanduser().resolve()
    except OSError:
        return False


def _record_daemon_upgrade_error(
    life_dir: Path,
    request: dict[str, Any],
    error: str,
) -> None:
    _write_daemon_upgrade_request(
        life_dir,
        {
            **request,
            "last_error": str(error)[:1000],
            "updated_at": time.time(),
        },
    )


def _complete_scheduled_daemon_upgrade(
    sid: str,
    *,
    life_dir: Path,
    global_root: Path | str | None,
) -> dict[str, Any]:
    request = _read_daemon_upgrade_request(life_dir)
    if request is None:
        return {"rc": 0, "upgraded": False, "reason": "upgrade request is absent"}
    if not _upgrade_request_matches_current_source(request):
        return {
            "rc": 2,
            "error": "upgrade request belongs to a different Argus installation",
        }

    status = _srv().read_daemon_status(life_dir)
    if status.alive and status.pid is not None:
        compatible, _ = _srv().daemon_protocol_compatibility(status)
        if _srv().daemon_runtime_owned_by_current_source(status) and compatible is True:
            _daemon_upgrade_request_path(life_dir).unlink(missing_ok=True)
            return {"rc": 0, "upgraded": False, "reason": "daemon is already current"}
        expected_pid = int(request.get("expected_pid") or 0)
        if status.pid != expected_pid or not _srv().daemon_runtime_owned_by_current_source(status):
            error = "daemon identity changed before the scheduled drain"
            _record_daemon_upgrade_error(life_dir, request, error)
            return {"rc": 2, "error": error}
        stop_rc = _srv().stop_daemon(
            life_dir,
            drain=True,
            drain_timeout=0.0,
            force=False,
            preserve_upgrade_request=True,
        )
        if stop_rc == 2:
            return {
                "rc": 0,
                "upgraded": False,
                "scheduled": True,
                "draining": True,
                "reason": "daemon is draining at its mission boundary",
            }
        if stop_rc not in {0, 1}:
            error = "daemon is still draining at its mission boundary"
            _record_daemon_upgrade_error(life_dir, request, error)
            return {"rc": 2, "error": error}

    with _srv().daemon_command_execution_lock(life_dir) as acquired:
        if not acquired:
            return {"rc": 2, "error": "daemon command lock unavailable"}
        request = _read_daemon_upgrade_request(life_dir)
        if request is None:
            return {
                "rc": 0,
                "upgraded": False,
                "reason": "upgrade was cancelled by a newer daemon command",
            }
        status = _srv().read_daemon_status(life_dir)
        if status.alive:
            compatible, _ = _srv().daemon_protocol_compatibility(status)
            if _srv().daemon_runtime_owned_by_current_source(status) and compatible is True:
                _daemon_upgrade_request_path(life_dir).unlink(missing_ok=True)
                return {
                    "rc": 0,
                    "upgraded": False,
                    "reason": "daemon is already current",
                }
            error = "another daemon became active before scheduled restart"
            _record_daemon_upgrade_error(life_dir, request, error)
            return {"rc": 2, "error": error}

        resume_continuous = bool(request.get("resume_continuous"))
        objective = str(request.get("objective") or "")
        if resume_continuous:
            _srv().write_continuous_config(
                life_dir,
                enabled=True,
                objective=objective,
            )
        started = _srv().start_project_daemon(
            sid,
            global_root=_global_root(global_root),
            resume_continuous=resume_continuous,
        )
        if started is None or int(started.get("rc") or 0) != 0:
            error = (
                "daemon restart returned no result"
                if started is None
                else str(started.get("error") or f"daemon restart returned {started!r}")
            )
            _record_daemon_upgrade_error(life_dir, request, error)
            return {"rc": 2, "error": error}
        _daemon_upgrade_request_path(life_dir).unlink(missing_ok=True)
        return {**started, "upgraded": True}


def schedule_project_daemon_upgrade(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Persist and asynchronously finish one same-installation daemon upgrade."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    request = _read_daemon_upgrade_request(life_dir)
    if request is not None:
        if not _upgrade_request_matches_current_source(request):
            return {
                "rc": 0,
                "scheduled": False,
                "reason": "upgrade request belongs to a different installation",
            }
        reason = str(request.get("reason") or "pending daemon upgrade")
    else:
        status = _srv().read_daemon_status(life_dir)
        compatible, reason = _srv().daemon_protocol_compatibility(status)
        if not status.alive or status.pid is None:
            return {"rc": 0, "scheduled": False, "reason": "daemon is not running"}
        if compatible is not False:
            return {"rc": 0, "scheduled": False, "reason": "daemon is current"}
        if not _srv().daemon_runtime_owned_by_current_source(status):
            return {
                "rc": 0,
                "scheduled": False,
                "reason": "daemon belongs to a different Argus installation",
            }
        continuous = _srv().read_continuous_state(life_dir)
        request = {
            "schema_version": 1,
            "sid": sid,
            "expected_pid": status.pid,
            "source_root": str(_srv().runtime_identity().get("source_root") or ""),
            "resume_continuous": bool(continuous.enabled),
            "objective": str(continuous.objective or ""),
            "reason": reason,
            "requested_at": time.time(),
        }
        _write_daemon_upgrade_request(life_dir, request)

    key = str(life_dir.resolve())
    with _SCHEDULED_DAEMON_UPGRADES_LOCK:
        if key in _SCHEDULED_DAEMON_UPGRADES:
            return {"rc": 0, "scheduled": True, "reason": "upgrade already scheduled"}
        _SCHEDULED_DAEMON_UPGRADES.add(key)

    def _run() -> None:
        try:
            result = _complete_scheduled_daemon_upgrade(
                sid,
                life_dir=life_dir,
                global_root=global_root,
            )
            if result.get("draining") is True:
                timer = threading.Timer(
                    5.0,
                    lambda: _srv().schedule_project_daemon_upgrade(
                        sid,
                        global_root=global_root,
                    ),
                )
                timer.daemon = True
                timer.start()
            if int(result.get("rc") or 0) != 0:
                log.error("scheduled daemon upgrade incomplete sid=%s result=%r", sid, result)
        except Exception:
            log.exception("scheduled daemon upgrade crashed sid=%s", sid)
        finally:
            with _SCHEDULED_DAEMON_UPGRADES_LOCK:
                _SCHEDULED_DAEMON_UPGRADES.discard(key)

    worker = threading.Thread(
        target=_run,
        name=f"argus-daemon-upgrade-{sid[:12]}",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        with _SCHEDULED_DAEMON_UPGRADES_LOCK:
            _SCHEDULED_DAEMON_UPGRADES.discard(key)
        raise
    return {"rc": 0, "scheduled": True, "reason": reason}


def reconcile_pending_daemon_upgrades(
    roots: list[Path],
) -> list[str]:
    """Resume durable upgrade requests whenever the WebAPI starts."""
    scheduled: list[str] = []
    for root in roots:
        projects = core_paths.session_states_root(root)
        try:
            life_dirs = [path for path in projects.iterdir() if path.is_dir()]
        except FileNotFoundError:
            continue
        except OSError:
            log.exception("cannot scan pending daemon upgrades root=%s", root)
            continue
        for life_dir in life_dirs:
            if not project_state.daemon_upgrade_pending(life_dir):
                continue
            try:
                result = _srv().schedule_project_daemon_upgrade(
                    life_dir.name,
                    global_root=root,
                )
            except Exception:
                log.exception("cannot resume daemon upgrade sid=%s", life_dir.name)
                continue
            if result is not None and result.get("scheduled") is True:
                scheduled.append(life_dir.name)
    return scheduled
