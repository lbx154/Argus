"""Read-only project state assembled for WebAPI clients.

This module owns path validation, project listing, snapshot construction, and
the call-ledger cache. Route registration and write-side commands stay in
``server.py``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..apps.cli._follow import _read_recent_project_events
from ..cli.roles_status import RoleActivity, RoleConfig, resolve_all_roles, role_activity
from ..core import paths as core_paths
from ..core.cost_control import cost_control_snapshot
from ..core.provider_quota import provider_usage_snapshot
from ..core.session import SessionMeta, list_sessions, read_session_meta
from ..core.transcript import first_operator_text
from ..core.usage import UsageSummary, project_usage_summary
from ..daemon.commands import daemon_command_snapshot
from ..daemon.life_worker import (
    DaemonStatus,
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
)
from ..daemon.protocol import daemon_protocol_compatibility
from ..life.memory import LifeMemory
from .protocol import SNAPSHOT_SCHEMA_VERSION

DAEMON_ADMISSION_FILE = "daemon.admission.json"

_SPEND_CACHE: dict[str, tuple[tuple[int, int, int] | None, UsageSummary]] = {}
_SPEND_CACHE_LOCK = threading.Lock()


def resolve_global_root(value: Path | str | None) -> Path:
    return Path(value) if value is not None else core_paths.global_root()


def project_life_dir(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    """Resolve one safe direct child of ``<global_root>/projects``."""
    projects = (resolve_global_root(global_root) / "projects").resolve()
    try:
        life_dir = (projects / sid).resolve()
    except (OSError, ValueError):
        return None
    if life_dir.parent != projects or not life_dir.is_dir():
        return None
    return life_dir


def daemon_dict(status: DaemonStatus) -> dict[str, Any]:
    budget = resolve_effective_budget(status)
    protocol_compatible, protocol_error = daemon_protocol_compatibility(status)
    return {
        "alive": bool(status.alive),
        "pid": status.pid,
        "started_at_iso": status.started_at_iso,
        "uptime_seconds": status.uptime_seconds,
        "backend": status.backend,
        "per_mission_cap_usd": budget.per_mission_cap_usd,
        "daily_cap_usd": budget.daily_cap_usd,
        "global_daily_cap_usd": budget.global_daily_cap_usd,
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
        "message": str(exc or type(exc).__name__)[:500],
    }


def daemon_error_dict(exc: BaseException) -> dict[str, Any]:
    try:
        budget = resolve_effective_budget(None)
        per_mission = budget.per_mission_cap_usd
        daily = budget.daily_cap_usd
        global_daily = budget.global_daily_cap_usd
    except Exception:  # noqa: BLE001 - the original diagnostic is authoritative
        per_mission = daily = global_daily = None
    return {
        "alive": False,
        "pid": None,
        "started_at_iso": None,
        "uptime_seconds": None,
        "backend": None,
        "per_mission_cap_usd": per_mission,
        "daily_cap_usd": daily,
        "global_daily_cap_usd": global_daily,
        "read_status": "error",
        "read_error": str(exc or type(exc).__name__)[:500],
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
        out.append({
            "role": config.role,
            "backend": config.backend,
            "backend_label": config.backend_label,
            "model": config.model,
            "effort": config.effort,
            "active": bool(activity.active) if activity else False,
            "label": activity.label if activity else "idle",
            "status": activity.status if activity else "idle",
            "age_s": activity.age_s if activity else None,
        })
    return out


def session_dict(meta: SessionMeta | None, sid: str) -> dict[str, Any]:
    if meta is None:
        return {
            "id": sid,
            "display_name": "",
            "objective": "",
            "last_active": 0.0,
            "cwd": "",
            "launch_cwd": "",
        }
    return {
        "id": meta.id,
        "display_name": meta.display_name,
        "objective": meta.objective,
        "last_active": meta.last_active,
        "cwd": meta.cwd,
        "launch_cwd": meta.launch_cwd,
    }


def compact_backlog_item(item: Any) -> dict[str, Any]:
    objective = str(getattr(item, "objective", "") or "")
    title = str(getattr(item, "title", "") or "").strip()
    if not title:
        title = objective.splitlines()[0][:180]
    return {
        "id": str(getattr(item, "id", "")),
        "title": title,
        "objective": "" if title else objective[:240],
        "status": str(getattr(item, "status", "pending")),
        "priority": int(getattr(item, "priority", 100)),
        "max_cost_usd": float(getattr(item, "max_cost_usd", 0.0)),
        "iterate": bool(getattr(item, "iterate", False)),
        "pending_question": str(getattr(item, "pending_question", "") or "")[:500],
    }


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
        total = project_usage_summary(life_dir)
    except Exception as exc:  # noqa: BLE001 - snapshot remains available
        total = _empty_usage_summary()
        if diagnostics is not None:
            diagnostics.append(diagnostic("usage", exc))
        return total
    with _SPEND_CACHE_LOCK:
        _SPEND_CACHE[key] = (stat_signature(life_dir / "usage.jsonl"), total)
    return total


def stat_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        int(getattr(stat, "st_ino", 0) or 0),
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
        daemon = daemon_dict(status)
        if daemon["read_status"] == "error":
            diagnostics.append({
                "section": "daemon",
                "error_type": "StatusReadError",
                "message": str(daemon["read_error"]),
            })
        if daemon["protocol_compatible"] is False:
            diagnostics.append({
                "section": "daemon_protocol",
                "error_type": "ProtocolMismatch",
                "message": str(daemon["protocol_error"]),
            })
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
        daemon["backend_label"] = (
            engineer.get("backend_label") or daemon.get("backend")
        )

    items: list[Any] = []
    try:
        memory = LifeMemory.open(life_dir)
        items = list(memory.backlog.all())
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
        session = session_dict(read_session_meta(root, sid), sid)
    except Exception as exc:  # noqa: BLE001
        session = session_dict(None, sid)
        diagnostics.append(diagnostic("session", exc))

    try:
        request_usage = provider_usage_snapshot(root=root)
    except Exception as exc:  # noqa: BLE001
        request_usage = None
        diagnostics.append(diagnostic("request_usage", exc))

    try:
        cost_control = cost_control_snapshot(global_root=root)
    except Exception as exc:  # noqa: BLE001
        cost_control = None
        diagnostics.append(diagnostic("cost_control", exc))

    try:
        daemon_commands = daemon_command_snapshot(life_dir)
    except Exception as exc:  # noqa: BLE001
        daemon_commands = None
        diagnostics.append(diagnostic("daemon_commands", exc))

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
        "request_usage": request_usage,
        "cost_control": cost_control,
        "daemon_commands": daemon_commands,
    }
    admission = read_daemon_admission(life_dir, diagnostics=diagnostics)
    if admission is not None:
        snapshot["daemon_admission"] = admission
    if compact:
        try:
            continuous = read_continuous_state(life_dir)
        except Exception as exc:  # noqa: BLE001
            continuous = None
            diagnostics.append(diagnostic("continuous", exc))
        snapshot["continuous"] = (
            {
                "enabled": continuous.enabled,
                "objective": continuous.objective,
                "done_reason": continuous.done_reason,
                "done_at": continuous.done_at,
            }
            if continuous is not None
            else {"enabled": False, "objective": ""}
        )
        snapshot["pending_questions"] = [
            compact_backlog_item(item)
            for item in items
            if getattr(item, "pending_question", "")
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
        life_dir = root / "projects" / meta.id
        try:
            status = read_daemon_status(life_dir)
            item["daemon_alive"] = bool(status.alive)
            item["daemon_pid"] = status.pid
            item["uptime_seconds"] = status.uptime_seconds
        except Exception:  # noqa: BLE001 - picker remains available
            item["daemon_alive"] = False
            item["daemon_pid"] = None
            item["uptime_seconds"] = None
        try:
            continuous = read_continuous_state(life_dir)
            campaign_objective = str(continuous.objective or "").strip()
        except Exception:  # noqa: BLE001
            campaign_objective = ""
        if not item.get("objective") and campaign_objective:
            item["objective"] = campaign_objective
        label = item.get("display_name") or item.get("objective") or ""
        if not label:
            try:
                label = first_operator_text(life_dir)[:60]
            except Exception:  # noqa: BLE001
                label = ""
        item["label"] = label or meta.id
        out.append(item)
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
    "project_life_dir",
    "read_daemon_admission",
    "resolve_global_root",
    "roles_list",
    "session_dict",
    "settled_spend",
    "stat_signature",
]
