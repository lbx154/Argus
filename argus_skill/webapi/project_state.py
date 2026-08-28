"""Read-only project state assembled for WebAPI clients.

This module owns path validation, project listing, snapshot construction, and
the call-ledger cache. Route registration and write-side commands stay in
``server.py``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..apps.cli._follow import _read_recent_project_events
from ..core import paths as core_paths
from ..core.cost_control import CostControlLockBusyError, cost_control_snapshot
from ..core.metrics import metrics_snapshot
from ..core.mission_view import snapshot_mission_view
from ..core.provider_quota import provider_usage_snapshot
from ..core.role_config import RoleConfig, resolve_all_roles
from ..core.runtime_identity import runtime_identity
from ..core.session import (
    SessionMeta,
    durable_session_activity,
    list_sessions,
    read_session_meta,
)
from ..core.usage import UsageLedger, UsageSummary
from ..daemon.commands import daemon_command_snapshot
from ..daemon.life_worker import (
    DaemonStatus,
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
)
from ..daemon.protocol import (
    daemon_protocol_compatibility,
    daemon_runtime_owned_by_current_source,
)
from ..daemon.state import DAEMON_UPGRADE_REQUEST_FILE
from ..life.memory import LifeMemory
from ..life.role_activity import RoleActivity, role_activity
from .daemon_liveness import web_daemon_liveness
from .protocol import SNAPSHOT_SCHEMA_VERSION

DAEMON_ADMISSION_FILE = "daemon.admission.json"
_PROJECT_INDEX_LABEL_CHARS = 180
_PROJECT_INDEX_OBJECTIVE_CHARS = 1_000
_HOST_CACHE_MAX_ENTRIES = 64
_PROJECT_CACHE_MAX_ENTRIES = 256

_SPEND_CACHE: dict[str, tuple[tuple[int, int, int] | None, UsageSummary]] = {}
_SPEND_CACHE_LOCK = threading.Lock()
_METRICS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_METRICS_CACHE_LOCK = threading.Lock()
_METRICS_CACHE_TTL_SECONDS = 60.0
_HOST_SNAPSHOT_CACHE_TTL_SECONDS = 60.0
_COST_CONTROL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COST_CONTROL_CACHE_LOCK = threading.Lock()
_GLOBAL_USAGE_CACHE: dict[str, tuple[float, UsageSummary]] = {}
_GLOBAL_USAGE_CACHE_LOCK = threading.Lock()
_HOST_REFRESHING: set[str] = set()
_HOST_REFRESHING_LOCK = threading.Lock()


def _store_bounded_cache_entry(
    cache: dict[Any, Any],
    key: Any,
    value: Any,
    *,
    max_entries: int,
) -> None:
    """Store one entry while evicting the oldest project/root keys."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > max_entries:
        del cache[next(iter(cache))]


def project_usage_summary(project_root: Path | str) -> UsageSummary:
    """Return settled rows without taking the writer/reconciliation lock."""
    return UsageLedger(project_root, migrate_legacy=False).summary()


def _project_index_text(value: Any, limit: int, *, single_line: bool = False) -> str:
    text = str(value or "")[:limit]
    if single_line:
        text = " ".join(text.splitlines()).strip()
    return text


def resolve_global_root(value: Path | str | None) -> Path:
    return Path(value) if value is not None else core_paths.global_root()


