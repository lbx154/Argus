"""``argus-skill`` web/TUI backend API — thin FastAPI layer over the daemon.

The 7×24 daemon is a file-based pub/sub: it appends events to
``<life_dir>/events.jsonl`` and reads commands from ``backlog.jsonl`` /
``inbox.jsonl``. Both new frontends — the Ink terminal UI (``frontend/tui/``)
and the React web UI (``frontend/web/``) — are **clients of this one API**, so
neither reimplements backend logic.

Design rules (keep this layer dumb):
- Read-only project aggregation lives in :mod:`.project_state`; this module
  re-exports its stable API for compatibility.
- Every endpoint DELEGATES to an existing ``argus_skill`` function. This module
  never parses event semantics or backlog schemas itself — it forwards dicts and
  calls the reused helpers (``list_sessions``, ``read_daemon_status``,
  ``role_activity``, ``resolve_all_roles``, ``_read_recent_jsonl_events``,
  ``LifeMemory.backlog``).
- Defaults to a ``127.0.0.1`` bind — unlike ``tools/dashboard.py`` which binds
  ``0.0.0.0`` with no auth. Expose to a LAN only via an explicit ``--web-host``.
- ``fastapi`` / ``uvicorn`` are the optional ``[web]`` extra; import them lazily
  inside :func:`create_app` / :func:`serve` so importing this module never
  hard-requires them.

M0 scope: ``GET /api/projects``, ``GET /api/projects/{sid}/snapshot``,
``GET /api/projects/{sid}/events``, ``WS /api/projects/{sid}/stream``.
Command POSTs (task/nudge/daemon start-stop/config) land in M1.
"""

# NB: deliberately NO ``from __future__ import annotations`` here — the nested
# FastAPI route handlers in create_app() annotate params with the locally-
# imported ``WebSocket``/``Query`` types, and stringized annotations would make
# FastAPI fail to resolve them (it reads annotations against module globals,
# where the lazily-imported fastapi symbols do not live). Runtime ``X | None``
# unions are fine on the required Python >=3.11.

import asyncio
import json
import os
import queue
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ..apps._inbox import count_pending_inbox_messages, queue_inbox_message
from ..apps._life_actions import add_backlog_item, append_note, parse_add_flags
from ..apps.cli._follow import _read_recent_jsonl_events, _read_recent_project_events
from ..cli.roles_status import resolve_all_roles, role_activity
from ..core.config_snapshot import build_config_snapshot
from ..core.event_catalog import EventType, canonical_event_type
from ..core.metrics import (
    http_route_template,
    metrics_snapshot,
    record_metric,
    render_prometheus,
)
from ..core.provider_quota import provider_usage_snapshot
from ..core.session import (
    SessionMeta,
    normalize_session_name,
    read_session_meta,
    session_lifecycle_lock,
    session_meta_lock,
    update_session_meta,
    write_session_meta,
)
from ..core.transcript import read_turns
from ..daemon.commands import DaemonCommandReceipt, execute_daemon_command
from ..daemon.life_worker import (
    DaemonStatus,
    LifeWorkerConfig,
    _active_daemon_count,
    _max_active_daemons,
    read_continuous_state,
    read_daemon_status,
    spawn_detached_daemon,
    stop_daemon,
    write_continuous_config,  # noqa: F401 - compatibility export
)
from ..life.memory import BacklogItem, LifeMemory, _read_jsonl_tail_history
from ..manager.front_door import (
    ManagerHandoffError,
    ManagerHandoffSupersededError,
)
from ..tools.doctor import run_diagnostics
from . import artifacts, project_state
from .protocol import build_api_meta, protocol_header

_DAEMON_ADMISSION_FILE = project_state.DAEMON_ADMISSION_FILE
_daemon_dict = project_state.daemon_dict
_global_root = project_state.resolve_global_root
_roles_list = project_state.roles_list
_settled_spend = project_state.settled_spend
_stat_signature = project_state.stat_signature
build_snapshot = project_state.build_snapshot
list_projects = project_state.list_projects
project_life_dir = project_state.project_life_dir
_artifact_metadata = artifacts.artifact_metadata
_latest_evidence_files = artifacts.latest_evidence_files
_manager_live_view_files = artifacts.manager_live_view_files
_project_git_diff = artifacts.project_git_diff
_project_workspace = artifacts.project_workspace
_resolved_project_artifact = artifacts.resolved_project_artifact
_safe_artifact_path = artifacts.safe_artifact_path
get_project_artifact = artifacts.get_project_artifact
list_project_artifacts = artifacts.list_project_artifacts

__all__ = [
    "DaemonStatus",
    "create_app", "serve", "project_life_dir", "build_snapshot", "list_projects",
    "enqueue_task", "enqueue_nudge", "answer_pending_question",
    "start_project_daemon", "stop_project_daemon",
    "replace_project_daemon", "list_running_daemons",
    "update_project", "delete_project", "list_trashed_projects",
    "restore_trashed_project", "upgrade_project_daemon",
    "set_continuous", "get_status", "get_journal", "add_project_note",
    "abort_project_mission", "dispose_backlog", "stop_backlog_iteration",
    "get_doctor", "get_config",
    "get_identity", "get_transcript",
    "get_backlog_item",
    "set_operator_config", "set_identity", "run_skill_command",
    "list_project_artifacts", "get_project_artifact",
]

EVENT_FILE = "events.jsonl"
_JOURNAL_TAIL_CACHE: dict[
    tuple[str, int],
    tuple[tuple[tuple[int, int, int] | None, tuple[int, int, int] | None], list[dict[str, Any]]],
] = {}
_JOURNAL_TAIL_CACHE_LOCK = threading.Lock()
_WEB_UI_DROPPED_EVENT_TYPES = frozenset({
    EventType.AGENT_IO_START,
    EventType.AGENT_IO_STREAM,
    EventType.AGENT_IO_COMPLETE,
    EventType.USAGE_RECORDED,
    EventType.CODEX_UTIL_COMPLETED,
    EventType.SKILL_COST_COMPLETED,
    EventType.BUDGET_RESERVATION_CREATED,
    EventType.BUDGET_RESERVATION_SETTLED,
    EventType.BUDGET_RESERVATION_RELEASED,
})


def _event_visible_in_web_ui(event: dict[str, Any]) -> bool:
    if event.get("operator_alert") is True:
        return True
    return canonical_event_type(event.get("type")) not in _WEB_UI_DROPPED_EVENT_TYPES


def _web_cache_control(path: str) -> str:
    """Return cache policy for the static SPA shell and hashed build assets."""
    if path in {"/", "/index.html"}:
        return "no-store"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return ""


def _command_response(receipt: DaemonCommandReceipt) -> dict[str, Any]:
    result = dict(receipt.result)
    if receipt.status in {"failed", "rejected"}:
        result.setdefault("rc", 3)
        result.setdefault("error", receipt.error)
    result.update({
        "command_id": receipt.command_id,
        "command_status": receipt.status,
        "command_revision": receipt.revision,
        "command": receipt.to_jsonable(),
    })
    return result


# ---------------------------------------------------------------------------
# Pure helpers (no FastAPI import — unit-testable without the [web] extra)
# ---------------------------------------------------------------------------

def _manager_stream_heartbeat_seconds() -> float:
    """Silence interval before SSE reports that it is awaiting a model event.

    This is status, not invented chain-of-thought: the frame says only what the
    bridge can verify (the Manager turn is still alive and ACP has emitted
    nothing new). Set ``ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S=0`` to disable.
    """
    raw = os.environ.get("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", "10")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 10.0


