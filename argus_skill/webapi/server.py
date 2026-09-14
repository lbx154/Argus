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
import logging
import os
import queue
import re
import threading  # noqa: F401 - used via server.threading in tests/webapi/test_commands_m1.py
import time
from pathlib import Path
from typing import Any

from ..apps.cli._follow import (
    _merge_recent_event_rows,
    _read_recent_project_events,  # noqa: F401 - used via server_mod._read_recent_project_events in webapi/routes/projects.py
)
from ..core.event_catalog import EventType, canonical_event_type
from ..core.metrics import (
    http_route_template,
    metrics_snapshot,  # noqa: F401 - used via server_mod.metrics_snapshot in webapi/routes/meta.py
    record_metric,
    render_prometheus,  # noqa: F401 - used via server_mod.render_prometheus in webapi/routes/meta.py
)
from ..core.runtime_identity import (
    runtime_identity,  # noqa: F401 - monkeypatched via server.runtime_identity; read by daemon_upgrade._srv()
)
from ..daemon.commands import (
    DaemonCommandReceipt,
    daemon_command_execution_lock,  # noqa: F401 - monkeypatched via server.daemon_command_execution_lock; read by daemon_upgrade._srv()
    execute_daemon_command,  # noqa: F401 - used via server_mod.execute_daemon_command in webapi/routes/daemon.py
)
from ..daemon.life_worker import (
    DaemonStatus,
    _active_daemon_count,  # noqa: F401 - monkeypatched via server._active_daemon_count; read by daemon_lifecycle._srv()
    _active_workspace_owner,  # noqa: F401 - monkeypatched via server._active_workspace_owner; read by daemon_lifecycle._srv()
    _max_active_daemons,  # noqa: F401 - monkeypatched via server._max_active_daemons; read by daemon_lifecycle._srv()
    read_continuous_state,  # noqa: F401 - used via server.read_continuous_state in tests/webapi/test_commands_m1.py
    read_daemon_status,  # also retained for daemon_lifecycle/daemon_upgrade compatibility
    stop_daemon,  # noqa: F401 - monkeypatched via server.stop_daemon; read by daemon_lifecycle/daemon_upgrade._srv()
    write_continuous_config,  # noqa: F401 - compatibility export
)
from ..daemon.life_worker import (
    spawn_detached_daemon_clean as spawn_detached_daemon,  # noqa: F401 - monkeypatched via server.spawn_detached_daemon; read by daemon_lifecycle._srv()
)
from ..daemon.protocol import (
    daemon_protocol_compatibility,  # noqa: F401 - monkeypatched via server.daemon_protocol_compatibility; read by daemon_upgrade._srv()
    daemon_runtime_owned_by_current_source,  # noqa: F401 - monkeypatched via server.daemon_runtime_owned_by_current_source; read by daemon_upgrade._srv()
)
from ..life.memory import (
    _read_jsonl_tail_history,  # noqa: F401 - used via server_mod._read_jsonl_tail_history in webapi/routes/projects.py
)
from ..manager.front_door import (
    ManagerHandoffError,  # noqa: F401 - used via server_mod.ManagerHandoffError in webapi/routes/{daemon,workitems}.py
    ManagerHandoffSupersededError,  # noqa: F401 - used via server_mod.ManagerHandoffSupersededError in webapi/routes/{daemon,workitems}.py
)
from . import artifacts, project_state
from .counterexample_dashboard import (  # noqa: F401 - route delegation export
    build_counterexample_dashboard,
)
from .protocol import build_api_meta, protocol_header
from .source_update import (  # noqa: F401 - used by routes/meta.py through server_mod
    read_source_update_status,
    start_source_update,
)

log = logging.getLogger(__name__)

_QUIET_ACCESS_PATHS = re.compile(
    r"^(?:/api/projects(?:/costs|/[^/]+/(?:snapshot|artifacts|git-diff|status|journal))?"
    r"|/api/runtime/source-update)$"
)


