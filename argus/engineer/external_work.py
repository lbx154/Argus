"""Generic external-work liveness protocol for long-running jobs.

It reports what registered background work is doing; it never waits on it.
External liveness never implies scientific progress or acceptance.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.daemon_lock import is_pid_running
from ..core.json_codec import is_finite_number
from ..core.process_identity import process_identity_is_running

EXTERNAL_WORK_REGISTRY = ".argus_external_work"
EXTERNAL_WORK_PROTOCOL_VERSION = 1
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"
_MISSION_ENV = "ARGUS_PLUGIN_PARENT_MISSION_ID"
_SUBAGENT_INFLIGHT_STATES = frozenset({
    "running", "starting", "preflight", "waiting_resource", "discussing",
})
_SUBAGENT_DEGRADED_HEALTH = frozenset({"degrading", "stuck", "diverging"})
# This caps time between observable facts, never the duration of external work.
_CADENCE_CAP_SECONDS = 900.0
# A direct subagent launched without a timeout whose command has written no
# output for this long is reported stalled: a hung command otherwise looks
# healthy for as long as its process lives, and a wait on it never ends.
_DIRECT_SILENCE_STALE_SECONDS = 6 * 3600.0


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
    # Owners renew heartbeats; this threshold detects a dead owner, not slow work.
    stale_after_seconds: float = 1800.0
    activity_stale_after_seconds: float = 0.0
    activity_silence_seconds: float = 0.0
    poll_after_seconds: float = 120.0
    outcome: str = ""
    reason: str = ""
    evidence_paths: tuple[str, ...] = ()
    activity_paths: tuple[str, ...] = ()
    run_id: str = ""
    started_at: float = 0.0
    facts: tuple[str, ...] = ()
    # Who started the work: "runtime" for work Argus itself set up on the
    # lead's behalf (an idea portfolio), "lead" for work the Engineer asked
    # for, "" when the record does not say.
    owner: str = ""

    @property
    def waitable(self) -> bool:
        return self.state is ExternalWorkState.RUNNING_HEALTHY


ExternalWorkSource = Callable[[Path, float], list[ExternalWorkStatus]]
_EXTERNAL_WORK_SOURCES: list[ExternalWorkSource] = []


def register_external_work_source(source: ExternalWorkSource) -> ExternalWorkSource:
    """Let a higher layer project its own long-running work onto this protocol.

    The round loop lives below the packages that own teams and other
    supervised work, so they register a reader here instead of being imported.
    A source receives ``(workdir, now)`` and returns validated statuses; a
    source that raises is skipped for that scan.
    """
    if source not in _EXTERNAL_WORK_SOURCES:
        _EXTERNAL_WORK_SOURCES.append(source)
    return source


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    # Invalid liveness timestamps must not bypass stale-heartbeat comparisons;
    # invalid durations must not turn a bounded wait into an infinite one.
    return number if is_finite_number(number) else default


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
    try:
        value = int(str(pid).strip())
    except (TypeError, ValueError):
        return False
    return is_pid_running(value)


def _recorded_process_alive(record: dict[str, Any], pid_field: str) -> bool:
    identity_fields = {
        "pid": ("process_identity", "command_process_identity"),
        "worker_pid": ("worker_process_identity",),
    }
    identity = next(
        (
            record.get(field)
            for field in identity_fields.get(pid_field, ())
            if isinstance(record.get(field), dict)
        ),
        None,
    )
    if (
        identity is None
        and pid_field == "pid"
        and record.get("pid") == record.get("worker_pid")
        and isinstance(record.get("worker_process_identity"), dict)
    ):
        identity = record["worker_process_identity"]
    try:
        pid = int(record.get(pid_field) or 0)
    except (TypeError, ValueError):
        return False
    return process_identity_is_running(pid, identity, pid_is_running=_pid_alive)


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


def _log_silence_seconds(
    record: dict[str, Any], *, path: Path, now: float
) -> float:
    """Seconds since this job last wrote a byte anywhere in its own logs.

    Used only when the owner declared no activity paths. A job still starting
    up has written nothing yet, so silence is measured from ``started_at``
    when the directory is empty, and not at all when neither is known.
    """
    # The directory is named from the record file, not from a work_id field --
    # live records mostly do not carry one, and reading it there reported a
    # healthy job as silent for four hours.
    newest = 0.0
    try:
        logs = path.parent / f"{path.stem}_logs"
        if logs.is_dir():
            for entry in logs.iterdir():
                if entry.is_file() and entry.stat().st_size > 0:
                    newest = max(newest, entry.stat().st_mtime)
    except OSError:
        return 0.0
    if newest > 0:
        return max(0.0, now - newest)
    started_at = _coerce_float(record.get("started_at"), 0.0)
    return max(0.0, now - started_at) if started_at > 0 else 0.0


def _activity_silence_seconds(
    record: dict[str, Any], *, path: Path, now: float
) -> float:
    """Return silence on owner-declared project-local activity paths.

    The external owner updates ``heartbeat_at`` to prove liveness. Reusing that
    timestamp as progress lets a wedged job remain healthy forever, so activity
    is measured only from the files the owner explicitly declared. Missing
    paths can be judged only when the owner also supplied ``started_at``.
    """
    relative_paths = _safe_relative_paths(record.get("activity_paths"))
    if not relative_paths:
        # Every live job in seven campaigns declared none, so this returned
        # zero for all of them and nothing was ever judged stalled. A job that
        # writes nothing anywhere is silent by any definition, and its own log
        # directory always exists: run-01 waited four hours on a claim run with
        # zero bytes on both streams at 0% CPU while a GPU sat idle, and only
        # stopped because a human looked. Silence on the logs is an
        # observation, not a verdict about the work.
        return _log_silence_seconds(record, path=path, now=now)
    try:
        project_root = path.parent.parent.resolve(strict=False)
    except OSError:
        return 0.0
    newest = 0.0
    for relative in relative_paths:
        try:
            candidate = (project_root / relative).resolve(strict=False)
            candidate.relative_to(project_root)
            newest = max(newest, candidate.stat().st_mtime)
        except (OSError, ValueError):
            continue
    if newest > 0:
        return max(0.0, now - newest)
    started_at = _coerce_float(record.get("started_at"), 0.0)
    return max(0.0, now - started_at) if started_at > 0 else 0.0


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
    activity_stale_after = max(
        1.0,
        _coerce_float(record.get("activity_stale_after_seconds"), stale_after),
    )
    activity_silence = 0.0
    if state is ExternalWorkState.RUNNING_HEALTHY:
        if heartbeat_at <= 0 or now - heartbeat_at > stale_after:
            state = ExternalWorkState.STALLED
            reason = "external-work heartbeat is stale"
        else:
            activity_silence = _activity_silence_seconds(
                record,
                path=path,
                now=now,
            )
            reason = " ".join(str(record.get("reason") or "").split())[:500]
    else:
        reason = " ".join(str(record.get("reason") or "").split())[:500]
    return ExternalWorkStatus(
        work_id=work_id,
        state=state,
        description=" ".join(str(record.get("description") or "").split())[:240],
        source=str(record.get("source") or "external").strip()[:80],
        heartbeat_at=heartbeat_at,
        stale_after_seconds=stale_after,
        activity_stale_after_seconds=activity_stale_after,
        activity_silence_seconds=activity_silence,
        poll_after_seconds=max(
            1.0, _coerce_float(record.get("poll_after_seconds"), 120.0)
        ),
        outcome=str(record.get("outcome") or "").strip()[:200],
        reason=reason,
        evidence_paths=_safe_relative_paths(record.get("evidence_paths")),
        activity_paths=_safe_relative_paths(record.get("activity_paths")),
        started_at=_coerce_float(record.get("started_at"), 0.0),
    )


def _direct_silence(record: dict[str, Any], now: float) -> float:
    """How long a direct subagent's command has written nothing; 0 without logs.

    Writing is judged from the output logs the worker records and from the run
    directory and its entries -- a training step that checkpoints without
    printing is busy, not hung -- starting no earlier than the launch.
    """
    logs = [record.get(key) for key in ("stdout_log", "stderr_log") if record.get(key)]
    if not logs:
        return 0.0
    paths = [Path(str(log_path)) for log_path in logs]
    if record.get("run_dir"):
        run_dir = Path(str(record.get("cwd") or ".")) / str(record["run_dir"])
        paths.append(run_dir)
        try:
            paths.extend(run_dir.iterdir())
        except OSError:
            pass
    stamps = [_coerce_float(record.get("started_at"), 0.0)]
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(0.0, now - max(stamps))


def _subagent_status(
    record: dict[str, Any], *, path: Path, now: float
) -> ExternalWorkStatus | None:
    work_id = str(record.get("task_id") or path.stem).strip()
    if not work_id:
        return None
    state_value = str(record.get("state") or "").strip().lower()
    monitor_interval = max(
        1.0,
        _coerce_float(
            record.get("current_monitor_interval"),
            _coerce_float(record.get("monitor_interval"), 120.0),
        ),
    )
    next_check_at = _coerce_float(record.get("next_check_at"), 0.0)
    poll_after_seconds = (
        max(1.0, next_check_at - now)
        if next_check_at > 0
        else monitor_interval
    )
    stale_after = max(monitor_interval, _CADENCE_CAP_SECONDS) * 2.0
    explicit_heartbeat = max(
        _coerce_float(record.get("heartbeat_at"), 0.0),
        _coerce_float(record.get("last_heartbeat"), 0.0),
    )
    heartbeat_at = explicit_heartbeat
    if heartbeat_at <= 0:
        try:
            heartbeat_at = path.stat().st_mtime
        except OSError:
            heartbeat_at = 0.0
    mode = str(record.get("mode") or "direct").strip().lower()
    # Only an owner that renews its record has a heartbeat that can go stale:
    # the supervised worker rewrites it at every check (and stamps it while
    # discussing), and any worker queued for a resource stamps it every poll,
    # while a direct worker writes once and then waits on a command for as long
    # as that command legitimately runs. A live pid with a stale heartbeat is a
    # wedged owner, and the Engineer must hear that fact instead of waiting on
    # it forever.
    heartbeat_stale = (
        (mode == "supervised" or state_value == "waiting_resource")
        and heartbeat_at > 0
        and now - heartbeat_at > stale_after
    )
    health = str(record.get("last_supervisor_health") or "").strip().lower()
    decision = str(record.get("last_supervisor_decision") or "").strip().lower()
    concern = " ".join(str(
        record.get("last_supervisor_concern") or record.get("concern") or ""
    ).split())
    worker_pid_field = "worker_pid" if record.get("worker_pid") else "pid"
    worker_alive = _recorded_process_alive(record, worker_pid_field)
    child_alive = _recorded_process_alive(record, "pid")
    exit_status_path = str(record.get("exit_status_path") or "").strip()
    exit_status_exists = bool(
        exit_status_path and Path(exit_status_path).is_file()
    )
    resource_facts: list[str] = []
    resource_wait = record.get("resource_wait")
    if state_value == "waiting_resource" and isinstance(resource_wait, dict):
        poll_after_seconds = max(
            1.0,
            _coerce_float(resource_wait.get("poll_after_seconds"), poll_after_seconds),
        )
        holders = resource_wait.get("holders")
        holder_intents = [
            str(item.get("intent") or "(intent not declared)")
            for item in holders or []
            if isinstance(item, dict)
        ]
        resource_facts.append(
            f"resource queue position {resource_wait.get('position')}; "
            f"holder intents: {', '.join(holder_intents) or '(none visible)'}"
        )
    ledger_root = str(record.get("resource_ledger_root") or "").strip()
    grant_id = str(record.get("resource_grant_id") or "").strip()
    if (
        ledger_root
        and grant_id
        and Path(ledger_root).is_absolute()
        and Path(ledger_root).is_dir()
    ):
        try:
            from ..tools.resource_ledger.ledger import ResourceLedger  # noqa: PLC0415

            ledger_status = ResourceLedger(root=ledger_root).status(refresh_probe=False)
            grant = next(
                (item for item in ledger_status["grants"] if item.get("id") == grant_id),
                None,
            )
            for request in (grant or {}).get("yield_requests", []):
                if not isinstance(request, dict):
                    continue
                response = request.get("response")
                fact = f"someone asks for the card: {request.get('reason', '')}"
                if response:
                    fact += f"; holder response: {response.get('decision')}: {response.get('reason')}"
                resource_facts.append(fact)
        except (OSError, ValueError):
            pass
    if state_value not in _SUBAGENT_INFLIGHT_STATES:
        state = ExternalWorkState.TERMINAL
        reason = str(record.get("timeout_message") or "").strip() or (
            f"subagent state={state_value or 'unknown'}"
        )
    elif state_value == "waiting_resource" and worker_alive and not heartbeat_stale:
        state = ExternalWorkState.RUNNING_HEALTHY
        reason = resource_facts[0] if resource_facts else "waiting for a resource grant"
    elif mode == "direct" and exit_status_exists:
        state = ExternalWorkState.TERMINAL
        reason = "direct subagent exit receipt is present"
    elif mode == "direct" and not (worker_alive or child_alive):
        state = ExternalWorkState.STALLED
        reason = "direct subagent owner and durable child are not alive"
    elif mode != "direct" and not worker_alive:
        state = ExternalWorkState.STALLED
        reason = "subagent worker process is not alive"
    elif (
        mode == "direct"
        and record.get("timeout_seconds") is None
        and _direct_silence(record, now) > _DIRECT_SILENCE_STALE_SECONDS
    ):
        state = ExternalWorkState.STALLED
        reason = (
            "direct subagent has no timeout and its command has written no "
            f"output for {int(_direct_silence(record, now))}s; it may be hung"
        )
    elif heartbeat_stale:
        state = ExternalWorkState.STALLED
        reason = (
            "subagent heartbeat is stale: its worker is alive but has not "
            f"updated the record for {int(now - heartbeat_at)}s "
            f"(limit {int(stale_after)}s)"
        )
    elif (
        mode not in {"direct", "supervised"}
        or state_value == "discussing"
        or health in _SUBAGENT_DEGRADED_HEALTH
        or concern.lower().strip(".") not in {
            "", "none", "n/a", "na", "null", "nil", "-", "no concern",
            "no concerns", "no issues", "no issue",
        }
        or decision == "early_stop"
    ):
        state = ExternalWorkState.NEEDS_ATTENTION
        details = concern or health or decision or mode
        reason = f"subagent requires Engineer attention: {details}"
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
        poll_after_seconds=poll_after_seconds,
        outcome=str(record.get("outcome") or decision).strip()[:200],
        reason=reason,
        evidence_paths=_safe_relative_paths(record.get("evidence_paths")),
        run_id=str(record.get("run_id") or "").strip()[:240],
        started_at=_coerce_float(record.get("started_at"), 0.0),
        facts=tuple(resource_facts),
    )


def scan_external_work(
    workdir: Path | str,
    *,
    now: float | None = None,
    include_subagents: bool = True,
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
    if include_subagents:
        current_team_task_id = os.environ.get(_TEAM_TASK_ENV, "").strip()
        current_mission_id = os.environ.get(_MISSION_ENV, "").strip()
        for path in _registry_files(workdir, ".argus_subagents"):
            record = _read_record(path)
            if (
                record is not None
                and current_team_task_id
                and str(record.get("owner_team_task_id") or "").strip()
                != current_team_task_id
            ):
                continue
            if (
                record is not None
                and not current_team_task_id
                and current_mission_id
                and str(record.get("owner_mission_id") or "").strip()
                not in {"", current_mission_id}
            ):
                continue
            status = (
                _subagent_status(record, path=path, now=observed_at)
                if record is not None
                else None
            )
            if status is not None and status.work_id not in statuses:
                statuses[status.work_id] = status
    for source in list(_EXTERNAL_WORK_SOURCES):
        try:
            projected = source(Path(workdir), observed_at)
        except Exception:  # noqa: BLE001 — one broken source must not hide the others
            continue
        for status in projected:
            if status.work_id not in statuses:
                statuses[status.work_id] = status
    return [statuses[key] for key in sorted(statuses)]


def external_work_rows(statuses: Iterable[ExternalWorkStatus]) -> list[dict[str, str]]:
    """Work as ``(work_id, run_id, state)`` rows.

    They move when a job starts, ends or fails, and stay put while a healthy
    job only appends to its own logs.
    """
    return [
        {"work_id": status.work_id, "run_id": status.run_id, "state": status.state.value}
        for status in statuses
    ]


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


def render_external_work_advisory(
    workdir: Path | str,
    *,
    now: float | None = None,
    include_subagents: bool = True,
) -> str:
    """Render the single liveness advisory for external work and subagents."""
    statuses = [
        status
        for status in scan_external_work(
            workdir,
            now=now,
            include_subagents=include_subagents,
        )
        if not (
            status.source == "subagent"
            and status.state is ExternalWorkState.TERMINAL
        )
    ]
    if not statuses:
        return ""
    lines = [
        "## External work status",
        "These records describe execution liveness only; they are not scientific evidence.",
    ]
    for status in statuses:
        detail = status.reason or status.outcome or status.description
        suffix = f" - {detail}" if detail else ""
        lines.append(
            f"- `{status.work_id}` ({status.source}): {status.state.value}{suffix}"
        )
        for fact in status.facts:
            if fact != detail:
                lines.append(f"  - fact: {fact}")
    if any(
        status.state in {
            ExternalWorkState.NEEDS_ATTENTION,
            ExternalWorkState.STALLED,
        }
        for status in statuses
    ):
        lines.extend([
            "A NEEDS_ATTENTION or STALLED item will not finish on its own.",
            "Diagnose it now from the declared activity/evidence paths and owner state; then repair, cancel, or restart it with a concrete check.",
        ])
    return "\n".join(lines)