def _iter_manager_stream_items(
    items: Any,
    *,
    heartbeat_s: float,
    clock: Any = None,
):
    """Drain Manager fragments and add honest heartbeat phase frames.

    ``items`` is queue-like solely to keep this helper deterministic in tests.
    Heartbeats never advance ``last_real_at``; therefore their ``quiet_s`` is
    the elapsed time since the most recent genuine phase/delta from the worker.
    A real event resets that clock, and the sentinel stops heartbeats at once.
    """
    now = clock or time.monotonic
    last_real_at = now()
    while True:
        try:
            item = items.get(timeout=heartbeat_s) if heartbeat_s > 0 else items.get()
        except queue.Empty:
            quiet_s = max(0, int(now() - last_real_at))
            yield {
                "type": "phase",
                "role": "manager",
                "label": f"Manager · waiting for the next model event · {quiet_s}s quiet",
                "heartbeat": True,
                "quiet_s": quiet_s,
            }
            continue
        if item is None:
            return
        last_real_at = now()
        yield item


# ---------------------------------------------------------------------------
# Command helpers (write side) — all go through the SAME reused functions the
# CLI uses, so the flock CAS / atomic writes are shared. Never write the
# backlog/inbox files directly. Each returns None if the project is unknown.
# ---------------------------------------------------------------------------

