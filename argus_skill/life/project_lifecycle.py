"""Project lifecycle state machine (F5).

Models a research project's lifecycle as an explicit state machine so the
supervisor can quarantine stuck projects, refuse budget to abandoned ones,
and surface clear status in ``--status`` / Telegram digests.

States::

    incubating  →  the project has an objective and a backlog but hasn't
                   produced any evidence yet. Initial state.
    running     →  experiments are producing evidence bundles. Backlog has
                   live tasks.
    writing     →  evidence collection is "done enough"; current focus is
                   on draft / review / submission stages.
    quarantined →  hard-stopped. Daemon won't allocate tokens until a human
                   resumes or archives.
    done        →  submission stage cleared; project produced its final
                   artifact (e.g. paper PDF + reviewable bundle).
    archived    →  done OR quarantined project the user has decided to
                   close out. Terminal.

Transition rules are deterministic, time- and budget-aware, and meant to be
called once per supervisor tick. They never call out to LLMs.

The module is intentionally standalone — it owns its own ``ProjectStatus``
dataclass and ``LifecycleEvent`` log entries. Integration with the
supervisor / BacklogItem schema is a follow-up; for now any caller can
build a ProjectStatus from existing memory state and use this to decide
the next action.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Literal


class ProjectState(str, Enum):
    INCUBATING = "incubating"
    RUNNING = "running"
    WRITING = "writing"
    QUARANTINED = "quarantined"
    DONE = "done"
    ARCHIVED = "archived"


# Default thresholds. Override per-project if needed; supervisor reads
# these to decide automatic transitions.
DEFAULT_INCUBATING_MAX_DAYS = 7
DEFAULT_RUNNING_MAX_DAYS = 14
DEFAULT_WRITING_MAX_DAYS = 21
DEFAULT_QUARANTINE_BUDGET_FRACTION = 0.80


@dataclass(frozen=True)
class LifecycleEvent:
    """One transition decision, suitable for journaling."""

    at: datetime
    from_state: ProjectState
    to_state: ProjectState
    reason: str

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
        }


@dataclass
class ProjectStatus:
    """Observable state of a project at one point in time.

    All fields are populated by the caller (typically reading from
    LifeMemory / backlog state); this module never reads the filesystem.
    """

    project_id: str
    state: ProjectState
    created_at: datetime
    last_evidence_at: datetime | None = None  # newest evidence bundle mtime
    last_progress_at: datetime | None = None  # newest backlog progress event
    last_state_change_at: datetime | None = None
    budget_usd: float = 0.0  # total budget allocated
    spent_usd: float = 0.0   # actual cost incurred so far
    has_draft: bool = False
    has_submission_artifact: bool = False
    consecutive_no_progress_ticks: int = 0
    # Configurable thresholds (defaulted but overridable per-project)
    incubating_max_days: int = DEFAULT_INCUBATING_MAX_DAYS
    running_max_days: int = DEFAULT_RUNNING_MAX_DAYS
    writing_max_days: int = DEFAULT_WRITING_MAX_DAYS
    quarantine_budget_fraction: float = DEFAULT_QUARANTINE_BUDGET_FRACTION

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_evidence_at": _iso_or_none(self.last_evidence_at),
            "last_progress_at": _iso_or_none(self.last_progress_at),
            "last_state_change_at": _iso_or_none(self.last_state_change_at),
            "budget_usd": self.budget_usd,
            "spent_usd": self.spent_usd,
            "budget_fraction_spent": (
                self.spent_usd / self.budget_usd if self.budget_usd > 0 else 0.0
            ),
            "has_draft": self.has_draft,
            "has_submission_artifact": self.has_submission_artifact,
            "consecutive_no_progress_ticks": self.consecutive_no_progress_ticks,
        }


def _iso_or_none(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _days_since(now: datetime, then: datetime | None) -> float | None:
    if then is None:
        return None
    return (now - then).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Transition policy — pure functions, no side effects
# ---------------------------------------------------------------------------


def decide_next_state(
    status: ProjectStatus,
    *,
    now: datetime | None = None,
) -> LifecycleEvent | None:
    """Return the next transition event, or ``None`` if no transition is
    warranted at this tick.

    Order of precedence:

    1. Terminal states (done / archived) never transition.
    2. Submission artifact present → done.
    3. Budget exhausted (≥ quarantine_budget_fraction) → quarantine.
    4. State-specific timeouts (incubating / running / writing too long
       without expected progress signal) → quarantine.
    5. Natural progression: incubating → running (first evidence), running →
       writing (draft started).
    """
    now = now or datetime.now(timezone.utc)

    # 1. Terminal states.
    if status.state in (ProjectState.DONE, ProjectState.ARCHIVED):
        return None

    # 2. Submission artifact reached: project is done regardless of state.
    if status.has_submission_artifact and status.state != ProjectState.DONE:
        return LifecycleEvent(
            at=now,
            from_state=status.state,
            to_state=ProjectState.DONE,
            reason="submission_artifact_present",
        )

    # 3. Budget exhaustion → quarantine (unless already quarantined or done).
    if (
        status.state != ProjectState.QUARANTINED
        and status.budget_usd > 0
        and (status.spent_usd / status.budget_usd)
        >= status.quarantine_budget_fraction
        and not status.has_draft
    ):
        # Hitting 80% budget without even a draft is the canonical
        # "project is going nowhere" signal.
        return LifecycleEvent(
            at=now,
            from_state=status.state,
            to_state=ProjectState.QUARANTINED,
            reason=(
                f"budget {status.spent_usd:.2f}/{status.budget_usd:.2f} "
                f"≥{status.quarantine_budget_fraction:.0%} with no draft"
            ),
        )

    # 4. State-specific timeouts.
    if status.state == ProjectState.INCUBATING:
        age = _days_since(now, status.created_at) or 0.0
        if age > status.incubating_max_days and status.last_evidence_at is None:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.INCUBATING,
                to_state=ProjectState.QUARANTINED,
                reason=(
                    f"incubating {age:.1f}d > {status.incubating_max_days}d "
                    f"without any evidence"
                ),
            )
        # 5. Natural progression: first evidence appeared → running.
        if status.last_evidence_at is not None:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.INCUBATING,
                to_state=ProjectState.RUNNING,
                reason="first_evidence_bundle_appeared",
            )

    elif status.state == ProjectState.RUNNING:
        # No new evidence for N days while still in running.
        if status.last_evidence_at is not None:
            since_evidence = _days_since(now, status.last_evidence_at) or 0.0
            if since_evidence > status.running_max_days:
                return LifecycleEvent(
                    at=now,
                    from_state=ProjectState.RUNNING,
                    to_state=ProjectState.QUARANTINED,
                    reason=(
                        f"no new evidence for {since_evidence:.1f}d "
                        f"> {status.running_max_days}d"
                    ),
                )
        # Natural progression: draft started → writing.
        if status.has_draft:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.RUNNING,
                to_state=ProjectState.WRITING,
                reason="draft_started",
            )

    elif status.state == ProjectState.WRITING:
        # Writing too long without submission artifact.
        anchor = (
            status.last_progress_at
            or status.last_state_change_at
            or status.last_evidence_at
            or status.created_at
        )
        since_anchor = _days_since(now, anchor) or 0.0
        if since_anchor > status.writing_max_days:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.WRITING,
                to_state=ProjectState.QUARANTINED,
                reason=(
                    f"writing stage idle {since_anchor:.1f}d "
                    f"> {status.writing_max_days}d without submission"
                ),
            )

    # No transition.
    return None


def apply_event(
    status: ProjectStatus, event: LifecycleEvent
) -> ProjectStatus:
    """Return a new ``ProjectStatus`` with the transition applied. Pure."""
    return replace(
        status,
        state=event.to_state,
        last_state_change_at=event.at,
        # Quarantine entry resets the no-progress counter so a resume gets
        # a fresh window.
        consecutive_no_progress_ticks=(
            0
            if event.to_state == ProjectState.QUARANTINED
            else status.consecutive_no_progress_ticks
        ),
    )


# ---------------------------------------------------------------------------
# User-initiated transitions (not policy-driven)
# ---------------------------------------------------------------------------


def resume(
    status: ProjectStatus, *, now: datetime | None = None, reason: str = "user_resume"
) -> tuple[ProjectStatus, LifecycleEvent]:
    """User flips a quarantined project back to its working state."""
    if status.state != ProjectState.QUARANTINED:
        raise ValueError(
            f"cannot resume project in state {status.state.value!r}"
        )
    now = now or datetime.now(timezone.utc)
    # Heuristic: if there's no evidence yet → incubating; if there's a draft
    # → writing; otherwise running.
    if status.has_draft:
        target = ProjectState.WRITING
    elif status.last_evidence_at is None:
        target = ProjectState.INCUBATING
    else:
        target = ProjectState.RUNNING
    event = LifecycleEvent(
        at=now,
        from_state=status.state,
        to_state=target,
        reason=reason,
    )
    return apply_event(status, event), event


def archive(
    status: ProjectStatus,
    *,
    now: datetime | None = None,
    reason: str = "user_archive",
) -> tuple[ProjectStatus, LifecycleEvent]:
    """User closes out a project (done or quarantined). Terminal."""
    if status.state == ProjectState.ARCHIVED:
        raise ValueError("project already archived")
    now = now or datetime.now(timezone.utc)
    event = LifecycleEvent(
        at=now,
        from_state=status.state,
        to_state=ProjectState.ARCHIVED,
        reason=reason,
    )
    return apply_event(status, event), event


# ---------------------------------------------------------------------------
# Budget gate — exported so supervisor can call before allocating tokens
# ---------------------------------------------------------------------------


def is_token_allocatable(status: ProjectStatus) -> bool:
    """True iff supervisor should be willing to spend tokens on this project
    right now."""
    return status.state in (
        ProjectState.INCUBATING,
        ProjectState.RUNNING,
        ProjectState.WRITING,
    )


# ---------------------------------------------------------------------------
# Bulk tick — convenience for "advance every project in one shot"
# ---------------------------------------------------------------------------


def tick_all(
    statuses: Iterable[ProjectStatus],
    *,
    now: datetime | None = None,
) -> list[tuple[ProjectStatus, LifecycleEvent | None]]:
    """Apply one policy tick to each project. Returns (new_status, event_or_none)
    tuples in input order."""
    now = now or datetime.now(timezone.utc)
    out: list[tuple[ProjectStatus, LifecycleEvent | None]] = []
    for status in statuses:
        event = decide_next_state(status, now=now)
        if event is None:
            out.append((status, None))
        else:
            out.append((apply_event(status, event), event))
    return out


# ---------------------------------------------------------------------------
# Observable status inference — used by the supervisor each tick
# ---------------------------------------------------------------------------


def infer_observable_status(
    project_root: Path,
    *,
    project_id: str | None = None,
    budget_usd: float = 0.0,
    spent_usd: float = 0.0,
) -> ProjectStatus:
    """Build a :class:`ProjectStatus` from observable signals in
    ``project_root``. Caller supplies budget numbers (those live in
    LifeBudget, not the filesystem).

    Signals read:

    * ``project_root`` exists → ``created_at`` from its mtime
    * newest mtime under ``benchmarks/evidence/`` → ``last_evidence_at``
    * ``paper/main.tex`` exists → ``has_draft = True``
    * ``paper/main.pdf`` exists → ``has_submission_artifact = True``

    The initial ``state`` is a heuristic based on the strongest observable
    signal; the supervisor overlays persisted state on top so a quarantined
    project stays quarantined across daemon restarts.
    """
    project_root = Path(project_root)
    project_id = project_id or project_root.name

    created_at = datetime.now(timezone.utc)
    if project_root.exists():
        created_at = datetime.fromtimestamp(
            project_root.stat().st_mtime, tz=timezone.utc
        )

    last_evidence_at: datetime | None = None
    evidence_root = project_root / "benchmarks" / "evidence"
    if evidence_root.is_dir():
        mtimes: list[datetime] = []
        for child in evidence_root.iterdir():
            if not child.is_dir():
                continue
            mtimes.append(
                datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
            )
        if mtimes:
            last_evidence_at = max(mtimes)

    has_draft = (project_root / "paper" / "main.tex").exists()
    has_submission_artifact = (project_root / "paper" / "main.pdf").exists()

    if has_submission_artifact:
        initial_state = ProjectState.WRITING
    elif has_draft:
        initial_state = ProjectState.WRITING
    elif last_evidence_at is not None:
        initial_state = ProjectState.RUNNING
    else:
        initial_state = ProjectState.INCUBATING

    return ProjectStatus(
        project_id=project_id,
        state=initial_state,
        created_at=created_at,
        last_evidence_at=last_evidence_at,
        last_progress_at=last_evidence_at,
        last_state_change_at=created_at,
        budget_usd=float(budget_usd),
        spent_usd=float(spent_usd),
        has_draft=has_draft,
        has_submission_artifact=has_submission_artifact,
    )
