"""Runtime-owned teams as external work the lead waits on instead of polling.

A research idea portfolio is a team of route workers the runtime forms and the
daemon-resident Curator staffs. Until now the lead Engineer had no legitimate
way to wait for it: the external-work protocol knew subagents and registry
files, not teams, so the lead spent model rounds on ``team status``, ``ps`` and
``tail`` (thirty-one turns and 1.15 million input tokens in one round on the
stable web trial, 2026-09-16). Projecting each team onto the same
``ExternalWorkStatus`` the round loop already understands gives the lead the
structured wait, and lets the runtime wait on the lead's behalf. The team
package sits above the engineer layer, so it registers this reader instead of
being imported by the round loop.
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..engineer.external_work import (
    ExternalWorkState,
    ExternalWorkStatus,
    register_external_work_source,
)

TEAM_WORK_PREFIX = "team:"
RUNTIME_OWNER = "runtime"
_IN_FLIGHT_STATES = frozenset({"claimed", "running"})
_ATTENTION_STATES = frozenset({"failed", "blocked"})
# The Curator lives inside the daemon: while the daemon runs, the team is
# staffed, so a quiet task board is patience, not a dead owner.
_TEAM_STALE_AFTER_SECONDS = 6 * 3600.0
# One cadence of the harness wait; the task board is a handful of small files.
_TEAM_POLL_AFTER_SECONDS = 300.0
_MAX_FACTS = 8


def team_work_id(team_id: str) -> str:
    return f"{TEAM_WORK_PREFIX}{team_id}"


def _relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except (OSError, ValueError):
        return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _task_fact(task: dict[str, Any], *, now: float) -> str:
    task_id = str(task.get("task_id") or "")
    state = str(task.get("state") or "")
    owner = str(task.get("owner") or "")
    started = float(task.get("claim_ts") or 0.0)
    fact = f"{task_id}: {state}"
    if owner:
        fact += f" by {owner}"
    if state in _IN_FLIGHT_STATES and started > 0:
        fact += f" for {max(0, int((now - started) // 60))}m"
    reason = str(task.get("reason") or task.get("pending_question") or "").strip()
    if state in _ATTENTION_STATES and reason:
        fact += f" — {reason[:120]}"
    return fact


def team_work_status(
    project_root: Path | str,
    marker: dict[str, Any],
    *,
    now: float | None = None,
) -> ExternalWorkStatus | None:
    """Project one campaign marker onto the external-work protocol."""
    from . import pool, task_board

    observed_at = time.time() if now is None else float(now)
    project = Path(project_root)
    team_id = str(marker.get("team_id") or "").strip()
    team_root = Path(str(marker.get("team_root") or "")).expanduser()
    if not team_id or not team_root.is_dir():
        return None
    tasks = task_board.snapshot(team_root)
    if not tasks:
        return None
    pool_doc = pool.read(team_root)
    pool_state = str(pool_doc.get("state") or "running")
    counts = Counter(str(task.get("state") or "") for task in tasks)
    in_flight = sum(counts[state] for state in _IN_FLIGHT_STATES)
    pending = counts.get("pending", 0)
    attention = sum(counts[state] for state in _ATTENTION_STATES)
    done = counts.get("done", 0)
    width = pool_doc.get("width")
    staffed = pool_state == "running" and (width is None or int(width or 0) > 0)

    heartbeat = max(
        [float(marker.get("created_ts") or 0.0), _mtime(team_root / "pool.json")]
        + [
            float(task.get(field) or 0.0)
            for task in tasks
            for field in ("heartbeat_ts", "claim_ts", "finished_ts")
        ]
    )
    description = (
        f"team {team_id}: {in_flight} running, {pending} pending, {done} done"
        + (f", {attention} need attention" if attention else "")
    )
    ordered = sorted(
        tasks,
        key=lambda task: (
            str(task.get("state") or "") not in _ATTENTION_STATES,
            str(task.get("state") or "") not in _IN_FLIGHT_STATES,
            str(task.get("task_id") or ""),
        ),
    )
    facts = tuple(_task_fact(task, now=observed_at) for task in ordered[:_MAX_FACTS])

    outcome = ""
    reason = ""
    if pool_state == "dissolved":
        state = ExternalWorkState.TERMINAL
        outcome = "dissolved"
    elif attention and not in_flight:
        state = ExternalWorkState.NEEDS_ATTENTION
        reason = (
            f"{attention} task(s) failed or wait for an operator answer and no "
            "worker is running; settle them before anything else"
        )
    elif in_flight or (pending and staffed):
        state = ExternalWorkState.RUNNING_HEALTHY
    elif pending:
        state = ExternalWorkState.NEEDS_ATTENTION
        reason = (
            f"pool is {pool_state}"
            + (" with width 0" if staffed is False and pool_state == "running" else "")
            + f" while {pending} task(s) are still pending"
        )
    else:
        state = ExternalWorkState.TERMINAL
        outcome = "all tasks done"

    artifacts = _relative(team_root / "artifacts", project)
    return ExternalWorkStatus(
        work_id=team_work_id(team_id),
        state=state,
        description=description,
        source="team",
        heartbeat_at=heartbeat,
        stale_after_seconds=_TEAM_STALE_AFTER_SECONDS,
        poll_after_seconds=_TEAM_POLL_AFTER_SECONDS,
        outcome=outcome,
        reason=reason,
        evidence_paths=(artifacts,) if artifacts else (),
        started_at=float(marker.get("created_ts") or 0.0),
        facts=facts,
        owner=str(marker.get("owner") or ""),
    )


def scan_team_work(
    project_root: Path | str,
    *,
    now: float | None = None,
) -> list[ExternalWorkStatus]:
    """Every registered campaign of the project, as external work."""
    from . import registry

    statuses: list[ExternalWorkStatus] = []
    try:
        markers = registry.list_markers(Path(project_root))
    except OSError:
        return statuses
    for marker in markers:
        try:
            status = team_work_status(project_root, marker, now=now)
        except (OSError, ValueError, TypeError):
            continue
        if status is not None:
            statuses.append(status)
    return statuses


def _external_work_source(workdir: Path, now: float) -> list[ExternalWorkStatus]:
    return scan_team_work(workdir, now=now)


# Importing the team package is what makes its campaigns visible to the round
# loop's external-work scan; the lead's process imports it while forming or
# describing the portfolio, long before the first round.
register_external_work_source(_external_work_source)


__all__ = [
    "RUNTIME_OWNER",
    "TEAM_WORK_PREFIX",
    "scan_team_work",
    "team_work_id",
    "team_work_status",
]
