"""Tests for argus_skill.life.project_lifecycle (F5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from argus_skill.life.project_lifecycle import (
    DEFAULT_INCUBATING_MAX_DAYS,
    DEFAULT_RUNNING_MAX_DAYS,
    DEFAULT_WRITING_MAX_DAYS,
    LifecycleEvent,
    ProjectState,
    ProjectStatus,
    apply_event,
    archive,
    decide_next_state,
    is_token_allocatable,
    resume,
    tick_all,
)


def _utc(dt: str) -> datetime:
    return datetime.fromisoformat(dt).replace(tzinfo=timezone.utc)


def _fresh(state: ProjectState = ProjectState.INCUBATING, **overrides) -> ProjectStatus:
    base = dict(
        project_id="proj-1",
        state=state,
        created_at=_utc("2026-05-01T00:00:00"),
        last_evidence_at=None,
        last_progress_at=None,
        last_state_change_at=_utc("2026-05-01T00:00:00"),
        budget_usd=1000.0,
        spent_usd=0.0,
        has_draft=False,
        has_submission_artifact=False,
    )
    base.update(overrides)
    return ProjectStatus(**base)


# ---------------------------------------------------------------------------
# decide_next_state: terminal + submission artifact
# ---------------------------------------------------------------------------


def test_done_state_never_transitions() -> None:
    status = _fresh(state=ProjectState.DONE)
    assert decide_next_state(status, now=_utc("2027-01-01T00:00:00")) is None


def test_archived_state_never_transitions() -> None:
    status = _fresh(state=ProjectState.ARCHIVED)
    assert decide_next_state(status, now=_utc("2027-01-01T00:00:00")) is None


def test_submission_artifact_promotes_to_done_from_writing() -> None:
    status = _fresh(state=ProjectState.WRITING, has_submission_artifact=True)
    event = decide_next_state(status, now=_utc("2026-05-15T00:00:00"))
    assert event is not None
    assert event.to_state == ProjectState.DONE
    assert event.reason == "submission_artifact_present"


def test_submission_artifact_promotes_even_from_running() -> None:
    status = _fresh(state=ProjectState.RUNNING, has_submission_artifact=True)
    event = decide_next_state(status)
    assert event is not None
    assert event.to_state == ProjectState.DONE


# ---------------------------------------------------------------------------
# Budget exhaustion → quarantine
# ---------------------------------------------------------------------------


def test_budget_exhaustion_without_draft_quarantines() -> None:
    status = _fresh(
        state=ProjectState.RUNNING,
        budget_usd=1000.0,
        spent_usd=850.0,  # 85% > 80% threshold
        has_draft=False,
        last_evidence_at=_utc("2026-05-04T00:00:00"),
    )
    event = decide_next_state(status, now=_utc("2026-05-05T00:00:00"))
    assert event is not None
    assert event.to_state == ProjectState.QUARANTINED
    assert "budget" in event.reason


def test_budget_exhaustion_with_draft_does_not_quarantine() -> None:
    # If there's at least a draft, hitting 80% budget is acceptable —
    # we'd rather finish than quarantine.
    status = _fresh(
        state=ProjectState.WRITING,
        budget_usd=1000.0,
        spent_usd=850.0,
        has_draft=True,
        last_evidence_at=_utc("2026-05-04T00:00:00"),
        last_progress_at=_utc("2026-05-04T00:00:00"),
        last_state_change_at=_utc("2026-05-04T00:00:00"),
    )
    event = decide_next_state(status, now=_utc("2026-05-05T00:00:00"))
    # No quarantine event; either None (no transition) or natural step.
    if event is not None:
        assert event.to_state != ProjectState.QUARANTINED


# ---------------------------------------------------------------------------
# Timeouts → quarantine
# ---------------------------------------------------------------------------


def test_incubating_timeout_quarantines() -> None:
    status = _fresh(state=ProjectState.INCUBATING, last_evidence_at=None)
    now = _utc("2026-05-01T00:00:00") + timedelta(days=DEFAULT_INCUBATING_MAX_DAYS + 1)
    event = decide_next_state(status, now=now)
    assert event is not None
    assert event.to_state == ProjectState.QUARANTINED
    assert "incubating" in event.reason


def test_running_no_new_evidence_timeout_quarantines() -> None:
    status = _fresh(
        state=ProjectState.RUNNING,
        last_evidence_at=_utc("2026-05-01T00:00:00"),
    )
    now = _utc("2026-05-01T00:00:00") + timedelta(days=DEFAULT_RUNNING_MAX_DAYS + 1)
    event = decide_next_state(status, now=now)
    assert event is not None
    assert event.to_state == ProjectState.QUARANTINED
    assert "no new evidence" in event.reason


def test_writing_idle_timeout_quarantines() -> None:
    status = _fresh(
        state=ProjectState.WRITING,
        has_draft=True,
        last_progress_at=_utc("2026-05-01T00:00:00"),
    )
    now = _utc("2026-05-01T00:00:00") + timedelta(days=DEFAULT_WRITING_MAX_DAYS + 1)
    event = decide_next_state(status, now=now)
    assert event is not None
    assert event.to_state == ProjectState.QUARANTINED
    assert "writing" in event.reason


# ---------------------------------------------------------------------------
# Natural progression
# ---------------------------------------------------------------------------


def test_incubating_advances_to_running_on_first_evidence() -> None:
    status = _fresh(
        state=ProjectState.INCUBATING,
        last_evidence_at=_utc("2026-05-02T00:00:00"),
    )
    event = decide_next_state(status, now=_utc("2026-05-03T00:00:00"))
    assert event is not None
    assert event.to_state == ProjectState.RUNNING
    assert event.reason == "first_evidence_bundle_appeared"


def test_running_advances_to_writing_when_draft_started() -> None:
    status = _fresh(
        state=ProjectState.RUNNING,
        last_evidence_at=_utc("2026-05-04T00:00:00"),
        has_draft=True,
    )
    event = decide_next_state(status, now=_utc("2026-05-05T00:00:00"))
    assert event is not None
    assert event.to_state == ProjectState.WRITING
    assert event.reason == "draft_started"


# ---------------------------------------------------------------------------
# apply_event & user-initiated transitions
# ---------------------------------------------------------------------------


def test_apply_event_returns_new_status_with_state_set() -> None:
    status = _fresh()
    event = LifecycleEvent(
        at=_utc("2026-05-05T00:00:00"),
        from_state=ProjectState.INCUBATING,
        to_state=ProjectState.RUNNING,
        reason="manual",
    )
    new = apply_event(status, event)
    assert new.state == ProjectState.RUNNING
    assert new.last_state_change_at == event.at
    # Original is unmodified.
    assert status.state == ProjectState.INCUBATING


def test_resume_from_quarantine_to_writing_when_draft_present() -> None:
    status = _fresh(state=ProjectState.QUARANTINED, has_draft=True)
    new, event = resume(status, now=_utc("2026-05-10T00:00:00"))
    assert new.state == ProjectState.WRITING
    assert event.from_state == ProjectState.QUARANTINED
    assert event.to_state == ProjectState.WRITING


def test_resume_from_quarantine_to_running_when_evidence_present() -> None:
    status = _fresh(
        state=ProjectState.QUARANTINED,
        last_evidence_at=_utc("2026-05-03T00:00:00"),
    )
    new, event = resume(status)
    assert new.state == ProjectState.RUNNING


def test_resume_from_quarantine_to_incubating_when_no_evidence_or_draft() -> None:
    status = _fresh(state=ProjectState.QUARANTINED)
    new, event = resume(status)
    assert new.state == ProjectState.INCUBATING


def test_resume_refuses_non_quarantined_state() -> None:
    status = _fresh(state=ProjectState.RUNNING)
    with pytest.raises(ValueError):
        resume(status)


def test_archive_moves_any_state_to_archived() -> None:
    for src in (
        ProjectState.INCUBATING,
        ProjectState.RUNNING,
        ProjectState.WRITING,
        ProjectState.QUARANTINED,
        ProjectState.DONE,
    ):
        new, event = archive(_fresh(state=src))
        assert new.state == ProjectState.ARCHIVED
        assert event.from_state == src


def test_archive_refuses_already_archived() -> None:
    with pytest.raises(ValueError):
        archive(_fresh(state=ProjectState.ARCHIVED))


# ---------------------------------------------------------------------------
# is_token_allocatable + tick_all
# ---------------------------------------------------------------------------


def test_is_token_allocatable_for_active_states() -> None:
    for state in (ProjectState.INCUBATING, ProjectState.RUNNING, ProjectState.WRITING):
        assert is_token_allocatable(_fresh(state=state))


def test_is_token_allocatable_blocks_terminal_and_quarantine() -> None:
    for state in (ProjectState.QUARANTINED, ProjectState.DONE, ProjectState.ARCHIVED):
        assert not is_token_allocatable(_fresh(state=state))


def test_tick_all_advances_each_project_independently() -> None:
    a = _fresh(project_id="a", state=ProjectState.INCUBATING,
               last_evidence_at=_utc("2026-05-02T00:00:00"))
    b = _fresh(project_id="b", state=ProjectState.RUNNING,
               last_evidence_at=_utc("2026-05-02T00:00:00"))
    c = _fresh(project_id="c", state=ProjectState.DONE)

    results = tick_all([a, b, c], now=_utc("2026-05-03T00:00:00"))

    assert results[0][0].state == ProjectState.RUNNING
    assert results[0][1] is not None  # event fired
    # b: still RUNNING, no draft yet, evidence is fresh, no transition.
    assert results[1][0].state == ProjectState.RUNNING
    assert results[1][1] is None
    # c: terminal, untouched.
    assert results[2][0].state == ProjectState.DONE
    assert results[2][1] is None


def test_to_dict_includes_budget_fraction() -> None:
    status = _fresh(budget_usd=1000.0, spent_usd=250.0)
    d = status.to_dict()
    assert d["budget_fraction_spent"] == pytest.approx(0.25)
    assert d["state"] == ProjectState.INCUBATING.value
