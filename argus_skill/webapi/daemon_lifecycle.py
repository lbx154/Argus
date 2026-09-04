"""Daemon lifecycle and upgrade orchestration for the webapi server.

Extracted from ``server.py`` as part of a behavior-preserving decomposition.
Public/private names remain re-exported from ``server`` for backward
compatibility (tests and ``webapi/routes/*`` reach them via ``server.*`` /
``server_mod.*``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from ..core.event_catalog import EventType
from ..core.role_config import resolve_all_roles
from ..core.session import (
    SessionMeta,
    migrate_legacy_session_workdir,
    normalize_session_name,
    read_session_meta,
    resolve_session_workdir,
    session_workdir_is_bound,
    session_lifecycle_lock,
    update_session_meta,
    write_session_meta,
)
from ..daemon.life_worker import (
    LifeWorkerConfig,
    _acquire_daemon_spawn_lock,
    _launcher_failure_message,
    _release_daemon_spawn_lock,
    _workspace_start_error,
)
from ..life.memory import LifeMemory
from ..life.role_activity import role_activity
from . import project_state
from ._server_module import server_module as _srv

log = logging.getLogger(__name__)

_global_root = project_state.resolve_global_root
_roles_list = project_state.roles_list
_daemon_dict = project_state.daemon_dict
_DAEMON_ADMISSION_FILE = project_state.DAEMON_ADMISSION_FILE
project_life_dir = project_state.project_life_dir
list_projects = project_state.list_projects


def _worker_config_from_env(life_dir: Path, global_root: Path) -> LifeWorkerConfig:
    """Minimal daemon config from the current global cap/backend — mirrors what a
    fresh CLI launch would enforce. Resolve role models/efforts through the
    SAME persisted/env/vault precedence used by /config and the CLI; leaving
    these fields at ``LifeWorkerConfig``'s dataclass defaults silently launched
    gpt-5.5 while the cockpit reported a configured Sonnet model."""
    from ..core.knobs import (
        resolve_budget_caps,
        resolve_role_backend,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )

    budget = resolve_budget_caps(
        project_state_dir=life_dir,
        global_root=global_root,
    )
    meta = read_session_meta(global_root, life_dir.name)
    if not session_workdir_is_bound(meta):
        prior = _srv().read_daemon_status(life_dir).project_workdir
        project_workdir = migrate_legacy_session_workdir(
            global_root,
            life_dir.name,
            state_dir=life_dir,
            candidates=(prior,),
        )
    else:
        project_workdir = resolve_session_workdir(meta, state_dir=life_dir)

    return LifeWorkerConfig(
        life_dir=life_dir,
        global_root=global_root,
        # All four roles use the same persisted execution root. Internal daemon
        # state remains under life_dir regardless of where project work happens.
        project_workdir=project_workdir,
        # default="codex": the cockpit autostart has no --backend flag to
        # honour, so the chain is the operator's ONLY channel here; codex
        # matches LifeWorkerConfig's own dataclass default, which is what this
        # field held before when nothing was configured.
        backend=resolve_role_backend("", default="codex"),
        engineer_model=resolve_role_model(
            "engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL",
        ),
        reviewer_model=resolve_role_model(
            "reviewer", role_env="ARGUS_SKILL_REVIEWER_MODEL",
        ),
        engineer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        ),
        reviewer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
        ),
        global_daily_cap_usd=budget.global_daily_cap_usd,
        planner_task_iteration_max_cycles=int(
            os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "0")
        ),
    )


_UNFINISHED_BACKLOG_STATUSES = {"pending", "running", "in_progress", "claimed"}
_TRANSIENT_WINDOWS_SPAWN_MARKERS = (
    "winerror 32",
    "winerror 33",
    "sharing violation",
    "lock violation",
    "resource temporarily unavailable",
)


def _running_on_windows() -> bool:
    """Return the host platform without requiring tests to mutate global ``os``."""
    return os.name == "nt"


def _retryable_windows_spawn_failure(rc: int, diagnostic: str) -> bool:
    """Retry only an unexpected, explicitly transient Windows bootstrap error.

    Exit codes 2 and 3 are deliberate admission/workspace failures.  An rc=1
    can also be deterministic (bad auth, a missing module, or invalid startup
    code), so blindly replaying every rc=1 would repeat login prompts and other
    side effects.  The bounded retry is reserved for the Win32 sharing/locking
    failures that can clear between two CreateProcess attempts.
    """
    if not _running_on_windows() or rc != 1:
        return False
    lowered = diagnostic.casefold()
    return any(marker in lowered for marker in _TRANSIENT_WINDOWS_SPAWN_MARKERS)


def list_running_daemons(
    *, global_root: Path | str | None = None, exclude_sid: str = "",
) -> list[dict[str, Any]]:
    """Return live daemon sessions with enough context for replacement choice."""
    root = _global_root(global_root)
    rows: list[dict[str, Any]] = []
    for project in list_projects(global_root=root, limit=2000, include_empty=True):
        sid = str(project.get("id") or "")
        if not sid or sid == exclude_sid or not project.get("daemon_alive"):
            continue
        life_dir = core_paths.session_state_root(sid, root=root)
        try:
            items = LifeMemory.open(life_dir).backlog.active()
        except Exception:  # noqa: BLE001
            items = []
        unfinished = [
            item for item in items if item.status in _UNFINISHED_BACKLOG_STATUSES
        ]
        active_item = next(
            (item for item in unfinished if item.status != "pending"),
            unfinished[0] if unfinished else None,
        )
        try:
            roles = _roles_list(
                resolve_all_roles(env=os.environ),
                role_activity(life_dir),
            )
        except Exception:  # noqa: BLE001
            roles = []
        active_role = next((role for role in roles if role.get("active")), None)
        try:
            continuous = _srv().read_continuous_state(life_dir)
        except Exception:  # noqa: BLE001
            continuous = None
        rows.append({
            **project,
            "active_role": (active_role or {}).get("role", ""),
            "activity": (active_role or {}).get("label", ""),
            "current_task": getattr(active_item, "title", "") or "",
            "unfinished_tasks": len(unfinished),
            "continuous_enabled": bool(continuous and continuous.enabled),
            "continuous_objective": (
                str(continuous.objective or "") if continuous is not None else ""
            ),
        })
    return rows


def _admission_required(
    *,
    root: Path,
    sid: str,
    limit: int,
    active_count: int,
    resume_continuous: bool,
) -> dict[str, Any]:
    running = _srv().list_running_daemons(global_root=root, exclude_sid=sid)
    admission = {
        "rc": 2,
        "already_alive": False,
        "admission_required": True,
        "requested_at": time.time(),
        "target_sid": sid,
        "resume_continuous": bool(resume_continuous),
        "limit": limit,
        "active_count": active_count,
        "error": (
            f"active daemon limit {limit} reached; choose one running session "
            "to park before starting this work"
        ),
        "running_daemons": running,
    }
    try:
        path = core_paths.session_state_root(sid, root=root) / _DAEMON_ADMISSION_FILE
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(admission, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass
    return admission


def _clear_daemon_admission(life_dir: Path) -> None:
    try:
        (life_dir / _DAEMON_ADMISSION_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def start_project_daemon(
    sid: str, *, global_root: Path | str | None = None,
    resume_continuous: bool = False,
    reclaim_idle: bool = False,
) -> dict[str, Any] | None:
    """Spawn this project's detached daemon (if not already alive). Blocking-ish
    (subprocess spawn) — call from a threadpool in the async endpoint."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    st = _srv().read_daemon_status(life_dir)
    if st.alive:
        _clear_daemon_admission(life_dir)
        return {"rc": 0, "already_alive": True, "daemon": _daemon_dict(st)}
    try:
        config = _worker_config_from_env(life_dir, root)
    except (OSError, RuntimeError) as exc:
        return {
            "rc": 3,
            "already_alive": False,
            "error": f"daemon workdir is unavailable: {exc}",
            "daemon": _daemon_dict(_srv().read_daemon_status(life_dir)),
        }
    # Web project workers are finite unless they adopt a persisted campaign
    # that is explicitly both enabled and open-ended. Otherwise the dataclass's
    # CLI-oriented 7x24 default would keep a drained task process alive forever,
    # including one started with the cockpit's Resume button.
    config.continuous_open_ended = False
    if resume_continuous:
        continuous = _srv().read_continuous_state(life_dir)
        if (
            not continuous.enabled
            and continuous.objective.strip()
            and continuous.done_reason.strip().lower().startswith("operator ")
        ):
            _srv().write_continuous_config(
                life_dir,
                enabled=True,
                objective=continuous.objective,
            )
            continuous = _srv().read_continuous_state(life_dir)
        if continuous.enabled:
            config.continuous_objective = continuous.objective
            config.resume_continuous = True
            config.continuous_open_ended = continuous.open_ended
    daemon_limit = _srv()._max_active_daemons(config)
    active_count = _srv()._active_daemon_count(config)
    if daemon_limit > 0 and active_count >= daemon_limit:
        if reclaim_idle:
            running = _srv().list_running_daemons(global_root=root, exclude_sid=sid)
            idle = [
                row for row in running
                if int(row.get("unfinished_tasks") or 0) == 0
                and not row.get("active_role")
                and not row.get("continuous_enabled")
            ]
            if idle:
                victim = min(
                    idle,
                    key=lambda row: float(row.get("last_active") or 0.0),
                )
                replaced = _srv().replace_project_daemon(
                    sid,
                    str(victim.get("id") or ""),
                    global_root=root,
                    resume_continuous=resume_continuous,
                )
                if replaced is not None and int(replaced.get("rc") or 0) == 0:
                    replaced["auto_parked_idle"] = str(victim.get("id") or "")
                    return replaced
        return {
            **_admission_required(
                root=root,
                sid=sid,
                limit=daemon_limit,
                active_count=active_count,
                resume_continuous=resume_continuous,
            ),
            "daemon": _daemon_dict(_srv().read_daemon_status(life_dir)),
        }
    startup_diagnostic = ""
    startup_recovery_diagnostic = ""
    try:
        rc = _srv().spawn_detached_daemon(config, quiet=True)
        startup_diagnostic = config.last_spawn_error.strip()
        if _retryable_windows_spawn_failure(rc, startup_diagnostic):
            # The first launcher can finish just as its runtime publishes
            # status. Never create a second worker if that happened; otherwise
            # retry one known-transient Win32 sharing/lock failure exactly once.
            after_first = _srv().read_daemon_status(life_dir)
            if after_first.alive:
                startup_recovery_diagnostic = startup_diagnostic
                startup_diagnostic = ""
                rc = 0
            else:
                log.warning(
                    "retrying one transient Windows executor startup for session %s: %s",
                    sid,
                    startup_diagnostic,
                )
                rc = _srv().spawn_detached_daemon(config, quiet=True)
                second_diagnostic = config.last_spawn_error.strip()
                if rc == 0:
                    startup_recovery_diagnostic = startup_diagnostic
                    startup_diagnostic = ""
                else:
                    startup_diagnostic = second_diagnostic or startup_diagnostic
    except Exception as exc:  # noqa: BLE001 — return an actionable API result
        log.exception("background executor raised during startup for session %s", sid)
        return {
            "rc": 2,
            "already_alive": False,
            "error": (
                "The background worker could not start. "
                "Check the startup diagnostic and try again."
            ),
            "startup_diagnostic": f"{type(exc).__name__}: {exc}",
            "daemon": _daemon_dict(_srv().read_daemon_status(life_dir)),
        }
    result = {
        "rc": rc,
        "already_alive": False,
        "daemon": _daemon_dict(_srv().read_daemon_status(life_dir)),
    }
    if startup_diagnostic:
        result["startup_diagnostic"] = startup_diagnostic
    if startup_recovery_diagnostic:
        result["startup_retried"] = True
    if rc == 3:
        # ``startup_diagnostic`` first, because rc=3 is an admission refusal and
        # the refusal itself names what is holding the directory — the owning
        # pid, its session and project, and the three ways out. The generic
        # strings below say only that *something* owns it, which is the half of
        # the answer the operator cannot act on. ``_launcher_failure_message``
        # keeps a framework-formatted refusal whole rather than collapsing it to
        # its last line, which for the lease message left "- or start this
        # objective in a different directory" and nothing else.
        result["error"] = (
            _launcher_failure_message(startup_diagnostic, rc)
            if startup_diagnostic
            else ""
        ) or _workspace_start_error(config) or (
            "workdir changed or is already owned by another active session"
        )
        log.error(
            "background executor rejected session %s: %s",
            sid,
            startup_diagnostic or result["error"],
        )
        return result
    if rc != 0:
        active_count = _srv()._active_daemon_count(config)
        if daemon_limit > 0 and active_count >= daemon_limit:
            return {
                **_admission_required(
                    root=root,
                    sid=sid,
                    limit=daemon_limit,
                    active_count=active_count,
                    resume_continuous=resume_continuous,
                ),
                "daemon": _daemon_dict(_srv().read_daemon_status(life_dir)),
            }
        result["error"] = (
            "The background worker could not start. "
            "Check the startup diagnostic and try again."
        )
        log.error(
            "background executor failed to start for session %s (rc=%s): %s",
            sid,
            rc,
            startup_diagnostic or "no launcher diagnostic was captured",
        )
    else:
        _clear_daemon_admission(life_dir)
    return result