def _enqueue_task_unlocked(
    sid: str, text: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    from ..apps._life_actions import DEFAULT_LIFE_CONFIG

    iterate, cycles, budget, cleaned = parse_add_flags(
        text,
        defaults=DEFAULT_LIFE_CONFIG,
    )
    objective = cleaned or text.strip()
    item_id = BacklogItem.new_id()
    from .manager_bridge import manager_bounded_handoff

    mem = LifeMemory.open(life_dir)

    def _persist(execution_task: str, _division: Any):
        return add_backlog_item(
            mem,
            execution_task,
            item_id=item_id,
            iterate=iterate,
            iteration_max_cycles=cycles,
            iteration_budget_usd=budget,
        )

    item = manager_bounded_handoff(
        sid,
        objective,
        _persist,
        global_root=global_root,
        root_task_id=item_id,
        name_session=not bool(
            (read_session_meta(_global_root(global_root), sid) or SessionMeta(id=sid))
            .display_name.strip()
        ),
    )
    return item.to_jsonable()


def enqueue_task(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Append one Manager-authored task while excluding delete/restore races."""
    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with session_lifecycle_lock(lock_root, sid):
        return _enqueue_task_unlocked(sid, text, global_root=root)


def enqueue_task_command(
    sid: str,
    text: str,
    *,
    autostart_daemon: bool,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Atomically enqueue and optionally start before deletion can move the project."""
    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with session_lifecycle_lock(lock_root, sid):
        item = _enqueue_task_unlocked(sid, text, global_root=root)
        if item is None:
            return None
        response: dict[str, Any] = {"item": item}
        if autostart_daemon:
            response["daemon"] = start_project_daemon(
                sid,
                global_root=root,
                resume_continuous=False,
                reclaim_idle=True,
            )
        return response


def enqueue_nudge(
    sid: str, text: str, *, global_root: Path | str | None = None, source: str = "web"
) -> bool | None:
    """Queue operator guidance to the inbox (also emits ``life.inbox.queued``
    so it shows on the live stream)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    queue_inbox_message(life_dir, text.strip(), source=source)
    return True


def answer_pending_question(
    sid: str,
    item_id: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Continue one blocked item without routing its answer through Manager."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    answer = text.strip()
    mem = LifeMemory.open(life_dir)
    blocked, continuation = mem.backlog.continue_with_operator_reply(
        item_id,
        answer,
    )
    if blocked is None:
        return None
    if continuation is None:
        return {"error": "question is no longer pending"}
    return {
        "answered_item_id": blocked.id,
        "item": continuation.to_jsonable(),
    }


def _worker_config_from_env(life_dir: Path, global_root: Path) -> LifeWorkerConfig:
    """Minimal daemon config from the current env caps/backend — mirrors what a
    fresh CLI launch would enforce. Resolve role models/efforts through the
    SAME persisted/env/vault precedence used by /config and the CLI; leaving
    these fields at ``LifeWorkerConfig``'s dataclass defaults silently launched
    gpt-5.5 while the cockpit reported a configured Sonnet model."""
    from ..core.knobs import (
        resolve_budget_caps,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )

    budget = resolve_budget_caps()

    return LifeWorkerConfig(
        life_dir=life_dir,
        global_root=global_root,
        # Web/TUI sessions are isolated workspaces.  Detached daemons chdir to
        # `/`, so relying on process cwd makes vertical and stage resolution
        # diverge from the Manager front-door.  Pin execution to the session.
        project_workdir=life_dir,
        backend=os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
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
        per_mission_cap_usd=budget.per_mission_cap_usd,
        daily_cap_usd=budget.daily_cap_usd,
        global_daily_cap_usd=budget.global_daily_cap_usd,
        planner_task_iteration_max_cycles=int(
            os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "6")
        ),
        planner_task_iteration_budget_usd=float(
            os.environ.get("ARGUS_SKILL_PLANNER_TASK_ITERATION_BUDGET_USD", "30.0")
        ),
    )


_UNFINISHED_BACKLOG_STATUSES = {"pending", "running", "in_progress", "claimed"}


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
        life_dir = root / "projects" / sid
        try:
            items = LifeMemory.open(life_dir).backlog.all()
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
            continuous = read_continuous_state(life_dir)
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
    running = list_running_daemons(global_root=root, exclude_sid=sid)
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
        path = root / "projects" / sid / _DAEMON_ADMISSION_FILE
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
    st = read_daemon_status(life_dir)
    if st.alive:
        _clear_daemon_admission(life_dir)
        return {"rc": 0, "already_alive": True, "daemon": _daemon_dict(st)}
    config = _worker_config_from_env(life_dir, root)
    if resume_continuous:
        continuous = read_continuous_state(life_dir)
        if continuous.enabled:
            config.continuous_objective = continuous.objective
            config.resume_continuous = True
    daemon_limit = _max_active_daemons(config)
    active_count = _active_daemon_count(config)
    if daemon_limit > 0 and active_count >= daemon_limit:
        if reclaim_idle:
            running = list_running_daemons(global_root=root, exclude_sid=sid)
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
                replaced = replace_project_daemon(
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
            "daemon": _daemon_dict(read_daemon_status(life_dir)),
        }
    try:
        rc = spawn_detached_daemon(config, quiet=True)
    except Exception as exc:  # noqa: BLE001 — return an actionable API result
        return {
            "rc": 2,
            "already_alive": False,
            "error": f"background executor failed to start: {type(exc).__name__}: {exc}",
            "daemon": _daemon_dict(read_daemon_status(life_dir)),
        }
    result = {
        "rc": rc,
        "already_alive": False,
        "daemon": _daemon_dict(read_daemon_status(life_dir)),
    }
    if rc != 0:
        active_count = _active_daemon_count(config)
        if daemon_limit > 0 and active_count >= daemon_limit:
            return {
                **_admission_required(
                    root=root,
                    sid=sid,
                    limit=daemon_limit,
                    active_count=active_count,
                    resume_continuous=resume_continuous,
                ),
                "daemon": _daemon_dict(read_daemon_status(life_dir)),
            }
        result["error"] = f"background executor failed to start (rc={rc})"
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
        items = LifeMemory.open(victim_dir).backlog.all()
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
        victim_status = read_daemon_status(victim_dir)
        if not victim_status.alive:
            return {
                "rc": 2,
                "error": f"session {victim_sid} is no longer running; refresh the list",
            }
        stop_rc = stop_daemon(victim_dir, timeout=2.0, force=True)
        if stop_rc not in {0, 1}:
            return {
                "rc": 2,
                "error": f"could not park {victim_sid} (stop rc={stop_rc})",
            }
        deadline = time.monotonic() + 5.0
        while read_daemon_status(victim_dir).alive and time.monotonic() < deadline:
            time.sleep(0.05)
        if read_daemon_status(victim_dir).alive:
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
        started = start_project_daemon(
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
    web equivalent of ``--new --continuous --objective``). At the host-wide
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
    life_dir = root / "projects" / sid
    effective_launch_cwd = (
        str(Path(launch_cwd).expanduser().resolve())
        if launch_cwd
        else str(Path.cwd().resolve())
    )
    meta = SessionMeta(
        id=sid,
        display_name=normalize_session_name(name),
        created=now,
        last_active=now,
        cwd=str(life_dir),
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
        from .manager_bridge import manager_continuous_handoff

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
        start_result = start_project_daemon(
            sid,
            global_root=root,
            resume_continuous=True,
        )
    # else: idle session — no continuous, no eager spawn. The Manager (via
    # /message) writes objectives and lazily spawns the executor when needed.

    daemon = _daemon_dict(read_daemon_status(life_dir))
    rc = int((start_result or {}).get("rc") or 0)
    response = {
        "sid": sid,
        "rc": rc,
        "spawned": bool(start_result is not None and rc == 0),
        "daemon": daemon,
        "objective": obj,
    }
    if start_result is not None:
        response["start"] = start_result
    return response


def set_project_launch_cwd(
    sid: str, launch_cwd: str, *, global_root: Path | str | None = None,
) -> bool | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    resolved_cwd = str(Path(launch_cwd).expanduser().resolve())

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


def stop_project_daemon(
    sid: str, *, drain: bool = False, force: bool = False,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Stop this project's daemon. Blocking (waits up to the drain timeout) —
    call from a threadpool in the async endpoint."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    rc = stop_daemon(life_dir, drain=drain, force=force)
    return {"rc": rc}


def upgrade_project_daemon(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Restart one executor from the currently loaded checkout."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    status = read_daemon_status(life_dir)
    if not status.alive or status.pid is None:
        started = start_project_daemon(
            sid,
            global_root=global_root,
            resume_continuous=True,
        )
        return None if started is None else {**started, "upgraded": True}

    root = _global_root(global_root)
    continuous = read_continuous_state(life_dir)
    stop_rc = stop_daemon(
        life_dir,
        drain=True,
        drain_timeout=1800.0,
        force=False,
    )
    if stop_rc not in {0, 1}:
        return {
            "rc": 2,
            "error": "daemon is still draining active work; retry upgrade after it exits",
        }
    if continuous.enabled:
        write_continuous_config(
            life_dir,
            enabled=True,
            objective=continuous.objective,
        )
    started = start_project_daemon(
        sid,
        global_root=root,
        resume_continuous=continuous.enabled,
    )
    return None if started is None else {**started, "upgraded": True}


def update_project(
    sid: str, *, name: str, global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Update operator-owned session metadata without changing mission state."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    try:
        objective = read_continuous_state(life_dir).objective
    except Exception:  # noqa: BLE001 — legacy metadata repair is best-effort
        objective = ""
    normalized_name = normalize_session_name(name)

    def _rename(meta: SessionMeta) -> None:
        now = time.time()
        if not meta.created:
            meta.created = now
        if not meta.last_active:
            meta.last_active = now
        if not meta.cwd:
            meta.cwd = str(life_dir)
        if not meta.objective:
            meta.objective = objective
        meta.display_name = normalized_name

    meta = update_session_meta(root, sid, _rename, create=True)
    if meta is None:
        return None
    return {"ok": True, "sid": sid, "name": meta.display_name}


def delete_project(
    sid: str,
    *,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Reversibly remove a stopped session by moving it to projects_trash."""
    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with session_lifecycle_lock(lock_root, sid):
        with session_meta_lock(root, sid):
            life_dir = project_life_dir(sid, global_root=root)
            if life_dir is None:
                return None
            status = read_daemon_status(life_dir)
            if status.alive:
                return {
                    "ok": False,
                    "sid": sid,
                    "error": "pause the daemon before deleting this session",
                }

            date = time.strftime("%Y%m%d", time.localtime())
            dest_parent = root / "projects_trash" / date
            dest_parent.mkdir(parents=True, exist_ok=True)
            dest = dest_parent / sid
            if dest.exists():
                dest = dest_parent / f"{sid}.{int(time.time())}"
            shutil.move(str(life_dir), str(dest))
            return {
                "ok": True,
                "sid": sid,
                "trash_path": str(dest.relative_to(root)),
            }


def list_trashed_projects(
    *, global_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    root = _global_root(global_root)
    trash_root = root / "projects_trash"
    out: list[dict[str, Any]] = []
    try:
        candidates = [
            path
            for date_dir in trash_root.iterdir()
            if date_dir.is_dir()
            for path in date_dir.iterdir()
            if path.is_dir()
        ]
    except OSError:
        candidates = []
    for path in candidates:
        payload: dict[str, Any] = {}
        try:
            value = json.loads((path / "session.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                payload = value
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        sid = str(payload.get("id") or path.name.split(".", 1)[0]).strip()
        label = str(
            payload.get("display_name")
            or payload.get("objective")
            or sid
        ).strip()
        try:
            trashed_at = path.stat().st_mtime
        except OSError:
            trashed_at = 0.0
        out.append({
            "sid": sid,
            "label": label or sid,
            "launch_cwd": str(payload.get("launch_cwd") or ""),
            "trash_path": str(path.relative_to(root)),
            "trashed_at": trashed_at,
        })
    out.sort(key=lambda row: float(row.get("trashed_at") or 0.0), reverse=True)
    return out


def restore_trashed_project(
    trash_path: str,
    *,
    global_root: Path | str | None = None,
    existing_roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    root = _global_root(global_root).resolve()
    trash_root = (root / "projects_trash").resolve()
    try:
        source = (root / trash_path).resolve()
    except (OSError, ValueError):
        return None
    try:
        relative = source.relative_to(trash_root)
    except ValueError:
        return None
    if (
        len(relative.parts) != 2
        or len(relative.parts[0]) != 8
        or not relative.parts[0].isdigit()
        or source.is_symlink()
        or source.parent.is_symlink()
        or not source.is_dir()
    ):
        return None
    payload: dict[str, Any] = {}
    try:
        value = json.loads((source / "session.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            payload = value
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    sid = str(payload.get("id") or source.name.split(".", 1)[0]).strip()
    if not sid or Path(sid).name != sid:
        return None
    destination = (root / "projects" / sid).resolve()
    if destination.parent != (root / "projects").resolve():
        return None
    roots_to_check = tuple(existing_roots or (root,))
    lock_root = _global_root(roots_to_check[0])
    with session_lifecycle_lock(lock_root, sid):
        if not source.is_dir():
            return None
        if any(
            project_life_dir(sid, global_root=candidate) is not None
            for candidate in roots_to_check
        ):
            return {
                "ok": False,
                "sid": sid,
                "error": "a live session with this id already exists",
            }
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return {"ok": True, "sid": sid}


def set_continuous(
    sid: str, *, enabled: bool, objective: str = "",
    global_root: Path | str | None = None,
) -> bool | None:
    """Start/stop this project's continuous (self-directed) campaign by writing
    the hot-reloadable ``continuous.json``."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    if not enabled:
        from .manager_bridge import disable_manager_continuous

        disable_manager_continuous(sid, life_dir=life_dir)
        return True
    from .manager_bridge import manager_continuous_handoff

    manager_continuous_handoff(
        sid,
        objective.strip(),
        global_root=global_root,
    )
    return True


# ---------------------------------------------------------------------------
# Wave-1 read/inspect + backlog-lifecycle helpers — 1:1 with the Python
# cockpit's /status /journal /note /doctor /config /identity /transcript and
# the /done /skip /rm /stop backlog commands. All delegate; fail-soft per part.
# ---------------------------------------------------------------------------

def get_status(sid: str, *, global_root: Path | str | None = None) -> dict[str, Any] | None:
    """Composite of the Python /status view: identity, pending backlog + pending
    questions, recent journal, continuous, inbox count, daemon, active role."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)

    def _safe(fn, default):  # noqa: ANN001
        try:
            return fn()
        except Exception:  # noqa: BLE001 — /status must never raise
            return default

    identity = _safe(lambda: mem.identity.read().strip(), "")
    items = _safe(lambda: mem.backlog.all(), [])
    pending = [it.to_jsonable() for it in items if it.status == "pending"]
    questions = [
        it.to_jsonable() for it in items
        if it.to_jsonable().get("pending_question")
    ]
    journal = _safe(lambda: [e.to_jsonable() for e in mem.journal.tail(3)], [])
    cont = _safe(lambda: read_continuous_state(life_dir), None)
    continuous = (
        {"enabled": cont.enabled, "objective": cont.objective,
         "done_reason": cont.done_reason, "done_at": cont.done_at}
        if cont is not None else {"enabled": False, "objective": ""}
    )
    inbox_pending = _safe(lambda: count_pending_inbox_messages(life_dir), 0)
    daemon = _safe(lambda: _daemon_dict(read_daemon_status(life_dir)),
                   {"alive": False, "pid": None})
    roles = _safe(lambda: _roles_list(resolve_all_roles(env=os.environ),
                                      role_activity(life_dir)), [])
    active = next((r["role"] for r in roles if r["active"]), None)
    return {
        "identity": identity,
        "backlog_pending": pending,
        "pending_questions": questions,
        "journal": journal,
        "continuous": continuous,
        "inbox_pending": inbox_pending,
        "daemon": daemon,
        "roles": roles,
        "active_role": active,
        "request_usage": provider_usage_snapshot(root=_global_root(global_root)),
    }


def get_journal(
    sid: str, *, n: int = 10, global_root: Path | str | None = None
) -> list[dict[str, Any]] | None:
    """Recent journal entries (mission summaries / notes) — the /journal tail."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    event_path = life_dir / EVENT_FILE
    signature = (
        _stat_signature(event_path),
        _stat_signature(event_path.with_suffix(event_path.suffix + ".1")),
    )
    key = (str(life_dir.resolve()), max(1, n))
    with _JOURNAL_TAIL_CACHE_LOCK:
        cached = _JOURNAL_TAIL_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    try:
        rows = [e.to_jsonable() for e in LifeMemory.open(life_dir).journal.tail(max(1, n))]
    except Exception:  # noqa: BLE001
        rows = []
    with _JOURNAL_TAIL_CACHE_LOCK:
        _JOURNAL_TAIL_CACHE[key] = (signature, rows)
    return rows




def add_project_note(
    sid: str, text: str, *, global_root: Path | str | None = None
) -> str | None:
    """Append a manual user.note to the timeline — the /note command."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    return append_note(LifeMemory.open(life_dir), text)


def get_backlog_item(
    sid: str, item_id: str, *, global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return one full backlog item (compact snapshots intentionally omit it)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        item = next(
            (row for row in LifeMemory.open(life_dir).backlog.all() if row.id == item_id),
            None,
        )
    except Exception:  # noqa: BLE001
        return None
    return item.to_jsonable() if item is not None else None


def abort_project_mission(
    sid: str,
    *,
    reason: str = "",
    requested_by: str = "operator",
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Request an immediate abort for this project's current mission."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    from ..tools.mission_control import request_current_mission_abort

    requested, item_id = request_current_mission_abort(
        life_dir,
        reason=reason or "operator requested immediate stop",
        requested_by=requested_by,
    )
    if requested:
        return {
            "requested": True,
            "item_id": item_id,
            "message": f"Stop requested for running task {item_id}.",
        }
    return {
        "requested": False,
        "item_id": None,
        "message": "No running task to abort. Pending tasks were left unchanged.",
    }


def dispose_backlog(
    sid: str, item_id: str, op: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """Backlog disposition — /done (mark_done) / /skip / /rm (status=skipped).
    Returns the updated item, or None if the project or item is unknown."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    bl = LifeMemory.open(life_dir).backlog
    item = bl.mark_done(item_id) if op == "done" else bl.update(item_id, status="skipped")
    return item.to_jsonable() if item is not None else None


def stop_backlog_iteration(
    sid: str, item_id: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """/stop — disable a task's auto-iteration (does not delete it)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    item = LifeMemory.open(life_dir).backlog.stop_iteration(item_id)
    return item.to_jsonable() if item is not None else None


def _daemon_log_tail(life_dir: Path, *, lines: int = 12) -> str:
    try:
        text = (life_dir / "daemon.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def get_doctor(sid: str, *, global_root: Path | str | None = None) -> dict[str, Any] | None:
    """Run the daemon-executor diagnostics — /doctor: ranked checks + the single
    recommended fix + a recent daemon.log tail. Reuses tools.doctor.run_diagnostics."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    checks = run_diagnostics(life_dir, global_root=root)
    rows = [{"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix} for c in checks]
    # run_diagnostics returns checks ordered by recommendation priority, so the
    # first failing check is the root-cause fix to surface first.
    recommended = next((r for r in rows if not r["ok"]), None)
    return {"checks": rows, "recommended": recommended, "log_tail": _daemon_log_tail(life_dir)}


def get_config(*, global_root: Path | str | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Runtime settings snapshot — /config: per-role backend/model/effort + every
    ARGUS_* knob. Env/process-global (not per-project). Reuses build_config_snapshot."""
    return build_config_snapshot(env=os.environ)


def get_identity(sid: str, *, global_root: Path | str | None = None) -> str | None:
    """The operator identity card text — /identity view (ensures a default)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)
    try:
        mem.identity.ensure_default()
        return mem.identity.read()
    except Exception:  # noqa: BLE001
        return ""


_CONFIG_ALIASES = {
    "backend": "ARGUS_SKILL_RUNNER_BACKEND",
    "engineer_backend": "ARGUS_SKILL_ENGINEER_BACKEND",
    "reviewer_backend": "ARGUS_SKILL_REVIEWER_BACKEND",
    "planner_backend": "ARGUS_SKILL_PLANNER_BACKEND",
    "manager_backend": "ARGUS_SKILL_MANAGER_BACKEND",
    "model": "ARGUS_SKILL_MODEL",
    "engineer_model": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer_model": "ARGUS_SKILL_REVIEWER_MODEL",
    "planner_model": "ARGUS_SKILL_PLAN_MODEL",
    "manager_model": "ARGUS_SKILL_MODEL",
    "engineer_effort": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer_effort": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "planner_effort": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "manager_effort": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "per_mission_cap": "ARGUS_SKILL_PER_MISSION_CAP_USD",
    "daily_cap": "ARGUS_SKILL_DAILY_CAP_USD",
    "global_daily_cap": "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
    "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "daemon_limit": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
    "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
    "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
    "safe_mode": "ARGUS_SKILL_SAFE_MODE",
    "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
    "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
}


def set_operator_config(name: str, value: str) -> dict[str, Any]:
    from ..core.knob_store import write_persisted_knob
    from ..core.knobs import cockpit_editable_names, normalize_cockpit_knob_value

    raw = (name or "").strip()
    env_name = _CONFIG_ALIASES.get(raw.lower(), raw.upper())
    allowed = set(cockpit_editable_names()) | {"ARGUS_SKILL_RUNNER_BACKEND"}
    if env_name not in allowed:
        raise ValueError(f"config key is not cockpit-editable: {raw}")
    val = normalize_cockpit_knob_value(env_name, value)
    write_persisted_knob(env_name, val)
    os.environ[env_name] = val
    return {"name": env_name, "value": val, "restart_required": True}


_BUDGET_BATCH_ALIASES = frozenset({
    "per_mission_cap",
    "daily_cap",
    "global_daily_cap",
    "codex_daily_requests",
    "copilot_daily_requests",
    "copilot_daily_premium",
})


def set_budget_config(values: dict[str, str]) -> dict[str, Any]:
    from ..core.knob_store import write_persisted_knobs
    from ..core.knobs import normalize_cockpit_knob_value

    unknown = sorted(set(values) - _BUDGET_BATCH_ALIASES)
    if unknown:
        raise ValueError(f"unsupported budget setting(s): {', '.join(unknown)}")
    normalized: dict[str, str] = {}
    for alias in _BUDGET_BATCH_ALIASES:
        if alias not in values:
            raise ValueError(f"missing budget setting: {alias}")
        env_name = _CONFIG_ALIASES[alias]
        normalized[env_name] = normalize_cockpit_knob_value(
            env_name,
            str(values[alias]),
        )
    if not write_persisted_knobs(normalized):
        raise RuntimeError("budget settings could not be persisted")
    os.environ.update(normalized)
    return {"values": normalized, "restart_required": True}


def set_identity(
    sid: str, text: str, *, global_root: Path | str | None = None,
) -> bool | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)
    mem.identity.path.parent.mkdir(parents=True, exist_ok=True)
    mem.identity.path.write_text((text or "").rstrip() + "\n", encoding="utf-8")
    return True


def run_skill_command(tokens: list[str]) -> str:
    from ..apps._life_actions import render_skills_cmd
    return render_skills_cmd(tokens)


def get_transcript(
    sid: str, *, n: int = 20, global_root: Path | str | None = None
) -> list[dict[str, Any]] | None:
    """Recent operator↔argus conversation turns — for transcript replay / resume."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        return read_turns(life_dir, limit=max(1, n))
    except Exception:  # noqa: BLE001
        return []


async def tail_events(
    life_dir: Path,
    *,
    replay_limit: int = 40,
    poll_interval: float = 0.25,
):
    """Async generator: yield the last ``replay_limit`` events, then every new
    ``events.jsonl`` line as it is appended.

    Roll-safe: ``events.jsonl`` rotates to ``.jsonl.1`` at 100MB (the daemon's
    ``event_log`` writer), which shrinks/replaces the live file. We track
    ``(st_ino, st_size)`` and, on a shrink or inode change, restart the byte
    offset from 0 so the freshly-rotated log is followed without dropping or
    duplicating a truncated line. A partial trailing line (no ``\\n`` yet) is
    buffered until its newline arrives.
    """
    path = life_dir / EVENT_FILE

    # Fix the tail baseline BEFORE replaying, so an event appended between the
    # replay snapshot and the first poll is neither dropped nor duplicated:
    # replay covers up to `offset`, the tail covers everything strictly after.
    offset = 0
    inode: int | None = None
    if path.exists():
        stat = path.stat()
        offset = stat.st_size
        inode = stat.st_ino

    for ev in _read_recent_jsonl_events(path, limit=replay_limit):
        yield ev

    buf = b""

    while True:
        await asyncio.sleep(poll_interval)
        try:
            stat = path.stat()
        except OSError:
            continue  # file gone mid-roll — wait for it to reappear
        if stat.st_ino != inode or stat.st_size < offset:
            offset, inode, buf = 0, stat.st_ino, b""  # rotated/truncated → restart
        if stat.st_size <= offset:
            continue
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        except OSError:
            continue
        buf += chunk
        *complete, buf = buf.split(b"\n")  # keep the last (possibly partial) line
        for raw in complete:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(ev, dict):
                yield ev


# ---------------------------------------------------------------------------
# FastAPI app (imports the [web] extra lazily)
# ---------------------------------------------------------------------------

def create_app(
    *,
    global_root: Path | str | None = None,
    auth_token: str | None = None,
    session_roots: list[Path | str] | None = None,
):
    """Build the FastAPI app. Requires the ``[web]`` extra (fastapi).

    ``auth_token`` (or env ``ARGUS_SKILL_WEB_TOKEN``) turns on bearer auth: when
    set, every command POST needs ``Authorization: Bearer <token>`` and the WS
    upgrade needs ``?token=<token>`` (browsers cannot set WS headers). With no
    token configured the API is unauthenticated — safe only behind the default
    ``127.0.0.1`` bind.
    """
    from fastapi import (
        Depends,
        FastAPI,
        Header,
        HTTPException,
        Query,
        Response,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from starlette.concurrency import run_in_threadpool
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.responses import FileResponse, StreamingResponse

    token = auth_token if auth_token is not None else os.environ.get("ARGUS_SKILL_WEB_TOKEN")
    primary_root = _global_root(global_root).expanduser().resolve()
    roots: list[Path] = [primary_root]
    if session_roots is not None:
        candidates = [Path(root).expanduser() for root in session_roots]
    elif global_root is None:
        candidates = [
            Path(root).expanduser()
            for root in os.environ.get("ARGUS_SKILL_WEB_SESSION_ROOTS", "").split(os.pathsep)
            if root.strip()
        ]
    else:
        candidates = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)

    api_meta = build_api_meta()
    app = FastAPI(
        title="argus-skill web API",
        version=str(api_meta["runtime"]["package_version"]),
    )

    @app.middleware("http")
    async def _add_protocol_headers(request, call_next):  # noqa: ANN001
        started_at = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            record_metric(
                _global_root(global_root),
                "web.request",
                labels={
                    "method": request.method,
                    "path": http_route_template(request.scope, request.url.path),
                    "status": 500,
                },
                fields={"duration_ms": (time.monotonic() - started_at) * 1_000},
            )
            raise
        record_metric(
            _global_root(global_root),
            "web.request",
            labels={
                "method": request.method,
                "path": http_route_template(request.scope, request.url.path),
                "status": response.status_code,
            },
            fields={"duration_ms": (time.monotonic() - started_at) * 1_000},
        )
        response.headers["X-Argus-Protocol"] = protocol_header()
        revision = api_meta["runtime"].get("revision")
        if revision:
            response.headers["X-Argus-Revision"] = str(revision)
        response.headers["X-Argus-Release"] = str(
            api_meta["runtime"].get("release_id") or "unknown"
        )
        cache_control = _web_cache_control(request.url.path)
        if cache_control:
            response.headers["Cache-Control"] = cache_control
        return response

    @app.on_event("shutdown")
    def _shutdown_warm_manager_clients() -> None:
        # Explicitly terminate module-level Copilot ACP clients. Otherwise an
        # old Web process can remain resident after Uvicorn shuts down, leaving
        # stale Copilot processes alive across repeated cockpit launches.
        try:
            from .manager_bridge import shutdown_manager_bridge

            shutdown_manager_bridge()
        except Exception:  # noqa: BLE001
            pass

    # Localhost dev only: allow the Vite dev server + same-origin. Not a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://localhost:8799", "http://127.0.0.1:8799",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def _require_auth(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return  # unauthenticated (localhost-only) mode
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def _root_for_project(sid: str) -> Path | None:
        for root in roots:
            if project_life_dir(sid, global_root=root) is not None:
                return root
        return None

    def _project_root_or_404(sid: str) -> Path:
        root = _root_for_project(sid)
        if root is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return root

    def _resolve_or_404(sid: str) -> Path:
        root = _project_root_or_404(sid)
        life_dir = project_life_dir(sid, global_root=root)
        if life_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return life_dir

    def _machine_projects(
        *, limit: int, include_empty: bool,
    ) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in roots:
            try:
                root_session_ids = {
                    path.name
                    for path in (root / "projects").iterdir()
                    if path.is_dir()
                }
            except OSError:
                root_session_ids = set()
            root_limit = limit + len(seen.intersection(root_session_ids))
            for project in list_projects(
                global_root=root,
                limit=root_limit,
                include_empty=include_empty,
            ):
                sid = str(project.get("id") or "")
                if not sid or sid in seen:
                    continue
                projects.append(project)
            # Routing uses the first root containing an ID, so reserve every ID
            # from that root even when its session is empty or outside `limit`.
            seen.update(root_session_ids)
        projects.sort(
            key=lambda project: float(project.get("last_active") or 0.0),
            reverse=True,
        )
        return projects[:limit]

    def _404_if_none(value, sid: str):
        if value is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return value

    class _TaskIn(BaseModel):
        text: str
        # Lazy daemon spawn (default on): queueing a task starts this project's
        # executor if none is alive — the same behaviour as the Python cockpit's
        # _autospawn_daemon_for_task, so `argus` + submit a task actually runs it.
        autostart_daemon: bool = True

    class _NudgeIn(BaseModel):
        text: str

    class _MessageIn(BaseModel):
        text: str

    class _AnswerIn(BaseModel):
        text: str

    class _AbortMissionIn(BaseModel):
        reason: str = ""

    class _CommandIn(BaseModel):
        command_id: str = ""
        expected_revision: int | None = None

    class _CreateDaemonIn(_CommandIn):
        objective: str = ""
        name: str = ""
        launch_cwd: str = ""

    class _LaunchCwdIn(BaseModel):
        launch_cwd: str

    class _StopIn(_CommandIn):
        drain: bool = False
        force: bool = False

    class _ReplaceDaemonIn(_CommandIn):
        victim_sid: str
        resume_continuous: bool = False

    class _ContinuousIn(BaseModel):
        enabled: bool
        objective: str = ""

    class _NoteIn(BaseModel):
        text: str

    class _PlanIn(BaseModel):
        text: str

    class _ConfigSetIn(BaseModel):
        name: str
        value: str

    class _BudgetSetIn(BaseModel):
        values: dict[str, str]

    class _ProjectUpdateIn(BaseModel):
        name: str

    class _IdentitySetIn(BaseModel):
        text: str

    class _SkillsIn(BaseModel):
        args: str = "ls"

    class _DisposeIn(BaseModel):
        op: str = "done"  # done | skip | rm

    # ── read endpoints (M0) ───────────────────────────────────────────────

    @app.get("/api/meta")
    def _meta(
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        if not token or authorization == f"Bearer {token}":
            return api_meta
        runtime = {
            **api_meta["runtime"],
            "source_root": "<redacted>",
            "configured_source_root": None,
            "source_root_matches_config": None,
            "executable": "<redacted>",
        }
        return {**api_meta, "runtime": runtime}

    @app.get("/api/metrics", dependencies=[Depends(_require_auth)])
    def _metrics() -> dict[str, Any]:
        return metrics_snapshot(root=_global_root(global_root))

    @app.get("/metrics", dependencies=[Depends(_require_auth)])
    def _prometheus_metrics() -> Response:
        snapshot = metrics_snapshot(root=_global_root(global_root))
        return Response(
            render_prometheus(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/projects")
    def _projects(
        limit: int = Query(100, ge=1, le=2000),
        include_empty: bool = Query(False),
    ) -> dict[str, Any]:
        return {
            "projects": _machine_projects(limit=limit, include_empty=include_empty),
            "local_cwd": str(Path.cwd().resolve()),
        }

    @app.get("/api/trash", dependencies=[Depends(_require_auth)])
    def _trash(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        query: str = Query("", max_length=200),
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for index, root in enumerate(roots):
            for entry in list_trashed_projects(global_root=root):
                entries.append({
                    **entry,
                    "trash_id": f"{index}:{entry['trash_path']}",
                })
        entries.sort(
            key=lambda entry: float(entry.get("trashed_at") or 0.0),
            reverse=True,
        )
        needle = query.strip().casefold()
        if needle:
            entries = [
                entry
                for entry in entries
                if needle in " ".join((
                    str(entry.get("sid") or ""),
                    str(entry.get("label") or ""),
                    str(entry.get("launch_cwd") or ""),
                    str(entry.get("trash_path") or ""),
                )).casefold()
            ]
        return {
            "entries": entries[offset:offset + limit],
            "total": len(entries),
            "offset": offset,
            "limit": limit,
        }

    @app.post(
        "/api/trash/{trash_id:path}/restore",
        dependencies=[Depends(_require_auth)],
    )
    async def _restore_trash(trash_id: str) -> dict[str, Any]:
        prefix, separator, relative = trash_id.partition(":")
        if not separator or not prefix.isdigit():
            raise HTTPException(status_code=404, detail="unknown trash entry")
        index = int(prefix)
        if index < 0 or index >= len(roots):
            raise HTTPException(status_code=404, detail="unknown trash entry")
        entry = next(
            (
                item
                for item in list_trashed_projects(global_root=roots[index])
                if item["trash_path"] == relative
            ),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown trash entry")
        if any(
            project_life_dir(str(entry["sid"]), global_root=root) is not None
            for root in roots
        ):
            raise HTTPException(
                status_code=409,
                detail="a session with this id already exists",
            )
        result = await run_in_threadpool(
            restore_trashed_project,
            relative,
            global_root=roots[index],
            existing_roots=roots,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown trash entry")
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("error"))
        return result

    @app.post("/api/daemons", dependencies=[Depends(_require_auth)])
    async def _create_daemon(body: _CreateDaemonIn) -> dict[str, Any]:
        """Create a brand-new daemon (session). The objective is OPTIONAL — with
        none, the daemon is idle and the user just talks to the Manager (which
        writes its own objectives). Threadpool: fs writes + optional fork."""
        root = _global_root(global_root)
        receipt = await run_in_threadpool(
            execute_daemon_command,
            root,
            operation="create",
            args={
                "objective": body.objective,
                "name": body.name,
                "launch_cwd": body.launch_cwd,
            },
            command_id=body.command_id or None,
            expected_revision=body.expected_revision,
            issuer="webapi",
            handler=lambda: create_daemon(
                body.objective,
                name=body.name,
                launch_cwd=body.launch_cwd,
                global_root=global_root,
            ),
        )
        return _command_response(receipt)

    @app.post("/api/projects/{sid}/launch-cwd", dependencies=[Depends(_require_auth)])
    async def _set_launch_cwd(sid: str, body: _LaunchCwdIn) -> dict[str, bool]:
        updated = await run_in_threadpool(
            set_project_launch_cwd,
            sid,
            body.launch_cwd,
            global_root=_project_root_or_404(sid),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return {"ok": True}

    @app.patch("/api/projects/{sid}", dependencies=[Depends(_require_auth)])
    async def _update_project(sid: str, body: _ProjectUpdateIn) -> dict[str, Any]:
        return _404_if_none(
            await run_in_threadpool(
                update_project,
                sid,
                name=body.name,
                global_root=_project_root_or_404(sid),
            ),
            sid,
        )

    @app.delete("/api/projects/{sid}", dependencies=[Depends(_require_auth)])
    async def _delete_project(sid: str) -> dict[str, Any]:
        result = _404_if_none(
            await run_in_threadpool(
                delete_project,
                sid,
                global_root=_project_root_or_404(sid),
                lifecycle_root=_global_root(global_root),
            ),
            sid,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("error", "project is busy"))
        return result

    @app.get("/api/projects/{sid}/snapshot")
    def _snapshot(
        sid: str,
        events_limit: int = Query(80, ge=1, le=500),
        compact: bool = Query(False),
    ) -> dict[str, Any]:
        return _404_if_none(
            build_snapshot(
                sid,
                global_root=_project_root_or_404(sid),
                events_limit=events_limit,
                compact=compact,
            ),
            sid,
        )

    @app.get("/api/projects/{sid}/events")
    def _events(
        sid: str,
        limit: int = Query(80, ge=1, le=1000),
        view: str = Query("full", pattern="^(full|ui)$"),
    ) -> dict[str, Any]:
        life_dir = _resolve_or_404(sid)
        if view == "ui":
            events = _read_jsonl_tail_history(
                life_dir / EVENT_FILE,
                limit,
                predicate=_event_visible_in_web_ui,
            )
        else:
            events = _read_recent_project_events(life_dir, limit=limit)
        return {"events": events}

    @app.get(
        "/api/projects/{sid}/artifacts",
        dependencies=[Depends(_require_auth)],
    )
    def _artifacts(sid: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "artifacts": _404_if_none(
                list_project_artifacts(
                    sid, global_root=_project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get(
        "/api/projects/{sid}/artifact",
        dependencies=[Depends(_require_auth)],
    )
    def _artifact(
        sid: str,
        response: Response,
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        artifact = get_project_artifact(
            sid, path, global_root=_project_root_or_404(sid)
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        return artifact

    @app.get(
        "/api/projects/{sid}/artifact/raw",
        dependencies=[Depends(_require_auth)],
    )
    def _artifact_raw(
        sid: str,
        path: str = Query(..., min_length=1),
        download: bool = Query(False),
    ):
        resolved = _resolved_project_artifact(
            sid, path, global_root=_project_root_or_404(sid)
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        info, file_path = resolved
        safe_inline = info["kind"] in {"image", "pdf", "audio", "video"}
        media_type = (
            "application/octet-stream" if download
            else str(info["mime"]) if safe_inline
            else "text/plain; charset=utf-8"
        )
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=str(info["name"]),
            content_disposition_type="attachment" if download else "inline",
            headers={
                # HTML/SVG and unknown binaries intentionally arrive as plain
                # text; prohibit browser MIME sniffing from turning them back
                # into executable content.
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @app.get(
        "/api/projects/{sid}/git-diff",
        dependencies=[Depends(_require_auth)],
    )
    def _git_diff(sid: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return _404_if_none(
            _project_git_diff(sid, global_root=_project_root_or_404(sid)),
            sid,
        )

    # ── command endpoints (M1, auth-gated) ────────────────────────────────

    @app.post("/api/projects/{sid}/tasks", dependencies=[Depends(_require_auth)])
    async def _post_task(sid: str, body: _TaskIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty task text")
        project_root = _project_root_or_404(sid)
        try:
            response = _404_if_none(
                await run_in_threadpool(
                    enqueue_task_command,
                    sid,
                    body.text,
                    autostart_daemon=body.autostart_daemon,
                    global_root=project_root,
                    lifecycle_root=_global_root(global_root),
                ),
                sid,
            )
        except ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return response

    @app.post("/api/projects/{sid}/nudge", dependencies=[Depends(_require_auth)])
    def _post_nudge(sid: str, body: _NudgeIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty nudge text")
        _404_if_none(
            enqueue_nudge(
                sid, body.text, global_root=_project_root_or_404(sid)
            ),
            sid,
        )
        return {"ok": True}

    @app.post(
        "/api/projects/{sid}/backlog/{item_id}/answer",
        dependencies=[Depends(_require_auth)],
    )
    async def _answer_pending(
        sid: str,
        item_id: str,
        body: _AnswerIn,
    ) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty answer")
        project_root = _project_root_or_404(sid)
        result = await run_in_threadpool(
            answer_pending_question,
            sid,
            item_id,
            body.text,
            global_root=project_root,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown backlog item")
        if result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        result["daemon"] = await run_in_threadpool(
            start_project_daemon,
            sid,
            global_root=project_root,
            reclaim_idle=True,
        )
        return result

    @app.post("/api/projects/{sid}/message", dependencies=[Depends(_require_auth)])
    async def _post_message(sid: str, body: _MessageIn) -> dict[str, Any]:
        """The Manager front-door: route natural language through the SAME triage
        the Manager pipeline uses. A conversational message ("你好") gets a Manager
        reply and never becomes a mission; only TEAM/complex work is enqueued.
        Runs in a threadpool because the Manager triage is a blocking LLM call.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        project_root = _project_root_or_404(sid)
        from .manager_bridge import manager_message

        result = await run_in_threadpool(
            manager_message, sid, body.text, global_root=project_root
        )
        # A task classification lazily spawns the executor, mirroring /tasks.
        if result.get("kind") == "task" and not result.get("daemon_alive"):
            result["daemon"] = await run_in_threadpool(
                start_project_daemon, sid, global_root=project_root,
                resume_continuous=bool(result.get("continuous")),
                reclaim_idle=True,
            )
        return result

    @app.post("/api/projects/{sid}/message/stream", dependencies=[Depends(_require_auth)])
    def _post_message_stream(sid: str, body: _MessageIn):
        """Streaming twin of ``/message`` (Server-Sent Events).

        The Manager turn is a blocking CLI call, but copilot/codex emit the reply
        as blocks *during* the turn and phase transitions fire live — the plain
        POST throws all that away, so the front-end looks frozen until the whole
        turn ends. Here we run ``manager_message`` on a worker thread with an
        ``on_fragment`` callback that pushes each block / phase onto a thread-safe
        queue; a synchronous generator drains the queue into SSE ``data:`` frames.
        A sync generator means Starlette runs it in a threadpool — no asyncio
        queue bridging, which keeps this robust and easy to reason about.

        Frame kinds: ``{"type":"phase",...}`` · ``{"type":"delta",...}`` ·
        ``{"type":"done","result":{...}}`` · ``{"type":"error","error":...}``.
        The blocking ``/message`` stays as the fallback for non-streaming clients.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        project_root = _project_root_or_404(sid)
        from .manager_bridge import manager_message

        q: "queue.Queue[dict | None]" = queue.Queue()

        def _run() -> None:
            def _on_fragment(kind: str, payload: dict) -> None:
                q.put({"type": kind, **payload})
            try:
                result = manager_message(
                    sid,
                    body.text,
                    global_root=project_root,
                    on_fragment=_on_fragment,
                )
                # Mirror the blocking endpoint: a task classification lazily spawns
                # the executor so streamed dispatch behaves like /message + /tasks.
                if result.get("kind") == "task" and not result.get("daemon_alive"):
                    try:
                        result["daemon"] = start_project_daemon(
                            sid,
                            global_root=project_root,
                            resume_continuous=bool(result.get("continuous")),
                            reclaim_idle=True,
                        )
                    except Exception as exc:  # noqa: BLE001 — surface failure in done frame
                        result["daemon"] = {
                            "rc": 2,
                            "error": (
                                "background executor failed to start: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        }
                q.put({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "error", "error": str(exc)})
            finally:
                q.put(None)  # sentinel: generator stops

        threading.Thread(target=_run, name=f"manager-stream-{sid}", daemon=True).start()

        def _gen():
            for item in _iter_manager_stream_items(
                q,
                heartbeat_s=_manager_stream_heartbeat_seconds(),
            ):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/projects/{sid}/daemon/start", dependencies=[Depends(_require_auth)])
    async def _daemon_start(
        sid: str,
        body: _CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or _CommandIn()
        life_dir = _resolve_or_404(sid)
        project_root = _project_root_or_404(sid)
        receipt = await run_in_threadpool(
            execute_daemon_command,
            life_dir,
            operation="start",
            args={"resume_continuous": True},
            command_id=command.command_id or None,
            expected_revision=command.expected_revision,
            issuer="webapi",
            handler=lambda: _404_if_none(
                start_project_daemon(
                    sid,
                    global_root=project_root,
                    resume_continuous=True,
                ),
                sid,
            ),
        )
        return _command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/stop", dependencies=[Depends(_require_auth)])
    async def _daemon_stop(sid: str, body: _StopIn | None = None) -> dict[str, Any]:
        b = body or _StopIn()
        life_dir = _resolve_or_404(sid)
        project_root = _project_root_or_404(sid)
        operation = "kill" if b.force else "drain" if b.drain else "stop"
        receipt = await run_in_threadpool(
            execute_daemon_command,
            life_dir,
            operation=operation,
            args={"drain": b.drain, "force": b.force},
            command_id=b.command_id or None,
            expected_revision=b.expected_revision,
            issuer="webapi",
            handler=lambda: _404_if_none(
                stop_project_daemon(
                    sid,
                    drain=b.drain,
                    force=b.force,
                    global_root=project_root,
                ),
                sid,
            ),
        )
        return _command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/replace", dependencies=[Depends(_require_auth)])
    async def _daemon_replace(sid: str, body: _ReplaceDaemonIn) -> dict[str, Any]:
        life_dir = _resolve_or_404(sid)
        project_root = _project_root_or_404(sid)
        receipt = await run_in_threadpool(
            execute_daemon_command,
            life_dir,
            operation="replace",
            args={
                "victim_sid": body.victim_sid,
                "resume_continuous": body.resume_continuous,
            },
            command_id=body.command_id or None,
            expected_revision=body.expected_revision,
            issuer="webapi",
            handler=lambda: _404_if_none(
                replace_project_daemon(
                    sid,
                    body.victim_sid,
                    global_root=project_root,
                    resume_continuous=body.resume_continuous,
                ),
                sid,
            ),
        )
        return _command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/upgrade", dependencies=[Depends(_require_auth)])
    async def _daemon_upgrade(
        sid: str,
        body: _CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or _CommandIn()
        life_dir = _resolve_or_404(sid)
        project_root = _project_root_or_404(sid)
        receipt = await run_in_threadpool(
            execute_daemon_command,
            life_dir,
            operation="upgrade",
            args={},
            command_id=command.command_id or None,
            expected_revision=command.expected_revision,
            issuer="webapi",
            handler=lambda: _404_if_none(
                upgrade_project_daemon(sid, global_root=project_root),
                sid,
            ),
        )
        return _command_response(receipt)

    @app.post("/api/projects/{sid}/continuous", dependencies=[Depends(_require_auth)])
    async def _post_continuous(sid: str, body: _ContinuousIn) -> dict[str, Any]:
        project_root = _project_root_or_404(sid)
        try:
            _404_if_none(
                await run_in_threadpool(
                    set_continuous,
                    sid,
                    enabled=body.enabled,
                    objective=body.objective,
                    global_root=project_root,
                ),
                sid,
            )
        except ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response: dict[str, Any] = {"ok": True}
        if body.enabled:
            response["daemon"] = await run_in_threadpool(
                start_project_daemon,
                sid,
                global_root=project_root,
                resume_continuous=True,
            )
        return response

    # ── Wave-1 read/inspect endpoints ─────────────────────────────────────

    @app.get("/api/projects/{sid}/status")
    def _status(sid: str) -> dict[str, Any]:
        return _404_if_none(
            get_status(sid, global_root=_project_root_or_404(sid)), sid
        )

    @app.get("/api/projects/{sid}/journal")
    def _journal(sid: str, n: int = Query(10, ge=1, le=500)) -> dict[str, Any]:
        return {
            "journal": _404_if_none(
                get_journal(
                    sid, n=n, global_root=_project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get("/api/projects/{sid}/doctor")
    def _doctor(sid: str) -> dict[str, Any]:
        return _404_if_none(
            get_doctor(sid, global_root=_project_root_or_404(sid)), sid
        )

    @app.get(
        "/api/projects/{sid}/config",
        dependencies=[Depends(_require_auth)],
    )
    def _config(sid: str) -> dict[str, Any]:
        return get_config(global_root=_project_root_or_404(sid))

    @app.get(
        "/api/projects/{sid}/identity",
        dependencies=[Depends(_require_auth)],
    )
    def _identity(sid: str) -> dict[str, Any]:
        return {
            "identity": _404_if_none(
                get_identity(sid, global_root=_project_root_or_404(sid)), sid
            )
        }

    @app.get("/api/projects/{sid}/transcript")
    def _transcript(sid: str, n: int = Query(20, ge=1, le=500)) -> dict[str, Any]:
        return {
            "turns": _404_if_none(
                get_transcript(
                    sid, n=n, global_root=_project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get("/api/projects/{sid}/backlog/{item_id}")
    def _backlog_item(sid: str, item_id: str) -> dict[str, Any]:
        item = get_backlog_item(
            sid, item_id, global_root=_project_root_or_404(sid)
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    # ── Wave-1 write endpoints (auth-gated) ───────────────────────────────

    @app.post("/api/projects/{sid}/note", dependencies=[Depends(_require_auth)])
    def _post_note(sid: str, body: _NoteIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty note text")
        return {
            "result": _404_if_none(
                add_project_note(
                    sid, body.text, global_root=_project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.post("/api/projects/{sid}/plan", dependencies=[Depends(_require_auth)])
    async def _plan_preview(sid: str, body: _PlanIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty plan objective")
        project_root = _project_root_or_404(sid)
        from .manager_bridge import manager_plan
        return await run_in_threadpool(
            manager_plan, sid, body.text, global_root=project_root,
        )

    @app.post("/api/projects/{sid}/config/set", dependencies=[Depends(_require_auth)])
    def _config_set(sid: str, body: _ConfigSetIn) -> dict[str, Any]:
        _project_root_or_404(sid)
        try:
            return set_operator_config(body.name, body.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/projects/{sid}/config/budget",
        dependencies=[Depends(_require_auth)],
    )
    def _budget_set(sid: str, body: _BudgetSetIn) -> dict[str, Any]:
        _project_root_or_404(sid)
        try:
            return set_budget_config(body.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{sid}/identity", dependencies=[Depends(_require_auth)])
    def _identity_set(sid: str, body: _IdentitySetIn) -> dict[str, Any]:
        _404_if_none(
            set_identity(
                sid, body.text, global_root=_project_root_or_404(sid)
            ),
            sid,
        )
        return {"ok": True}

    @app.post("/api/projects/{sid}/reset", dependencies=[Depends(_require_auth)])
    def _manager_reset(sid: str) -> dict[str, Any]:
        project_root = _project_root_or_404(sid)
        from .manager_bridge import reset_manager_context
        return {"ok": reset_manager_context(sid, global_root=project_root)}

    @app.post("/api/projects/{sid}/skills", dependencies=[Depends(_require_auth)])
    def _skills(sid: str, body: _SkillsIn) -> dict[str, Any]:
        _project_root_or_404(sid)
        try:
            tokens = shlex.split(body.args)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid skill arguments: {exc}") from exc
        return {"text": run_skill_command(tokens)}

    @app.post("/api/projects/{sid}/backlog/{item_id}/dispose", dependencies=[Depends(_require_auth)])
    def _dispose(sid: str, item_id: str, body: _DisposeIn) -> dict[str, Any]:
        if body.op not in ("done", "skip", "rm"):
            raise HTTPException(status_code=400, detail="op must be done|skip|rm")
        item = dispose_backlog(
            sid,
            item_id,
            body.op,
            global_root=_project_root_or_404(sid),
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    @app.post("/api/projects/{sid}/mission/abort", dependencies=[Depends(_require_auth)])
    def _abort_mission(sid: str, body: _AbortMissionIn | None = None) -> dict[str, Any]:
        request = body or _AbortMissionIn()
        return _404_if_none(
            abort_project_mission(
                sid,
                reason=request.reason,
                requested_by="operator",
                global_root=global_root,
            ),
            sid,
        )

    @app.post("/api/projects/{sid}/backlog/{item_id}/stop", dependencies=[Depends(_require_auth)])
    def _stop_item(sid: str, item_id: str) -> dict[str, Any]:
        item = stop_backlog_iteration(
            sid, item_id, global_root=_project_root_or_404(sid)
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    # ── live event stream (M0) ────────────────────────────────────────────

    @app.websocket("/api/projects/{sid}/stream")
    async def _stream(ws: WebSocket, sid: str, replay: int = 40,
                      view: str = Query(default="full", pattern="^(full|ui)$"),
                      token_q: str | None = Query(default=None, alias="token")) -> None:
        project_root = _root_for_project(sid)
        life_dir = (
            project_life_dir(sid, global_root=project_root)
            if project_root is not None
            else None
        )
        await ws.accept()
        if token and token_q != token:
            await ws.close(code=4401, reason="unauthorized")
            return
        if life_dir is None:
            await ws.close(code=4404, reason="unknown project")
            return
        try:
            async for ev in tail_events(life_dir, replay_limit=max(0, min(replay, 200))):
                if view == "ui" and not _event_visible_in_web_ui(ev):
                    continue
                await ws.send_json(ev)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 — a stream error must not crash the server
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    # ── static web UI (optional) ──────────────────────────────────────────
    # When the React frontend has been built (`npm run build` in frontend/web),
    # serve it from the same origin so `argus-skill --web` gives API + UI on one
    # port. The /api routes above are registered first, so they always win; this
    # catch-all mount only handles the SPA shell + assets. Skipped silently when
    # the bundle is absent (API-only mode, e.g. the Vite dev server proxies here).
    source_dist = Path(__file__).resolve().parents[2] / "frontend" / "web" / "dist"
    wheel_dist = Path(__file__).resolve().parents[1] / "_frontend" / "web" / "dist"
    web_dist = source_dist if source_dist.is_dir() else wheel_dist
    if web_dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


def serve(
    host: str = "127.0.0.1",
    port: int = 8799,
    *,
    global_root: Path | str | None = None,
    auth_token: str | None = None,
) -> int:
    """Run the API with uvicorn (blocking). Defaults to a localhost bind."""
    import uvicorn

    uvicorn.run(
        create_app(global_root=global_root, auth_token=auth_token),
        host=host, port=port, log_level="info",
    )
    return 0
