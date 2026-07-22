"""Durable local metrics, SLO evaluation, and Prometheus rendering."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .cost_control import CostControlLockBusyError, cost_control_snapshot
from .event_catalog import canonical_event_type, event_spec

METRICS_FILE = "metrics.jsonl"
METRICS_LOCK_FILE = "metrics.lock"
METRICS_SCHEMA_VERSION = 1
DEFAULT_METRICS_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_METRICS_RETENTION_DAYS = 7
DEFAULT_METRICS_MAX_ARCHIVES = 14

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_WEB_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
_PROVIDERS = frozenset({"codex", "copilot", "claude", "opencode", "memory"})
_CALL_STATUSES = frozenset({"completed", "error", "denied"})
_PRICING_STATUSES = frozenset({"priced", "partial", "unpriced", "not_billed", "unknown"})
_COMMAND_OPERATIONS = frozenset({"create", "start", "stop", "drain", "kill", "replace"})
_COMMAND_STATUSES = frozenset({"applied", "failed", "rejected"})


def metrics_root_for_project(project_root: Path | str) -> Path:
    project = Path(project_root).expanduser()
    if project.parent.name == "projects":
        return project.parent.parent
    return project


def http_route_template(scope: Mapping[str, Any], raw_path: str = "") -> str:
    """Return a bounded HTTP metric path without request-specific identifiers."""
    route = scope.get("route")
    candidate = (
        getattr(route, "path_format", None)
        or getattr(route, "path", None)
    )
    if isinstance(candidate, str) and candidate.startswith("/"):
        return candidate[:256]
    return "<unmatched>"


def _bounded_enum(value: Any, allowed: frozenset[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _normalize_labels(name: str, labels: dict[str, Any] | None) -> dict[str, str]:
    source = labels or {}
    if name == "web.request":
        method = str(source.get("method") or "").strip().upper()
        raw_status = source.get("status")
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            status = 0
        path = str(source.get("path") or "<unmatched>").strip()
        if not path.startswith("/") and path != "<unmatched>":
            path = "<unmatched>"
        return {
            "method": method if method in _WEB_METHODS else "OTHER",
            "path": path[:256],
            "status": str(status) if 100 <= status <= 599 else "unknown",
        }
    if name == "provider.call":
        return {
            "provider": _bounded_enum(source.get("provider"), _PROVIDERS, "other"),
            "status": _bounded_enum(source.get("status"), _CALL_STATUSES, "error"),
            "pricing_status": _bounded_enum(
                source.get("pricing_status"), _PRICING_STATUSES, "unknown"
            ),
        }
    if name == "daemon.command":
        return {
            "operation": _bounded_enum(
                source.get("operation"), _COMMAND_OPERATIONS, "other"
            ),
            "status": _bounded_enum(
                source.get("status"), _COMMAND_STATUSES, "failed"
            ),
        }
    if name == "event.validation_failure":
        canonical = canonical_event_type(source.get("type"))
        return {"type": canonical if event_spec(canonical) is not None else "unknown"}
    return {
        str(key)[:64]: str(item)[:256]
        for key, item in list(source.items())[:12]
    }


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return max(1, value)


@contextmanager
def _metrics_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / METRICS_LOCK_FILE
    key = str(lock_path.resolve())
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _archive_paths(root: Path) -> list[Path]:
    try:
        return sorted(root.glob("metrics.*.jsonl"), key=lambda path: path.name)
    except OSError:
        return []


def _prune_archives(root: Path, timestamp: float) -> None:
    retention_days = _env_positive_int(
        "ARGUS_SKILL_METRICS_RETENTION_DAYS",
        DEFAULT_METRICS_RETENTION_DAYS,
    )
    max_archives = _env_positive_int(
        "ARGUS_SKILL_METRICS_MAX_ARCHIVES",
        DEFAULT_METRICS_MAX_ARCHIVES,
    )
    cutoff = timestamp - retention_days * 86_400
    survivors: list[tuple[float, Path]] = []
    for path in _archive_paths(root):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if modified < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        survivors.append((modified, path))
    for _modified, path in sorted(survivors, reverse=True)[max_archives:]:
        try:
            path.unlink()
        except OSError:
            pass


def _rotate_if_needed(root: Path, path: Path, incoming_bytes: int, timestamp: float) -> None:
    max_bytes = _env_positive_int(
        "ARGUS_SKILL_METRICS_MAX_BYTES",
        DEFAULT_METRICS_MAX_BYTES,
    )
    try:
        current_bytes = path.stat().st_size
    except OSError:
        current_bytes = 0
    if current_bytes <= 0 or current_bytes + incoming_bytes <= max_bytes:
        return
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(timestamp))
    archive = root / f"metrics.{stamp}.{os.getpid()}.{uuid.uuid4().hex[:8]}.jsonl"
    try:
        os.replace(path, archive)
    except OSError:
        return
    _prune_archives(root, timestamp)


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
    wall_clock = time.time()
    row = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "ts": wall_clock if timestamp is None else float(timestamp),
        "name": str(name),
        "value": float(value),
        "labels": _normalize_labels(str(name), labels),
        "fields": fields or {},
        "pid": os.getpid(),
    }
    try:
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    try:
        encoded_bytes = len(line.encode("utf-8")) + 1
        with _metrics_lock(path_root):
            _rotate_if_needed(path_root, path, encoded_bytes, wall_clock)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        return


def _day_start(timestamp: float) -> float:
    local = time.localtime(timestamp)
    return time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )


def _records(root: Path, since: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Metrics files are append-only JSONL. Reading without the writer/rotation
    # lock is safe: an already-open archive inode remains readable after rename,
    # and a concurrent partial final line is simply ignored as invalid JSON.
    # Holding the writer lock while scanning a large daily log made unrelated
    # HTTP responses wait for observability aggregation to finish.
    paths = [*_archive_paths(root), root / METRICS_FILE]
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            continue
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


def _http_status(row: dict[str, Any]) -> int:
    try:
        return int(row.get("labels", {}).get("status", 0))
    except (TypeError, ValueError):
        return 0


def metrics_snapshot(
    *,
    root: Path | str,
    now: float | None = None,
    cost_control: Mapping[str, Any] | None = None,
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
    web_5xx = sum(_http_status(row) >= 500 for row in web)
    web_5xx_rate = web_5xx / len(web) if web else 0.0
    web_p95_ms = _percentile(
        (float(row.get("fields", {}).get("duration_ms") or 0.0) for row in web),
        0.95,
    )

    validation_failures = int(sum(
        float(row.get("value") or 0.0)
        for row in _metric_rows(rows, "event.validation_failure")
    ))
    if cost_control is not None:
        cost = dict(cost_control)
    else:
        try:
            cost = cost_control_snapshot(global_root=path_root, now=timestamp)
        except CostControlLockBusyError as exc:
            # A concurrent settlement is ordinary process activity, not an SLO
            # failure. The Web snapshot path passes its cached projection here;
            # standalone metric readers expose the transient miss explicitly.
            cost = {
                "active_reservations": -1,
                "unresolved_calls": -1,
                "blocking_unresolved_calls": 0,
                "policy": "unknown",
                "snapshot_stale": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            cost = {
                "active_reservations": 0,
                "unresolved_calls": -1,
                "blocking_unresolved_calls": -1,
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
    # Partial/unpriced calls remain visible in cost telemetry, but the current
    # admission policy explicitly treats them as non-blocking. Only a blocking
    # unresolved call (or an unreadable cost-control snapshot) is an SLO breach.
    blocking_unresolved = int(
        cost.get("blocking_unresolved_calls", cost.get("unresolved_calls", 0)) or 0
    )
    if blocking_unresolved < 0:
        violations.append("cost control snapshot unavailable")
    elif blocking_unresolved > 0:
        violations.append(
            f"blocking unresolved cost calls: {blocking_unresolved}"
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
        "# TYPE argus_cost_unresolved_calls gauge",
        f'argus_cost_unresolved_calls {cost.get("unresolved_calls", 0)}',
        "# TYPE argus_event_validation_failures_total gauge",
        f'argus_event_validation_failures_total {snapshot["event_validation_failures"]}',
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "METRICS_FILE",
    "METRICS_LOCK_FILE",
    "METRICS_SCHEMA_VERSION",
    "http_route_template",
    "metrics_root_for_project",
    "metrics_snapshot",
    "record_metric",
    "render_prometheus",
]
