"""Generic external-work liveness protocol for long-running jobs.

The harness uses this protocol only to decide whether an explicit wait can pause
mission progress clocks. External liveness never implies scientific progress or
acceptance.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

EXTERNAL_WORK_REGISTRY = ".argus_external_work"
EXTERNAL_WORK_PROTOCOL_VERSION = 1
_WAIT_SENTINEL = "WAIT_FOR_EXTERNAL_WORK:"
_WAIT_SENTINEL_RE = re.compile(
    r"^WAIT_FOR_EXTERNAL_WORK:\s*(?:`([^`\s]+)`|([^\s`]+))\s*$"
)
_TERMINAL_STATES = frozenset({
    "complete", "completed", "done", "error", "failed", "timeout",
    "crashed", "cancelled", "canceled", "stopped", "early_stopped",
})
_LEGACY_INFLIGHT_STATES = frozenset({
    "running", "starting", "preflight", "discussing",
})
_LEGACY_DEGRADED_HEALTH = frozenset({"degrading", "stuck", "diverging"})
_CADENCE_FLOOR_SECONDS = 30.0
_CADENCE_CAP_SECONDS = 900.0


class ExternalWorkState(str, Enum):
    """Control-plane state of external work, independent of scientific merit."""

    RUNNING_HEALTHY = "running_healthy"
    NEEDS_ATTENTION = "needs_attention"
    STALLED = "stalled"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class ExternalWorkStatus:
    """Validated external-work status exposed to the Engineer loop."""

    work_id: str
    state: ExternalWorkState
    description: str = ""
    source: str = "external"
    heartbeat_at: float = 0.0
    stale_after_seconds: float = 1800.0
    poll_after_seconds: float = 120.0
    outcome: str = ""
    reason: str = ""
    evidence_paths: tuple[str, ...] = ()
    activity_paths: tuple[str, ...] = ()

    @property
    def waitable(self) -> bool:
        return self.state is ExternalWorkState.RUNNING_HEALTHY


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_relative_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    paths: list[str] = []
    for raw in value[:32]:
        candidate = str(raw or "").strip().replace("\\", "/")
        if not candidate or candidate.startswith("/") or ".." in Path(candidate).parts:
            continue
        if candidate.startswith("./"):
            candidate = candidate[2:]
        if candidate:
            paths.append(candidate[:500])
    return tuple(dict.fromkeys(paths))


def _pid_alive(pid: object) -> bool:
    if pid is None:
        return True
    try:
        value = int(str(pid).strip())
    except (TypeError, ValueError):
        return True
    if value <= 0:
        return True
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


def _registry_files(workdir: Path | str, directory: str) -> list[Path]:
    root = Path(workdir) / directory
    try:
        resolved_root = root.resolve(strict=False)
        candidates = sorted(
            path for path in root.glob("*.json") if not path.name.endswith(".tmp")
        )
    except OSError:
        return []
    contained: list[Path] = []
    for path in candidates:
        try:
            path.resolve(strict=False).relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        contained.append(path)
    return contained


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _canonical_status(
    record: dict[str, Any], *, path: Path, now: float
) -> ExternalWorkStatus | None:
    try:
        version = int(record.get("version", 0) or 0)
    except (TypeError, ValueError):
        return None
    work_id = str(record.get("work_id") or "").strip()
    state_value = str(record.get("state") or "").strip().lower()
    if version != EXTERNAL_WORK_PROTOCOL_VERSION or not work_id:
        return None
    try:
        state = ExternalWorkState(state_value)
    except ValueError:
        return None
    heartbeat_at = _coerce_float(record.get("heartbeat_at"), 0.0)
    stale_after = max(1.0, _coerce_float(record.get("stale_after_seconds"), 1800.0))
    if state is ExternalWorkState.RUNNING_HEALTHY and (
        heartbeat_at <= 0 or now - heartbeat_at > stale_after
    ):
        state = ExternalWorkState.STALLED
        reason = "external-work heartbeat is stale"
    else:
        reason = " ".join(str(record.get("reason") or "").split())[:500]
    return ExternalWorkStatus(
        work_id=work_id,
        state=state,
        description=" ".join(str(record.get("description") or "").split())[:240],
        source=str(record.get("source") or "external").strip()[:80],
        heartbeat_at=heartbeat_at,
        stale_after_seconds=stale_after,
        poll_after_seconds=max(
            1.0, _coerce_float(record.get("poll_after_seconds"), 120.0)
        ),
        outcome=str(record.get("outcome") or "").strip()[:200],
        reason=reason,
        evidence_paths=_safe_relative_paths(record.get("evidence_paths")),
        activity_paths=_safe_relative_paths(record.get("activity_paths")),
    )


def _legacy_status(
    record: dict[str, Any], *, path: Path, now: float
) -> ExternalWorkStatus | None:
    work_id = str(record.get("task_id") or path.stem).strip()
    if not work_id:
        return None
    state_value = str(record.get("state") or "").strip().lower()
    monitor_interval = max(
        1.0, _coerce_float(record.get("monitor_interval"), 120.0)
    )
    stale_after = max(monitor_interval, _CADENCE_CAP_SECONDS) * 2.0
    heartbeat_at = _coerce_float(record.get("heartbeat_at"), 0.0)
    if heartbeat_at <= 0:
        try:
            heartbeat_at = path.stat().st_mtime
        except OSError:
            heartbeat_at = 0.0
    mode = str(record.get("mode") or "direct").strip().lower()
    health = str(record.get("last_supervisor_health") or "").strip().lower()
    decision = str(record.get("last_supervisor_decision") or "").strip().lower()
    concern = " ".join(str(
        record.get("last_supervisor_concern") or record.get("concern") or ""
    ).split())
    if state_value not in _LEGACY_INFLIGHT_STATES:
        state = ExternalWorkState.TERMINAL
        reason = f"legacy subagent state={state_value or 'unknown'}"
    elif not _pid_alive(record.get("worker_pid", record.get("pid"))):
        state = ExternalWorkState.STALLED
        reason = "legacy subagent worker process is not alive"
    elif heartbeat_at <= 0 or now - heartbeat_at > stale_after:
        state = ExternalWorkState.STALLED
        reason = "legacy subagent supervisor heartbeat is stale"
    elif (
        mode != "supervised"
        or state_value == "discussing"
        or health in _LEGACY_DEGRADED_HEALTH
        or concern.lower().strip(".") not in {
            "", "none", "n/a", "na", "null", "nil", "-", "no concern",
            "no concerns", "no issues", "no issue",
        }
        or decision == "early_stop"
    ):
        state = ExternalWorkState.NEEDS_ATTENTION
        reason = "legacy subagent requires Engineer attention"
    else:
        state = ExternalWorkState.RUNNING_HEALTHY
        reason = ""
    return ExternalWorkStatus(
        work_id=work_id,
        state=state,
        description=" ".join(str(record.get("description") or "").split())[:240],
        source="subagent",
        heartbeat_at=heartbeat_at,
        stale_after_seconds=stale_after,
        poll_after_seconds=monitor_interval,
        outcome=str(record.get("outcome") or decision).strip()[:200],
        reason=reason,
        evidence_paths=_safe_relative_paths(record.get("evidence_paths")),
    )


def scan_external_work(
    workdir: Path | str,
    *,
    now: float | None = None,
    include_legacy_subagents: bool = True,
) -> list[ExternalWorkStatus]:
    """Return validated current states, preferring canonical records by work id."""
    observed_at = time.time() if now is None else float(now)
    statuses: dict[str, ExternalWorkStatus] = {}
    for path in _registry_files(workdir, EXTERNAL_WORK_REGISTRY):
        record = _read_record(path)
        status = (
            _canonical_status(record, path=path, now=observed_at)
            if record is not None
            else None
        )
        if status is not None:
            statuses[status.work_id] = status
    if include_legacy_subagents:
        for path in _registry_files(workdir, ".argus_subagents"):
            record = _read_record(path)
            status = (
                _legacy_status(record, path=path, now=observed_at)
                if record is not None
                else None
            )
            if status is not None and status.work_id not in statuses:
                statuses[status.work_id] = status
    return [statuses[key] for key in sorted(statuses)]


def inspect_external_work(
    workdir: Path | str, work_id: str, *, now: float | None = None
) -> ExternalWorkStatus | None:
    """Find a work record by its declared id without constructing a path from it."""
    target = str(work_id or "").strip()
    if not target:
        return None
    return next(
        (status for status in scan_external_work(workdir, now=now)
         if status.work_id == target),
        None,
    )


def parse_external_wait_sentinel(message: str | None) -> str | None:
    """Legacy adapter for pre-control-file Engineer sessions."""
    if not message:
        return None
    text = message.strip()
    lines = text.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
    ):
        text = "\n".join(lines[1:-1]).strip()
    non_empty = [line.strip() for line in text.splitlines() if line.strip()]
    if not non_empty:
        return None
    match = _WAIT_SENTINEL_RE.fullmatch(non_empty[-1])
    return (match.group(1) or match.group(2)) if match is not None else None


def cadence_seconds(status: ExternalWorkStatus) -> float:
    return min(
        _CADENCE_CAP_SECONDS,
        max(_CADENCE_FLOOR_SECONDS, status.poll_after_seconds),
    )


def wait_for_external_work_cadence(
    workdir: Path | str,
    work_id: str,
    *,
    sleep: Callable[[float], None] | None = None,
    poll_interval: float = 15.0,
    now: Callable[[], float] | None = None,
) -> tuple[str, float]:
    """Wait one cadence while healthy, waking on any explicit state transition."""
    clock = now if now is not None else time.time
    sleeper = sleep if sleep is not None else time.sleep
    target = inspect_external_work(workdir, work_id, now=clock())
    if target is None:
        return ("unknown", 0.0)
    if not target.waitable:
        return (target.state.value, 0.0)
    budget = cadence_seconds(target)
    step = max(1.0, min(float(poll_interval), budget))
    waited = 0.0
    while waited < budget:
        chunk = min(step, budget - waited)
        sleeper(chunk)
        waited += chunk
        current = inspect_external_work(workdir, work_id, now=clock())
        if current is None:
            return ("unknown", waited)
        if not current.waitable:
            return (current.state.value, waited)
    return ("cadence_elapsed", waited)


def render_external_work_advisory(
    workdir: Path | str, *, now: float | None = None
) -> str:
    """Render canonical external work only; legacy subagents keep their old advisory."""
    statuses = scan_external_work(
        workdir, now=now, include_legacy_subagents=False
    )
    if not statuses:
        return ""
    lines = [
        "## External work status",
        "These records describe execution liveness only; they are not scientific evidence.",
    ]
    for status in statuses:
        detail = status.reason or status.outcome or status.description
        suffix = f" - {detail}" if detail else ""
        lines.append(f"- `{status.work_id}`: {status.state.value}{suffix}")
    if any(status.waitable for status in statuses):
        lines.extend([
            "If all remaining work depends on one RUNNING_HEALTHY item, end your response with",
            "    WAIT_FOR_EXTERNAL_WORK: <work_id>",
            "Argus will pause progress clocks for one declared cadence. File growth alone never counts as progress.",
        ])
    return "\n".join(lines)