class _PollingAccessFilter(logging.Filter):
    """Hide successful dashboard refreshes while retaining actionable traffic."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method = str(args[1]).upper()
        path = str(args[2]).partition("?")[0]
        try:
            status = int(args[4])
        except (TypeError, ValueError):
            return True
        return not (
            method in {"GET", "HEAD"}
            and 200 <= status < 400
            and _QUIET_ACCESS_PATHS.fullmatch(path) is not None
        )


def _uvicorn_log_config(uvicorn_module: Any) -> dict[str, Any]:
    """Return Uvicorn defaults with successful dashboard polling filtered."""
    import copy

    config = copy.deepcopy(uvicorn_module.config.LOGGING_CONFIG)
    config.setdefault("filters", {})["argus_polling"] = {
        "()": _PollingAccessFilter,
    }
    config["handlers"]["access"]["filters"] = ["argus_polling"]
    return config


_global_root = project_state.resolve_global_root
_settled_spend = project_state.settled_spend
build_snapshot = project_state.build_snapshot
list_projects = project_state.list_projects
list_project_costs = project_state.list_project_costs
project_life_dir = project_state.project_life_dir
_artifact_metadata = artifacts.artifact_metadata
_manager_live_view_files = artifacts.manager_live_view_files
_project_git_diff = artifacts.project_git_diff
_project_workspace = artifacts.project_workspace
_resolved_project_artifact = artifacts.resolved_project_artifact
_safe_artifact_path = artifacts.safe_artifact_path
get_project_artifact = artifacts.get_project_artifact
list_project_artifacts = artifacts.list_project_artifacts

__all__ = [
    "DaemonStatus",
    "create_app",
    "serve",
    "project_life_dir",
    "build_snapshot",
    "list_projects",
    "list_project_costs",
    "enqueue_task",
    "enqueue_nudge",
    "answer_pending_question",
    "start_project_daemon",
    "stop_project_daemon",
    "replace_project_daemon",
    "list_running_daemons",
    "update_project",
    "delete_project",
    "list_trashed_projects",
    "restore_trashed_project",
    "upgrade_project_daemon",
    "schedule_project_daemon_upgrade",
    "set_project_workdir",
    "set_continuous",
    "get_status",
    "get_journal",
    "add_project_note",
    "abort_project_mission",
    "dispose_backlog",
    "stop_backlog_iteration",
    "get_doctor",
    "get_config",
    "get_identity",
    "get_transcript",
    "get_backlog_item",
    "set_operator_config",
    "set_identity",
    "run_skill_command",
    "read_source_update_status",
    "start_source_update",
    "build_counterexample_dashboard",
    "list_project_artifacts",
    "get_project_artifact",
]

EVENT_FILE = "events.jsonl"
_WEB_UI_DROPPED_EVENT_TYPES = frozenset(
    {
        EventType.AGENT_IO_START,
        EventType.AGENT_IO_STREAM,
        EventType.AGENT_IO_COMPLETE,
        EventType.USAGE_RECORDED,
        EventType.CODEX_UTIL_COMPLETED,
        EventType.SKILL_COST_COMPLETED,
        EventType.BUDGET_RESERVATION_CREATED,
        EventType.BUDGET_RESERVATION_SETTLED,
        EventType.BUDGET_RESERVATION_RELEASED,
    }
)


def _event_visible_in_web_ui(event: dict[str, Any]) -> bool:
    if event.get("operator_alert") is True:
        return True
    return canonical_event_type(event.get("type")) not in _WEB_UI_DROPPED_EVENT_TYPES


# HTTP methods that cannot change the project index, so they leave the
# coalescing cache alone. Everything else invalidates it once it succeeds.
_INDEX_CACHE_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
    result.update(
        {
            "command_id": receipt.command_id,
            "command_status": receipt.status,
            "command_revision": receipt.revision,
            "command": receipt.to_jsonable(),
        }
    )
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
    raw = os.environ.get("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", "5")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 5.0


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
    active_role = "manager"
    role_labels = {
        "manager": "Manager",
        "planner": "Planner",
        "engineer": "Engineer",
        "reviewer": "Reviewer",
    }
    while True:
        try:
            item = items.get(timeout=heartbeat_s) if heartbeat_s > 0 else items.get()
        except queue.Empty:
            quiet_s = max(0, int(now() - last_real_at))
            actor = role_labels.get(active_role, active_role.title() or "Manager")
            yield {
                "type": "phase",
                "role": active_role,
                "label": f"{actor} · waiting for the next model event · {quiet_s}s quiet",
                "heartbeat": True,
                "quiet_s": quiet_s,
            }
            continue
        if item is None:
            return
        last_real_at = now()
        if item.get("type") == "phase" and not item.get("heartbeat"):
            active_role = str(item.get("role") or "manager").strip().lower()
        yield item


# ---------------------------------------------------------------------------
# Command helpers (write side) — all go through the SAME reused functions the
# CLI uses, so the flock CAS / atomic writes are shared. Never write the
# backlog/inbox files directly. Each returns None if the project is unknown.
# ---------------------------------------------------------------------------

from . import (
    daemon_lifecycle,
    daemon_upgrade,
    manager_pending_question,
    mission_items,
    project_crud,
)
from .daemon_services import DaemonServices

_SCHEDULED_DAEMON_UPGRADES = daemon_upgrade._SCHEDULED_DAEMON_UPGRADES
_SCHEDULED_DAEMON_UPGRADES_LOCK = daemon_upgrade._SCHEDULED_DAEMON_UPGRADES_LOCK
_worker_config_from_env = daemon_lifecycle._worker_config_from_env
list_running_daemons = daemon_lifecycle.list_running_daemons
_admission_required = daemon_lifecycle._admission_required
_clear_daemon_admission = daemon_lifecycle._clear_daemon_admission
start_project_daemon = daemon_lifecycle.start_project_daemon
_write_parked_state = daemon_lifecycle._write_parked_state
replace_project_daemon = daemon_lifecycle.replace_project_daemon
create_daemon = daemon_lifecycle.create_daemon
set_project_launch_cwd = daemon_lifecycle.set_project_launch_cwd
set_project_workdir = daemon_lifecycle.set_project_workdir
stop_project_daemon = daemon_lifecycle.stop_project_daemon
upgrade_project_daemon = daemon_upgrade.upgrade_project_daemon

_daemon_upgrade_request_path = daemon_upgrade._daemon_upgrade_request_path
_read_daemon_upgrade_request = daemon_upgrade._read_daemon_upgrade_request
_write_daemon_upgrade_request = daemon_upgrade._write_daemon_upgrade_request
_upgrade_request_matches_current_source = daemon_upgrade._upgrade_request_matches_current_source
_record_daemon_upgrade_error = daemon_upgrade._record_daemon_upgrade_error
_complete_scheduled_daemon_upgrade = daemon_upgrade._complete_scheduled_daemon_upgrade
schedule_project_daemon_upgrade = daemon_upgrade.schedule_project_daemon_upgrade
reconcile_pending_daemon_upgrades = daemon_upgrade.reconcile_pending_daemon_upgrades

update_project = project_crud.update_project
delete_project = project_crud.delete_project
list_trashed_projects = project_crud.list_trashed_projects
restore_trashed_project = project_crud.restore_trashed_project
set_continuous = project_crud.set_continuous

_enqueue_task_unlocked = mission_items._enqueue_task_unlocked
enqueue_task = mission_items.enqueue_task
enqueue_task_command = mission_items.enqueue_task_command
enqueue_nudge = mission_items.enqueue_nudge
answer_pending_question = manager_pending_question.manager_answer_pending_question
resolve_operator_decision = manager_pending_question.manager_resolve_operator_decision
get_status = mission_items.get_status
get_journal = mission_items.get_journal
add_project_note = mission_items.add_project_note
get_backlog_item = mission_items.get_backlog_item
abort_project_mission = mission_items.abort_project_mission
dispose_backlog = mission_items.dispose_backlog
stop_backlog_iteration = mission_items.stop_backlog_iteration
_daemon_log_tail = mission_items._daemon_log_tail
get_doctor = mission_items.get_doctor
get_config = mission_items.get_config
get_identity = mission_items.get_identity
set_operator_config = mission_items.set_operator_config
set_budget_config = mission_items.set_budget_config
set_identity = mission_items.set_identity
run_skill_command = mission_items.run_skill_command
get_transcript = mission_items.get_transcript


def _hides_inner_monologue() -> bool:
    """Whether the reasoning scratchpad stays out of the UI stream.

    Read per call rather than captured at import so that flipping the knob in
    the cockpit takes effect on the next event, not the next restart.
    """
    from ..core.role_reply import read_bool

    raw = os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0")
    return not read_bool({"SHOW": raw}, "SHOW", default=False)


def _is_inner_monologue(event: object) -> bool:
    return isinstance(event, dict) and str(event.get("kind") or "").strip() == "reasoning"


def _read_replay_snapshot(
    path: Path,
    *,
    limit: int,
    max_bytes: int = 256 * 1024,
) -> tuple[list[dict[str, Any]], int, int | None]:
    """Read replay and its byte boundary from one bounded file snapshot."""
    from ..core.jsonl_reader import read_jsonl_replay

    try:
        with path.open("rb") as stream:
            replay = read_jsonl_replay(stream, limit=limit, max_bytes=max_bytes)
            return replay.rows, replay.offset, os.fstat(stream.fileno()).st_ino
    except OSError:
        return [], 0, None


async def tail_events(
    life_dir: Path,
    *,
    replay_limit: int = 40,
    poll_interval: float = 0.25,
):
    """Yield bounded replay, then drain each retained generation before the next.

    An open file description retains unread bytes when events.jsonl rotates.
    Reads and generation selection briefly share the writer's events.lock; no
    lock is held while yielding, sleeping, or sending WebSocket messages.
    """
    from ..core.jsonl_reader import JsonlTail

    reader = JsonlTail(life_dir / EVENT_FILE)
    try:
        replay = reader.start(replay_limit=replay_limit, merge_rows=_merge_recent_event_rows)
        hide = _hides_inner_monologue()
        for event in replay:
            if not (hide and _is_inner_monologue(event)):
                yield event
        while True:
            batch = reader.read_batch()
            for event in batch.rows:
                # The reasoning knob remains live for newly appended records.
                if not (_is_inner_monologue(event) and _hides_inner_monologue()):
                    yield event
            # Drain an existing backlog cooperatively, rather than imposing a
            # poll delay per chunk. Idle and unfinished lines wait for an append.
            await asyncio.sleep(0 if batch.more else poll_interval)
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# FastAPI app (imports the [web] extra lazily)
# ---------------------------------------------------------------------------


def create_app(
    *,
    global_root: Path | str | None = None,
    auth_token: str | None = None,
    session_roots: list[Path | str] | None = None,
    daemon_services: DaemonServices | None = None,
    query_limits=None,
):
    """Build the FastAPI app. Requires the ``[web]`` extra (fastapi).

    ``auth_token`` (or env ``ARGUS_SKILL_WEB_TOKEN``) turns on bearer auth: when
    set, every command POST needs ``Authorization: Bearer <token>`` and the WS
    upgrade needs ``?token=<token>`` (browsers cannot set WS headers). With no
    token configured the API is unauthenticated — safe only behind the default
    ``127.0.0.1`` bind.

    ``daemon_services`` overrides project status/deletion and direct daemon
    starts for this app only. Defaults are captured when the app is built.
    ``query_limits`` accepts ``index_cache.QueryLimits`` for this app's isolated
    scan pool, queue capacity and HTTP deadlines. The app owns its shutdown.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.responses import JSONResponse

    from . import server as server_mod
    from .index_cache import CacheWaitTimeout, QueryExecutor, QueryUnavailable

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

    @app.exception_handler(QueryUnavailable)
    @app.exception_handler(CacheWaitTimeout)
    async def _cache_wait_timeout(_request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
            headers={"Retry-After": "1"},
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
        cache_control = _web_cache_control(request.url.path)
        if cache_control:
            response.headers["Cache-Control"] = cache_control
        if request.method not in _INDEX_CACHE_SAFE_METHODS and response.status_code < 400:
            # A rename/delete/restore (and anything else that moves a session's
            # last_active) must not be hidden behind the coalescing TTL, or the
            # very next poll shows the pre-mutation index and the operator reads
            # it as "my change did not take". Keyed off the HTTP method rather
            # than a list of routes so a future mutating endpoint cannot forget.
            ctx.invalidate_read_caches()
        return response

    @app.on_event("startup")
    def _resume_pending_daemon_upgrades() -> None:
        reconcile_pending_daemon_upgrades(roots)
        # Prime host-wide cost/usage projections before the first compact
        # snapshot. Otherwise a completed inline Manager call can momentarily
        # show project spend while global spend incorrectly appears empty.
        for root in roots:
            project_state._schedule_host_projection_refresh(root)

    @app.on_event("shutdown")
    def _shutdown_warm_manager_clients() -> None:
        # Explicitly terminate module-level Copilot ACP clients. Otherwise an
        # old Web process can remain resident after Uvicorn shuts down, leaving
        # stale Copilot processes alive across repeated cockpit launches.
        try:
            from .manager_state import shutdown_manager_bridge

            shutdown_manager_bridge()
        except Exception:  # noqa: BLE001
            pass

    # Localhost dev only: allow the Vite dev server + same-origin. Not a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8799",
            "http://127.0.0.1:8799",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    from .routes.advisor import register_advisor_routes
    from .routes.artifacts import register_artifact_routes
    from .routes.context import ServerContext
    from .routes.counterexamples import register_counterexample_routes
    from .routes.daemon import register_daemon_routes
    from .routes.manager import register_manager_routes
    from .routes.meta import register_meta_routes
    from .routes.projects import register_project_routes
    from .routes.workitems import register_workitem_routes
    from .routes.workspace_v2 import register_workspace_v2_routes

    ctx = ServerContext(
        global_root=global_root,
        token=token,
        roots=roots,
        api_meta=api_meta,
        list_projects=list_projects,
        list_project_costs=list_project_costs,
        list_trashed_projects=list_trashed_projects,
        project_life_dir=project_life_dir,
        daemon_services=(
            daemon_services if daemon_services is not None else DaemonServices(
                read_status=read_daemon_status,
                start=start_project_daemon,
            )
        ),
        query_executor=QueryExecutor(query_limits),
    )
    app.state.query_executor = ctx.query_executor
    app.state.query_limits = ctx.query_executor.limits

    @app.on_event("shutdown")
    async def _shutdown_query_workers() -> None:
        await ctx.query_executor.close()

    # Route registration is split by API domain so create_app() stays a thin
    # composition root: projects/sessions (listing, CRUD, snapshot/events,
    # work-item endpoints), daemon control (create/start/stop/replace/upgrade,
    # continuous), artifacts/read-only (artifact + git-diff file serving),
    # Manager streaming/messages (chat, SSE stream, live event WebSocket), and
    # config/diagnostics (meta, metrics, per-project config/identity/doctor,
    # operator config/budget/identity/reset/skills). Registrars share this
    # app's auth/root helpers and narrow daemon services through ``ctx``.
    # Project/work-item operations call their owning modules directly. The
    # remaining legacy services still receive the server compatibility facade.
    register_project_routes(app, ctx, server_mod)
    register_workitem_routes(app, ctx, server_mod)
    register_counterexample_routes(app, ctx, server_mod)
    register_daemon_routes(app, ctx, server_mod)
    register_artifact_routes(app, ctx, server_mod)
    from .routes.reader_foundation import register_reader_foundation_routes

    register_reader_foundation_routes(app, ctx)
    register_manager_routes(app, ctx, server_mod)
    register_meta_routes(app, ctx, server_mod)
    register_advisor_routes(app, ctx)
    register_workspace_v2_routes(app, ctx, server_mod)
    from .routes.map_datasets import register_map_dataset_routes

    register_map_dataset_routes(app, ctx)
    from .routes.map_notes import register_map_note_routes

    register_map_note_routes(app, ctx)
    from .routes.plugins import register_plugin_routes
    register_plugin_routes(app, ctx)
    from .routes.research_timeline import register_research_timeline_routes

    register_research_timeline_routes(app, ctx)

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
        import mimetypes

        from fastapi.staticfiles import StaticFiles

        # Windows' MIME registry commonly has no .mjs mapping. Serving the
        # PDF.js worker as text/plain makes WebView2 reject the module and then
        # fail its fake-worker fallback. Register the standards-compliant MIME
        # before StaticFiles resolves response headers.
        mimetypes.add_type("text/javascript", ".mjs")
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
    from ..core.runtime_identity import source_root_preflight_error

    source_error = source_root_preflight_error()
    if source_error:
        raise RuntimeError(f"webapi refused mismatched source root: {source_error}")
    import uvicorn

    uvicorn.run(
        create_app(global_root=global_root, auth_token=auth_token),
        host=host,
        port=port,
        log_level="info",
        log_config=_uvicorn_log_config(uvicorn),
    )
    return 0
