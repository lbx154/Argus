"""Durable local metrics, SLO evaluation, and Prometheus rendering."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .cost_control import cost_control_snapshot

METRICS_FILE = "metrics.jsonl"
METRICS_SCHEMA_VERSION = 1

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def metrics_root_for_project(project_root: Path | str) -> Path:
    project = Path(project_root).expanduser()
    if project.parent.name == "projects":
        return project.parent.parent
    return project


def record_metric(
    root: Path | str,
    name: str,
    *,
    value: float = 1.0,
    labels: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
    timestamp: float | None = None,
) -> None:
    path_root = Path(root).expanduser()
    path = path_root / METRICS_FILE
    row = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "ts": time.time() if timestamp is None else float(timestamp),
        "name": str(name),
        "value": float(value),
        "labels": {
            str(key): str(item)
            for key, item in (labels or {}).items()
        },
        "fields": fields or {},
        "pid": os.getpid(),
    }
    try:
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    try:
        path_root.mkdir(parents=True, exist_ok=True)
        with lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def _day_start(timestamp: float) -> float:
    local = time.localtime(timestamp)
    return time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )


def _records(root: Path, since: float) -> list[dict[str, Any]]:
    path = root / METRICS_FILE
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    with handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            try:
                ts = float(row.get("ts") or 0.0)
            except (TypeError, ValueError):
                continue
            if ts >= since:
                rows.append(row)
    return rows


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    rows = sorted(float(value) for value in values)
    if not rows:
        return None
    rank = max(0, math.ceil(percentile * len(rows)) - 1)
    return rows[min(rank, len(rows) - 1)]


def _metric_rows(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("name") == name]


def metrics_snapshot(
    *,
    root: Path | str,
    now: float | None = None,
) -> dict[str, Any]:
    timestamp = time.time() if now is None else float(now)
    path_root = Path(root).expanduser()
    rows = _records(path_root, _day_start(timestamp))

    provider = _metric_rows(rows, "provider.call")
    provider_completed = sum(
        row.get("labels", {}).get("status") == "completed" for row in provider
    )
    provider_errors = sum(
        row.get("labels", {}).get("status") == "error" for row in provider
    )
    provider_denied = sum(
        row.get("labels", {}).get("status") == "denied" for row in provider
    )
    provider_attempts = provider_completed + provider_errors
    provider_success_rate = (
        provider_completed / provider_attempts if provider_attempts else 1.0
    )
    provider_p95_ms = _percentile(
        (
            float(row.get("fields", {}).get("duration_ms") or 0.0)
            for row in provider
        ),
        0.95,
    )

    commands = _metric_rows(rows, "daemon.command")
    command_applied = sum(
        row.get("labels", {}).get("status") == "applied" for row in commands
    )
    command_failed = sum(
        row.get("labels", {}).get("status") == "failed" for row in commands
    )
    command_rejected = sum(
        row.get("labels", {}).get("status") == "rejected" for row in commands
    )
    command_attempts = command_applied + command_failed
    command_success_rate = command_applied / command_attempts if command_attempts else 1.0

    web = _metric_rows(rows, "web.request")
    web_5xx = sum(
        int(row.get("labels", {}).get("status", "0")) >= 500
        for row in web
    )
    web_5xx_rate = web_5xx / len(web) if web else 0.0
    web_p95_ms = _percentile(
        (float(row.get("fields", {}).get("duration_ms") or 0.0) for row in web),
        0.95,
    )

    validation_failures = int(sum(
        float(row.get("value") or 0.0)
        for row in _metric_rows(rows, "event.validation_failure")
    ))
    try:
        cost = cost_control_snapshot(global_root=path_root, now=timestamp)
    except Exception as exc:  # noqa: BLE001
        cost = {
            "active_reservations": 0,
            "reserved_usd": 0.0,
            "unresolved_calls": -1,
            "policy": "unknown",
            "error": f"{type(exc).__name__}: {exc}",
        }

    violations: list[str] = []
    if provider_attempts >= 5 and provider_success_rate < 0.95:
        violations.append(
            f"provider success rate {provider_success_rate:.1%} < 95%"
        )
    if command_attempts >= 3 and command_success_rate < 0.98:
        violations.append(
            f"daemon command success rate {command_success_rate:.1%} < 98%"
        )
    if web and web_5xx_rate > 0.01:
        violations.append(f"WebAPI 5xx rate {web_5xx_rate:.1%} > 1%")
    if validation_failures > 0:
        violations.append(f"event validation failures: {validation_failures}")
    if int(cost.get("unresolved_calls") or 0) != 0:
        violations.append(
            f"unresolved cost calls: {cost.get('unresolved_calls')}"
        )

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "day_start": _day_start(timestamp),
        "provider": {
            "completed": provider_completed,
            "errors": provider_errors,
            "denied": provider_denied,
            "success_rate": provider_success_rate,
            "p95_duration_ms": provider_p95_ms,
        },
        "daemon_commands": {
            "applied": command_applied,
            "failed": command_failed,
            "rejected": command_rejected,
            "success_rate": command_success_rate,
        },
        "web": {
            "requests": len(web),
            "errors_5xx": web_5xx,
            "error_rate_5xx": web_5xx_rate,
            "p95_duration_ms": web_p95_ms,
        },
        "event_validation_failures": validation_failures,
        "cost_control": cost,
        "slo": {
            "status": "healthy" if not violations else "degraded",
            "violations": violations,
        },
    }


def render_prometheus(snapshot: dict[str, Any]) -> str:
    provider = snapshot["provider"]
    commands = snapshot["daemon_commands"]
    web = snapshot["web"]
    cost = snapshot["cost_control"]
    healthy = 1 if snapshot["slo"]["status"] == "healthy" else 0
    lines = [
        "# TYPE argus_slo_healthy gauge",
        f"argus_slo_healthy {healthy}",
        "# TYPE argus_provider_calls_total gauge",
        f'argus_provider_calls_total{{status="completed"}} {provider["completed"]}',
        f'argus_provider_calls_total{{status="error"}} {provider["errors"]}',
        f'argus_provider_calls_total{{status="denied"}} {provider["denied"]}',
        "# TYPE argus_provider_success_ratio gauge",
        f'argus_provider_success_ratio {provider["success_rate"]}',
        "# TYPE argus_daemon_commands_total gauge",
        f'argus_daemon_commands_total{{status="applied"}} {commands["applied"]}',
        f'argus_daemon_commands_total{{status="failed"}} {commands["failed"]}',
        f'argus_daemon_commands_total{{status="rejected"}} {commands["rejected"]}',
        "# TYPE argus_web_requests_total gauge",
        f'argus_web_requests_total {web["requests"]}',
        "# TYPE argus_web_5xx_total gauge",
        f'argus_web_5xx_total {web["errors_5xx"]}',
        "# TYPE argus_cost_reserved_usd gauge",
        f'argus_cost_reserved_usd {cost.get("reserved_usd", 0.0)}',
        "# TYPE argus_cost_unresolved_calls gauge",
        f'argus_cost_unresolved_calls {cost.get("unresolved_calls", 0)}',
        "# TYPE argus_event_validation_failures_total gauge",
        f'argus_event_validation_failures_total {snapshot["event_validation_failures"]}',
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "METRICS_FILE",
    "METRICS_SCHEMA_VERSION",
    "metrics_root_for_project",
    "metrics_snapshot",
    "record_metric",
    "render_prometheus",
]
