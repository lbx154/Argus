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
    done        →  the reviewer certified project completion.
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

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class ProjectState(str, Enum):
    INCUBATING = "incubating"
    RUNNING = "running"
    WRITING = "writing"
    QUARANTINED = "quarantined"
    DONE = "done"
    ARCHIVED = "archived"


# Default budget-fraction threshold for the auto-quarantine plumbing rule.
# 80% of the *operator-set budget* with no draft is a budget guard, not a
# research-quality call: the project is about to run out of money without
# producing the expected artifact (paper draft). This is the only
# harness-side numeric threshold that's kept; everything else was moved
# to advisory signals (see ``advisory_time_signals``).
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
    # Configurable budget threshold. Time-based thresholds are NOT
    # configurable here because the harness no longer enforces them;
    # see ``advisory_time_signals`` for the advisory surface.
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
    2. Budget exhausted (≥ quarantine_budget_fraction) → quarantine.
    3. State-specific timeouts (incubating / running / writing too long
       without expected progress signal) → quarantine.
    4. Natural progression: incubating → running (first evidence), running →
       writing (draft started).
    """
    now = now or datetime.now(timezone.utc)

    # 1. Terminal states.
    if status.state in (ProjectState.DONE, ProjectState.ARCHIVED):
        return None

    # 2. Budget exhaustion → quarantine (unless already quarantined or done).
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

    # 3. State-specific natural progressions only.
    #    Earlier versions of this module also auto-quarantined on time
    #    spent in a stage (incubating>7d, running>14d no new evidence,
    #    writing>21d). Those constants were research-tempo judgments
    #    ("21 days writing without submission is too long") which the
    #    boundary documented in docs/VALUE_VS_HONESTY.md
    #    correctly flagged as harness-side science verdicts. They were
    #    removed; ``advisory_time_signals`` below surfaces the same
    #    numbers as informational signals the planner/reviewer can read,
    #    without the harness deciding "give up on this project".
    if status.state == ProjectState.INCUBATING:
        # Natural progression: first evidence appeared → running.
        if status.last_evidence_at is not None:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.INCUBATING,
                to_state=ProjectState.RUNNING,
                reason="first_evidence_bundle_appeared",
            )

    elif status.state == ProjectState.RUNNING:
        # Natural progression: draft started → writing.
        if status.has_draft:
            return LifecycleEvent(
                at=now,
                from_state=ProjectState.RUNNING,
                to_state=ProjectState.WRITING,
                reason="draft_started",
            )

    # WRITING: no auto-transitions. The reviewer rules on "done".

    # No transition.
    return None


@dataclass(frozen=True)
class AdvisorySignal:
    """A time-based observation about the project, surfaced to planner
    and reviewer agents. The harness does NOT act on these; they exist
    to inform the agent.
    """

    kind: str
    message: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message}


def advisory_time_signals(
    status: ProjectStatus, *, now: datetime | None = None
) -> list[AdvisorySignal]:
    """Surface time-in-state observations as plain advisory signals.

    Replaces the old hard-coded timeouts. The numbers are reported as
    facts ("you've been incubating 9.2 days"); the planner/reviewer
    decides whether that means pivot, push harder, or stay the course.
    Never blocks dispatch — that's what the harness budget cap is for.
    """
    now = now or datetime.now(timezone.utc)
    signals: list[AdvisorySignal] = []

    if status.state == ProjectState.INCUBATING:
        age = _days_since(now, status.created_at)
        if age is not None and status.last_evidence_at is None:
            signals.append(
                AdvisorySignal(
                    kind="incubating_time",
                    message=(
                        f"project has been incubating for {age:.1f} days "
                        f"with no evidence bundle yet"
                    ),
                )
            )

    elif status.state == ProjectState.RUNNING and status.last_evidence_at is not None:
        since = _days_since(now, status.last_evidence_at)
        if since is not None and since > 0:
            signals.append(
                AdvisorySignal(
                    kind="running_evidence_gap",
                    message=(
                        f"no new evidence bundle in {since:.1f} days; "
                        f"planner / reviewer: judge whether this matches "
                        f"the project's experiment cadence"
                    ),
                )
            )

    elif status.state == ProjectState.WRITING:
        anchor = (
            status.last_progress_at
            or status.last_state_change_at
            or status.last_evidence_at
            or status.created_at
        )
        since = _days_since(now, anchor)
        if since is not None and since > 0:
            signals.append(
                AdvisorySignal(
                    kind="writing_idle",
                    message=(
                        f"writing stage idle {since:.1f} days; "
                        f"reviewer: judge whether the draft is stuck "
                        f"or genuinely close to submission"
                    ),
                )
            )

    return signals


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
    """User reopens a blocked project in its observable working state."""
    if status.state not in (
        ProjectState.QUARANTINED,
        ProjectState.DONE,
        ProjectState.ARCHIVED,
    ):
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

    # Opt #5: PIPELINE_STATE.json as a secondary signal — if the agent
    # has advanced the pipeline state (research → plan → benchmark →
    # ... → submission), reflect that in lifecycle state even when the
    # filesystem-derived signals haven't caught up yet (e.g. benchmark
    # stage in progress but benchmarks/evidence/ still empty because
    # the agent is still building bundles). Pre-Opt-#5 these would
    # disagree, confusing the cockpit.
    pipeline_stage = _read_pipeline_stage(project_root)
    pipeline_inferred: ProjectState | None = None
    if pipeline_stage in ("draft", "review", "submission"):
        pipeline_inferred = ProjectState.WRITING
    elif pipeline_stage in ("plan", "benchmark", "run", "analysis"):
        pipeline_inferred = ProjectState.RUNNING

    if has_submission_artifact:
        initial_state = ProjectState.WRITING
    elif has_draft:
        initial_state = ProjectState.WRITING
    elif last_evidence_at is not None:
        initial_state = ProjectState.RUNNING
    elif pipeline_inferred is not None:
        # No filesystem evidence yet but PIPELINE_STATE says we're
        # past incubating — promote.
        initial_state = pipeline_inferred
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


def _read_pipeline_stage(project_root: Path) -> str | None:
    """Best-effort: read current_stage from research/PIPELINE_STATE.json.
    Returns None on missing / malformed file."""
    import json as _json
    path = project_root / "research" / "PIPELINE_STATE.json"
    if not path.exists():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        stage = data.get("current_stage")
        if isinstance(stage, str) and stage.strip():
            return stage.strip().lower()
    except (OSError, _json.JSONDecodeError):
        return None
    return None