def _write_parked_state(
    victim_dir: Path,
    *,
    victim_sid: str,
    target_sid: str,
    previous_pid: int | None,
) -> None:
    try:
        items = LifeMemory.open(victim_dir).backlog.active()
        unfinished = [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
            }
            for item in items
            if item.status in _UNFINISHED_BACKLOG_STATUSES
        ]
    except Exception:  # noqa: BLE001
        unfinished = []
    payload = {
        "version": 1,
        "parked_at": time.time(),
        "session_id": victim_sid,
        "replaced_by": target_sid,
        "previous_pid": previous_pid,
        "unfinished_tasks": unfinished,
        "state_preserved": True,
    }
    path = victim_dir / "daemon.parked.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=victim_dir).append({
            "type": EventType.DAEMON_PARKED,
            "replaced_by": target_sid,
            "previous_pid": previous_pid,
            "unfinished_tasks": unfinished,
            "state_preserved": True,
        })
    except Exception:  # noqa: BLE001
        pass


_DAEMON_REPLACEMENT_LOCK = threading.Lock()


def replace_project_daemon(
    sid: str,
    victim_sid: str,
    *,
    global_root: Path | str | None = None,
    resume_continuous: bool = False,
) -> dict[str, Any] | None:
    """Park one live daemon, preserve its state, and start the queued target."""
    root = _global_root(global_root)
    target_dir = project_life_dir(sid, global_root=root)
    victim_dir = project_life_dir(victim_sid, global_root=root)
    if target_dir is None or victim_dir is None:
        return None
    if sid == victim_sid:
        return {"rc": 2, "error": "target and replacement victim are the same session"}

    with _DAEMON_REPLACEMENT_LOCK:
        victim_status = _srv().read_daemon_status(victim_dir)
        if not victim_status.alive:
            return {
                "rc": 2,
                "error": f"session {victim_sid} is no longer running; refresh the list",
            }
        stop_rc = _srv().stop_daemon(victim_dir, timeout=2.0, force=True)
        if stop_rc not in {0, 1}:
            return {
                "rc": 2,
                "error": f"could not park {victim_sid} (stop rc={stop_rc})",
            }
        deadline = time.monotonic() + 5.0
        while _srv().read_daemon_status(victim_dir).alive and time.monotonic() < deadline:
            time.sleep(0.05)
        if _srv().read_daemon_status(victim_dir).alive:
            return {
                "rc": 2,
                "error": f"session {victim_sid} did not release its daemon slot",
            }
        _write_parked_state(
            victim_dir,
            victim_sid=victim_sid,
            target_sid=sid,
            previous_pid=victim_status.pid,
        )
        started = _srv().start_project_daemon(
            sid,
            global_root=root,
            resume_continuous=resume_continuous,
        )
        if started is None:
            return None
        return {
            **started,
            "parked_session": victim_sid,
            "parked_state": str(victim_dir / "daemon.parked.json"),
        }