def daemon_upgrade_pending(life_dir: Path) -> bool:
    try:
        payload = json.loads((life_dir / DAEMON_UPGRADE_REQUEST_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return False
    requested_source = str(payload.get("source_root") or "").strip()
    current_source = str(runtime_identity().get("source_root") or "").strip()
    if not requested_source or not current_source:
        return False
    try:
        return (
            Path(requested_source).expanduser().resolve()
            == Path(current_source).expanduser().resolve()
        )
    except OSError:
        return False


def _cached_metrics_snapshot(
    root: Path,
    *,
    nonblocking: bool = False,
    cost_control: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Reuse the host-wide projection without blocking compact UI snapshots."""
    key = str(root.resolve())
    now = time.monotonic()
    with _METRICS_CACHE_LOCK:
        cached = _METRICS_CACHE.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        if nonblocking:
            # Compact snapshots are the cockpit's first-paint path. Even a
            # background JSON aggregation contends for the GIL and delayed the
            # UI by ~500 ms on large daily logs. Serve stale data when present;
            # otherwise omit observability until a full snapshot requests it.
            return cached[1] if cached is not None else None
    value = metrics_snapshot(root=root, cost_control=cost_control)
    with _METRICS_CACHE_LOCK:
        _store_bounded_cache_entry(
            _METRICS_CACHE,
            key,
            (now + _METRICS_CACHE_TTL_SECONDS, value),
            max_entries=_HOST_CACHE_MAX_ENTRIES,
        )
    return value


def _store_cost_control_cache(key: str, value: dict[str, Any]) -> None:
    with _COST_CONTROL_CACHE_LOCK:
        _store_bounded_cache_entry(
            _COST_CONTROL_CACHE,
            key,
            (
                time.monotonic() + _HOST_SNAPSHOT_CACHE_TTL_SECONDS,
                value,
            ),
            max_entries=_HOST_CACHE_MAX_ENTRIES,
        )


def _store_global_usage_cache(key: str, value: UsageSummary) -> None:
    with _GLOBAL_USAGE_CACHE_LOCK:
        _store_bounded_cache_entry(
            _GLOBAL_USAGE_CACHE,
            key,
            (
                time.monotonic() + _HOST_SNAPSHOT_CACHE_TTL_SECONDS,
                value,
            ),
            max_entries=_HOST_CACHE_MAX_ENTRIES,
        )


def _schedule_host_projection_refresh(root: Path) -> None:
    """Refresh both expensive host projections once, outside request threads."""
    key = str(root.resolve())
    with _HOST_REFRESHING_LOCK:
        if key in _HOST_REFRESHING:
            return
        _HOST_REFRESHING.add(key)

    def _refresh() -> None:
        try:
            try:
                _store_cost_control_cache(
                    key,
                    cost_control_snapshot(global_root=root),
                )
            except Exception:  # noqa: BLE001 - stale UI data remains usable
                pass
            try:
                from ..life.supervisor import global_daily_usage_summary

                _store_global_usage_cache(
                    key,
                    global_daily_usage_summary(global_root=root),
                )
            except Exception:  # noqa: BLE001 - stale UI data remains usable
                pass
        finally:
            with _HOST_REFRESHING_LOCK:
                _HOST_REFRESHING.discard(key)

    threading.Thread(
        target=_refresh,
        name="argus-web-host-snapshot-refresh",
        daemon=True,
    ).start()


def _cached_cost_control_snapshot(
    root: Path,
    *,
    nonblocking: bool = False,
) -> dict[str, Any] | None:
    """Reuse the expensive host ledger projection for the operator UI."""
    key = str(root.resolve())
    now = time.monotonic()
    with _COST_CONTROL_CACHE_LOCK:
        cached = _COST_CONTROL_CACHE.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
    if nonblocking:
        _schedule_host_projection_refresh(root)
        return {**cached[1], "snapshot_stale": True} if cached is not None else None
    try:
        value = cost_control_snapshot(global_root=root)
    except CostControlLockBusyError:
        with _COST_CONTROL_CACHE_LOCK:
            cached = _COST_CONTROL_CACHE.get(key)
        if cached is None:
            raise
        return {**cached[1], "snapshot_stale": True}
    _store_cost_control_cache(key, value)
    return value


def _cached_global_daily_usage_summary(
    root: Path,
    *,
    nonblocking: bool = False,
) -> UsageSummary:
    """Reuse the all-project daily ledger roll-up across snapshot keys/SIDs."""
    key = str(root.resolve())
    now = time.monotonic()
    with _GLOBAL_USAGE_CACHE_LOCK:
        cached = _GLOBAL_USAGE_CACHE.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
    if nonblocking:
        _schedule_host_projection_refresh(root)
        return cached[1] if cached is not None else _empty_usage_summary()
    from ..life.supervisor import global_daily_usage_summary

    value = global_daily_usage_summary(global_root=root)
    _store_global_usage_cache(key, value)
    return value


def project_life_dir(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    """Resolve one safe direct child of ``<global_root>/projects``."""
    projects = core_paths.session_states_root(resolve_global_root(global_root)).resolve()
    try:
        life_dir = (projects / sid).resolve()
    except (OSError, ValueError):
        return None
    if life_dir.parent != projects or not life_dir.is_dir():
        return None
    return life_dir


def daemon_dict(status: DaemonStatus, *, life_dir: Path | None = None) -> dict[str, Any]:
    budget = resolve_effective_budget(status)
    protocol_compatible, protocol_error = daemon_protocol_compatibility(status)
    liveness = web_daemon_liveness(life_dir, status) if life_dir is not None else None
    alive = liveness.alive if liveness is not None else bool(status.alive)
    return {
        "alive": alive,
        "pid": liveness.pid if liveness is not None else status.pid,
        "control_available": (
            liveness.control_available if liveness is not None else bool(status.alive)
        ),
        "liveness_source": liveness.source if liveness is not None else "pid_lock",
        "heartbeat_age_seconds": (
            liveness.heartbeat_age_seconds if liveness is not None else None
        ),
        "started_at_iso": status.started_at_iso,
        "uptime_seconds": status.uptime_seconds,
        "health": {
            "state": liveness.phase if liveness is not None and alive else status.health_state,
            "stalled": status.stalled,
            "last_progress_at": (
                liveness.last_progress_at
                if liveness is not None and alive
                else status.last_progress_at
            ),
            "last_progress_event": (
                liveness.last_progress_event
                if liveness is not None and alive
                else status.last_progress_event
            ),
            "seconds_since_progress": (
                liveness.seconds_since_progress
                if liveness is not None and alive
                else status.seconds_since_progress
            ),
        },
        "backend": status.backend,
        "global_daily_cap_usd": budget.global_daily_cap_usd,
        "mission_width": status.mission_width,
        "read_status": "error" if status.status_read_error else "ok",
        "read_error": status.status_read_error,
        "protocol": {
            "name": status.protocol_name,
            "major": status.protocol_major,
            "minor": status.protocol_minor,
        },
        "capabilities": list(status.capabilities),
        "runtime": status.runtime,
        "protocol_compatible": protocol_compatible,
        "protocol_error": protocol_error,
    }


def diagnostic(section: str, exc: BaseException) -> dict[str, str]:
    return {
        "section": section,
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
    }


def daemon_error_dict(exc: BaseException) -> dict[str, Any]:
    try:
        budget = resolve_effective_budget(None)
        global_daily = budget.global_daily_cap_usd
    except Exception:  # noqa: BLE001 - the original diagnostic is authoritative
        global_daily = None
    return {
        "alive": False,
        "pid": None,
        "control_available": False,
        "liveness_source": "none",
        "heartbeat_age_seconds": None,
        "started_at_iso": None,
        "uptime_seconds": None,
        "health": {
            "state": "unknown",
            "stalled": False,
            "last_progress_at": None,
            "last_progress_event": "",
            "seconds_since_progress": None,
        },
        "backend": None,
        "global_daily_cap_usd": global_daily,
        "mission_width": None,
        "read_status": "error",
        "read_error": str(exc)[:500],
        "protocol": {"name": "", "major": None, "minor": None},
        "capabilities": [],
        "runtime": None,
        "protocol_compatible": None,
        "protocol_error": "",
    }


def roles_list(
    configs: list[RoleConfig],
    activities: dict[str, RoleActivity],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for config in configs:
        activity = activities.get(config.role)
        out.append(
            {
                "role": config.role,
                "backend": config.backend,
                "backend_label": config.backend_label,
                "model": config.model,
                "effort": config.effort,
                "active": bool(activity.active) if activity else False,
                "label": activity.label if activity else "idle",
                "status": activity.status if activity else "idle",
                "age_s": activity.age_s if activity else None,
            }
        )
    return out


def session_dict(meta: SessionMeta | None, sid: str) -> dict[str, Any]:
    if meta is None:
        return {
            "id": sid,
            "display_name": "",
            "objective": "",
            "created": 0.0,
            "last_active": 0.0,
            "cwd": "",
            "workdir": "",
            "launch_cwd": "",
        }
    return {
        "id": meta.id,
        "display_name": meta.display_name,
        "objective": meta.objective,
        "created": meta.created,
        "last_active": meta.last_active,
        "cwd": meta.cwd,
        "workdir": meta.workdir,
        "launch_cwd": meta.launch_cwd,
    }


def apply_campaign_workdir(
    session: dict[str, Any], life_dir: Path,
) -> dict[str, Any]:
    """Expose the repository Argus is actually using, not only launch root."""
    raw_base = str(session.get("workdir") or session.get("cwd") or "").strip()
    if not raw_base:
        session["campaign_workdir"] = ""
        return session
    try:
        from ..core.campaign_workdir import active_campaign_workdir

        active = active_campaign_workdir(life_dir, raw_base)
    except Exception:  # noqa: BLE001 - project picker remains best effort
        active = None
    session["session_workdir"] = raw_base
    session["campaign_workdir"] = str(active or "")
    if active is not None:
        session["workdir"] = str(active)
    return session


def compact_backlog_item(item: Any) -> dict[str, Any]:
    objective = item.objective
    title = item.title.strip()
    if not title:
        title = objective.splitlines()[0][:180]
    return {
        "id": item.id,
        "title": title,
        "objective": "" if title else objective[:240],
        "status": item.status,
        "priority": item.priority,
        "iterate": item.iterate,
        "pending_question": item.pending_question[:500],
        "operator_decision": dict(item.operator_decision),
        "started_ts": item.started_ts,
        "finished_ts": item.finished_ts,
        "deps": [str(dep) for dep in item.deps],
        "iteration_max_cycles": item.iteration_max_cycles,
        "iteration_cycles_done": item.iteration_cycles_done,
        "outcome": dict(item.outcome),
    }


def _carries_stage_state(root: Path) -> bool:
    """Does this root's pipeline state actually record a stage?

    ``current_stage`` answers every root that has the file at all, because a
    fresh project genuinely is at stage one and that fallback is right for it.
    It is wrong for a root that was never the stage's home. Under the split
    between a session state root and an execution workdir, the workdir copy of
    ``PIPELINE_STATE.json`` holds only the adopted objective — no ``vertical``,
    no ``current_stage``, no ``stages`` — so reading a stage out of it means
    reading the default vertical's first stage and calling it fact.
    """
    from ..core.pipeline_state import read_pipeline_state

    try:
        payload = read_pipeline_state(root)
    except (OSError, ValueError):
        return False
    return bool(
        str(payload.get("current_stage") or "").strip()
        or str(payload.get("vertical") or "").strip()
    )


def current_stage_for_session(
    session: dict[str, Any],
    life_dir: Path,
) -> str:
    """The stage this session is actually at, for the operator's cockpit.

    Testbed run 15 (``s-f0dbba19``) finished its math project at stage
    ``review`` of ``scope -> solve -> review``, with all three stages recorded
    done. The API served ``research`` — stage one of the eight-stage default
    pipeline, a vertical that project never ran — for the entire run, because
    the execution workdir happened to hold a three-key ``PIPELINE_STATE.json``
    carrying the adopted objective and nothing else, and the old lookup
    accepted the first root where that file merely *existed*.

    ``_snapshot`` overwrites the event-sourced stage with this value, so the
    served mission view contradicted the harness's own ``mission-view.json``.

    So: a root that records a stage answers first, and the state root is asked
    before the workdir. The old existence-only order is kept as a fallback,
    which is what still answers for a fresh project whose state file is empty.
    """
    from ..core.pipeline_state import pipeline_state_exists
    from ..skills.stage_machine import current_stage

    authoritative = [life_dir, session.get("workdir"), session.get("cwd")]
    legacy = [session.get("workdir"), session.get("cwd"), life_dir]
    for candidates, require_stage_state in ((authoritative, True), (legacy, False)):
        for raw in candidates:
            if not raw:
                continue
            root = Path(str(raw)).expanduser()
            if not pipeline_state_exists(root):
                continue
            if require_stage_state and not _carries_stage_state(root):
                continue
            try:
                return str(current_stage(root) or "")
            except Exception:  # noqa: BLE001 - snapshot remains available
                continue
    return ""


def _empty_usage_summary() -> UsageSummary:
    return UsageSummary(
        call_count=0,
        known_cost_usd=0.0,
        cost_usd=None,
        pricing_status="empty",
        priced_calls=0,
        partial_calls=0,
        unpriced_calls=0,
        not_billed_calls=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_output_tokens=0,
        premium_requests=0.0,
    )


def settled_spend(
    mem: LifeMemory | None,
    life_dir: Path,
    *,
    diagnostics: list[dict[str, str]] | None = None,
) -> UsageSummary:  # noqa: ARG001
    """Read the call ledger; lifecycle events are never summed for spend."""
    key = str(life_dir.resolve())
    signature = stat_signature(life_dir / "usage.jsonl")
    with _SPEND_CACHE_LOCK:
        cached = _SPEND_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    try:
        # Snapshot reads must never wait behind provider finalization.  The
        # durable JSONL is append-only, so reading settled rows without running
        # migrations/reconciliation gives the cockpit a safe current view.
        total = project_usage_summary(life_dir)
    except Exception as exc:  # noqa: BLE001 - snapshot remains available
        total = _empty_usage_summary()
        if diagnostics is not None:
            diagnostics.append(diagnostic("usage", exc))
        return total
    with _SPEND_CACHE_LOCK:
        _store_bounded_cache_entry(
            _SPEND_CACHE,
            key,
            (stat_signature(life_dir / "usage.jsonl"), total),
            max_entries=_PROJECT_CACHE_MAX_ENTRIES,
        )
    return total


def stat_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def read_daemon_admission(
    life_dir: Path,
    *,
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    try:
        raw = (life_dir / DAEMON_ADMISSION_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        if diagnostics is not None:
            diagnostics.append(diagnostic("daemon_admission", exc))
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        if diagnostics is not None:
            diagnostics.append(diagnostic("daemon_admission", exc))
        return None
    return value if isinstance(value, dict) and value.get("admission_required") else None


def build_snapshot(
    sid: str,
    *,
    global_root: Path | str | None = None,
    events_limit: int = 80,
    compact: bool = False,
) -> dict[str, Any] | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = resolve_global_root(global_root)
    diagnostics: list[dict[str, str]] = []

    try:
        status = read_daemon_status(life_dir)
        daemon = daemon_dict(status, life_dir=life_dir)
        if daemon["read_status"] == "error":
            diagnostics.append(
                {
                    "section": "daemon",
                    "error_type": "StatusReadError",
                    "message": str(daemon["read_error"]),
                }
            )
        if daemon["protocol_compatible"] is False:
            diagnostics.append(
                {
                    "section": "daemon_protocol",
                    "error_type": "ProtocolMismatch",
                    "message": str(daemon["protocol_error"]),
                }
            )
    except Exception as exc:  # noqa: BLE001 - return explicit partial state
        daemon = daemon_error_dict(exc)
        diagnostics.append(diagnostic("daemon", exc))

    try:
        roles = roles_list(resolve_all_roles(env=os.environ), role_activity(life_dir))
    except Exception as exc:  # noqa: BLE001
        roles = []
        diagnostics.append(diagnostic("roles", exc))

    engineer = next(
        (row for row in roles if row.get("role") == "engineer"),
        roles[0] if roles else None,
    )
    if engineer and engineer.get("backend"):
        daemon["backend"] = engineer["backend"]
        daemon["backend_label"] = engineer.get("backend_label") or daemon["backend"]

    items: list[Any] = []
    try:
        memory = LifeMemory.open(life_dir)
        # The project snapshot preserves the existing mission-history UI; live
        # scheduling endpoints use active() instead.
        items = list(memory.backlog.history())
        backlog = (
            [compact_backlog_item(item) for item in items]
            if compact
            else [item.to_jsonable() for item in items]
        )
    except Exception as exc:  # noqa: BLE001
        backlog = []
        memory = None
        diagnostics.append(diagnostic("backlog", exc))

    spend = settled_spend(memory, life_dir, diagnostics=diagnostics)

    try:
        recent = _read_recent_project_events(life_dir, limit=events_limit)
    except Exception as exc:  # noqa: BLE001
        recent = []
        diagnostics.append(diagnostic("recent_events", exc))

    try:
        meta = read_session_meta(root, sid)
        activity = durable_session_activity(life_dir)
        if meta is None:
            meta = SessionMeta(id=sid, created=activity, last_active=activity)
        else:
            meta.last_active = max(
                meta.last_active,
                activity,
            )
        session = apply_campaign_workdir(
            session_dict(meta, sid), life_dir
        )
    except Exception as exc:  # noqa: BLE001
        session = session_dict(None, sid)
        diagnostics.append(diagnostic("session", exc))

    try:
        continuous_state = read_continuous_state(life_dir)
        continuous_payload = {
            "enabled": continuous_state.enabled,
            "open_ended": continuous_state.open_ended,
            "objective": continuous_state.objective,
            "done_reason": continuous_state.done_reason,
            "done_at": continuous_state.done_at,
        }
    except Exception as exc:  # noqa: BLE001
        continuous_payload = {"enabled": False, "open_ended": False, "objective": ""}
        diagnostics.append(diagnostic("continuous", exc))

    try:
        mission_view = snapshot_mission_view(
            life_dir,
            session=session,
            daemon=daemon,
            roles=roles,
            backlog=backlog,
            continuous=continuous_payload,
            current_stage=current_stage_for_session(session, life_dir),
        )
    except Exception as exc:  # noqa: BLE001
        mission_view = None
        diagnostics.append(diagnostic("mission_view", exc))

    try:
        request_usage = provider_usage_snapshot(root=root)
    except Exception as exc:  # noqa: BLE001
        request_usage = None
        diagnostics.append(diagnostic("request_usage", exc))

    try:
        cost_control = _cached_cost_control_snapshot(root, nonblocking=compact)
    except Exception as exc:  # noqa: BLE001
        cost_control = None
        diagnostics.append(diagnostic("cost_control", exc))

    try:
        global_spend = _cached_global_daily_usage_summary(root, nonblocking=compact)
        # A stale host cache must never report less usage than the project
        # currently being rendered. Refresh synchronously only on that
        # contradiction; normal compact snapshots keep the nonblocking path.
        if (
            global_spend.call_count < spend.call_count
            or global_spend.known_cost_usd + 1e-12 < spend.known_cost_usd
        ):
            from ..life.supervisor import global_daily_usage_summary

            global_spend = global_daily_usage_summary(global_root=root)
            _store_global_usage_cache(str(root.resolve()), global_spend)
    except Exception as exc:  # noqa: BLE001
        global_spend = _empty_usage_summary()
        diagnostics.append(diagnostic("global_usage", exc))

    try:
        daemon_commands = daemon_command_snapshot(life_dir)
    except Exception as exc:  # noqa: BLE001
        daemon_commands = None
        diagnostics.append(diagnostic("daemon_commands", exc))

    try:
        observability = _cached_metrics_snapshot(
            root,
            nonblocking=compact,
            cost_control=cost_control,
        )
    except Exception as exc:  # noqa: BLE001
        observability = None
        diagnostics.append(diagnostic("observability", exc))

    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "session": session,
        "daemon": daemon,
        "roles": roles,
        "backlog": backlog,
        "recent_events": recent,
        "spend_usd": spend.cost_usd,
        "spend_status": spend.pricing_status,
        "usage_summary": spend.to_jsonable(),
        "global_spend_usd": global_spend.cost_usd,
        "global_spend_status": global_spend.pricing_status,
        "global_usage_summary": global_spend.to_jsonable(),
        "request_usage": request_usage,
        "cost_control": cost_control,
        "daemon_commands": daemon_commands,
        "observability": observability,
        "mission_view": mission_view,
    }
    admission = read_daemon_admission(life_dir, diagnostics=diagnostics)
    if admission is not None:
        snapshot["daemon_admission"] = admission
    if compact:
        snapshot["continuous"] = continuous_payload
        snapshot["pending_questions"] = [
            compact_backlog_item(item) for item in items if item.pending_question
        ]
    snapshot["partial"] = bool(diagnostics)
    snapshot["diagnostics"] = diagnostics
    return snapshot


def list_projects(
    *,
    global_root: Path | str | None = None,
    limit: int | None = None,
    include_empty: bool = False,
) -> list[dict[str, Any]]:
    root = resolve_global_root(global_root)
    out: list[dict[str, Any]] = []
    for meta in list_sessions(root, include_empty=include_empty):
        item = session_dict(meta, meta.id)
        life_dir = core_paths.session_state_root(meta.id, root=root)
        item = apply_campaign_workdir(item, life_dir)
        try:
            status = read_daemon_status(life_dir)
            daemon = daemon_dict(status, life_dir=life_dir)
            item["daemon_alive"] = daemon["alive"]
            item["daemon_pid"] = daemon["pid"]
            item["daemon_control_available"] = daemon["control_available"]
            item["daemon_liveness_source"] = daemon["liveness_source"]
            item["daemon_heartbeat_age_seconds"] = daemon["heartbeat_age_seconds"]
            item["uptime_seconds"] = status.uptime_seconds
            compatible, protocol_error = daemon_protocol_compatibility(status)
            item["daemon_protocol_compatible"] = compatible
            item["daemon_protocol_error"] = protocol_error
            item["daemon_source_owned"] = daemon_runtime_owned_by_current_source(status)
        except Exception:  # noqa: BLE001 - picker remains available
            item["daemon_alive"] = False
            item["daemon_pid"] = None
            item["daemon_control_available"] = False
            item["daemon_liveness_source"] = "none"
            item["daemon_heartbeat_age_seconds"] = None
            item["uptime_seconds"] = None
            item["daemon_protocol_compatible"] = None
            item["daemon_protocol_error"] = ""
            item["daemon_source_owned"] = False
        item["daemon_upgrade_pending"] = daemon_upgrade_pending(life_dir)
        try:
            continuous = read_continuous_state(life_dir)
            campaign_objective = str(continuous.objective or "").strip()
        except Exception:  # noqa: BLE001
            campaign_objective = ""
        raw_objective = item.get("objective") or campaign_objective
        display_name = _project_index_text(
            item.get("display_name"),
            _PROJECT_INDEX_LABEL_CHARS,
            single_line=True,
        )
        objective = _project_index_text(
            raw_objective,
            _PROJECT_INDEX_OBJECTIVE_CHARS,
        )
        item["display_name"] = display_name
        item["objective"] = objective
        item["label"] = (
            display_name
            or _project_index_text(
                objective,
                _PROJECT_INDEX_LABEL_CHARS,
                single_line=True,
            )
            or _project_index_text(meta.id, _PROJECT_INDEX_LABEL_CHARS)
        )
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def list_project_costs(
    *,
    global_root: Path | str | None = None,
    limit: int | None = None,
    include_empty: bool = False,
) -> list[dict[str, Any]]:
    """Return a compact, cache-backed spend feed for the Web daemon picker."""
    root = resolve_global_root(global_root)
    out: list[dict[str, Any]] = []
    for meta in list_sessions(root, include_empty=include_empty):
        life_dir = core_paths.session_state_root(meta.id, root=root)
        spend = settled_spend(None, life_dir)
        try:
            updated_at = (life_dir / "usage.jsonl").stat().st_mtime
        except OSError:
            updated_at = 0.0
        out.append(
            {
                "id": meta.id,
                "spend_usd": spend.cost_usd,
                "known_cost_usd": spend.known_cost_usd,
                "spend_status": spend.pricing_status,
                "usage_calls": spend.call_count,
                "premium_requests": spend.premium_requests,
                "updated_at": updated_at,
            }
        )
        if limit and len(out) >= limit:
            break
    return out


__all__ = [
    "DAEMON_ADMISSION_FILE",
    "build_snapshot",
    "compact_backlog_item",
    "daemon_dict",
    "diagnostic",
    "list_projects",
    "list_project_costs",
    "project_life_dir",
    "read_daemon_admission",
    "resolve_global_root",
    "roles_list",
    "session_dict",
    "settled_spend",
    "stat_signature",
]