def create_daemon(
    objective: str = "", *, name: str = "",
    launch_cwd: str = "",
    workdir: str = "",
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Mint a brand-new daemon (session). The objective is OPTIONAL: creating a
    daemon is starting a conversation with a fresh Manager, not configuring a
    research campaign. With no objective the session is created idle — the user
    just talks to it, and the Manager decides everything (reply to chat, or write
    its OWN objective and dispatch a mission). The daemon spawns lazily on the
    first real task (via POST /message), so an empty daemon leaves no idle
    executor. When an objective IS given, it's armed as a self-directed campaign
    and the daemon starts immediately when admission capacity is available (the
    web equivalent of ``--new --continuous --objective``). ``launch_cwd`` is
    discovery/UI metadata only; without an explicit execution ``workdir``, output
    goes to ``<ARGUS_SKILL_HOME>/workspaces/<sid>`` — never the Argus source
    checkout or process cwd. At the host-wide
    daemon cap, the session and objective stay persisted and the response carries
    replacement candidates for an explicit operator choice. Blocking-ish (fs +
    fork) — call from a threadpool. Returns the new sid + daemon status.
    """
    import time as _time

    from ..core.session import new_session_id

    root = _global_root(global_root)
    sid = new_session_id()
    now = _time.time()
    requested_objective = (objective or "").strip()
    life_dir = core_paths.session_state_root(sid, root=root)
    if workdir:
        effective_workdir = str(
            Path(workdir).expanduser().resolve(strict=True)
        )
    else:
        default_workdir = root / "workspaces" / sid
        default_workdir.mkdir(parents=True, exist_ok=True)
        effective_workdir = str(default_workdir.resolve(strict=True))
    if not Path(effective_workdir).is_dir():
        raise ValueError(f"workdir is not a directory: {effective_workdir}")
    if launch_cwd:
        effective_launch_cwd = str(
            Path(launch_cwd).expanduser().resolve(strict=True)
        )
    else:
        effective_launch_cwd = effective_workdir
    if not Path(effective_launch_cwd).is_dir():
        raise ValueError(f"launch cwd is not a directory: {effective_launch_cwd}")
    meta = SessionMeta(
        id=sid,
        display_name=normalize_session_name(name),
        created=now,
        last_active=now,
        cwd=str(life_dir),
        workdir=effective_workdir,
        objective="",
        launch_cwd=effective_launch_cwd,
        origin="web",
    )
    # Persist the deliberate Web session before the Manager round-trip so
    # concurrent empty-project GC cannot remove it while division is running.
    write_session_meta(root, meta)
    life_dir.mkdir(parents=True, exist_ok=True)

    start_result: dict[str, Any] | None = None
    obj = requested_objective
    if obj:
        from .manager_dispatch import manager_continuous_handoff

        obj = manager_continuous_handoff(
            sid,
            obj,
            global_root=root,
            name_session=not bool(meta.display_name),
        )
        from ..manager.front_door import _derive_session_name

        fallback_name = _derive_session_name(requested_objective, limit=32)

        def _finish_session(current: SessionMeta) -> None:
            current.objective = obj
            if not current.display_name:
                current.display_name = fallback_name

        update_session_meta(root, sid, _finish_session, create=True)
        # Explicit objective → arm the self-directed campaign + start the daemon
        # now. The daemon hot-reloads continuous.json.
        start_result = _srv().start_project_daemon(
            sid,
            global_root=root,
            resume_continuous=True,
        )
    # else: idle session — no continuous, no eager spawn. The Manager (via
    # /message) writes objectives and lazily spawns the executor when needed.

    daemon = _daemon_dict(_srv().read_daemon_status(life_dir))
    rc = int((start_result or {}).get("rc") or 0)
    response = {
        "sid": sid,
        "rc": rc,
        "spawned": bool(start_result is not None and rc == 0),
        "daemon": daemon,
        "objective": obj,
        "workdir": effective_workdir,
    }
    if start_result is not None:
        response["start"] = start_result
    return response


def set_project_launch_cwd(
    sid: str, launch_cwd: str, *, global_root: Path | str | None = None,
) -> bool | None:
    """Update UI launch-location metadata without changing execution workdir."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    try:
        resolved_path = Path(launch_cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not resolved_path.is_dir():
        return False
    resolved_cwd = str(resolved_path)

    def _set_launch_cwd(meta: SessionMeta) -> None:
        now = time.time()
        if not meta.created:
            meta.created = now
        if not meta.last_active:
            meta.last_active = now
        if not meta.cwd:
            meta.cwd = str(life_dir)
        meta.launch_cwd = resolved_cwd

    return update_session_meta(
        root,
        sid,
        _set_launch_cwd,
        create=True,
    ) is not None


def set_project_workdir(
    sid: str,
    workdir: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Atomically change a stopped session's exclusive execution workdir."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    try:
        target = Path(workdir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": f"workdir is unavailable: {exc}"}
    if not target.is_dir():
        return {"ok": False, "error": f"workdir is not a directory: {target}"}

    config = LifeWorkerConfig(
        life_dir=life_dir,
        global_root=root,
        project_workdir=target,
        project_fingerprint=sid,
    )
    from ..manager._session_ops import manager_pipeline_lock, manager_session_lock

    with (
        session_lifecycle_lock(root, sid),
        manager_pipeline_lock(life_dir),
        manager_session_lock(life_dir),
    ):
        spawn_lock = _acquire_daemon_spawn_lock(config)
        workspace_lease = None
        try:
            meta = read_session_meta(root, sid)
            current = resolve_session_workdir(meta, state_dir=life_dir)
            status = _srv().read_daemon_status(life_dir)
            if status.alive:
                if current == target:
                    return {"ok": True, "workdir": str(target), "unchanged": True}
                return {
                    "ok": False,
                    "error": "cannot change workdir while this daemon is running",
                }
            owner = _srv()._active_workspace_owner(config, target_workdir=target)
            if owner is not None:
                return {
                    "ok": False,
                    "error": (
                        f"workdir is already owned by active session {owner['sid']} "
                        f"(pid {owner['pid']})"
                    ),
                }
            from ..core.workspace_lease import (
                WorkspaceLeaseBusy,
                acquire_workspace_lease,
                release_workspace_lease,
            )

            try:
                workspace_lease = acquire_workspace_lease(
                    target,
                    owner={"sid": sid, "operation": "set-workdir"},
                )
            except WorkspaceLeaseBusy as exc:
                return {"ok": False, "error": str(exc)}

            def _set_workdir(current_meta: SessionMeta) -> None:
                now = time.time()
                if not current_meta.created:
                    current_meta.created = now
                if not current_meta.last_active:
                    current_meta.last_active = now
                if not current_meta.cwd:
                    current_meta.cwd = str(life_dir)
                current_meta.workdir = str(target)

            updated = update_session_meta(root, sid, _set_workdir, create=True)
            if updated is None:
                return None
            return {"ok": True, "workdir": str(target), "unchanged": current == target}
        finally:
            if workspace_lease is not None:
                release_workspace_lease(workspace_lease)
            _release_daemon_spawn_lock(spawn_lock)


def stop_project_daemon(
    sid: str, *, drain: bool = False, force: bool = False,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Stop this project's daemon. Blocking (waits up to the drain timeout) —
    call from a threadpool in the async endpoint."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    # An explicit UI force-stop is the Codex-style interrupt path: give the
    # verified daemon one second to honor its control marker, then terminate
    # only that captured process tree. Ordinary stop/drain semantics are
    # unchanged and remain available to lifecycle/upgrade flows.
    rc = _srv().stop_daemon(
        life_dir,
        timeout=1.0 if force else 10.0,
        drain=drain,
        force=force,
    )
    return {"rc": rc, "forced": force}
