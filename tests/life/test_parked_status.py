"""Every reader understands a ``parked`` row before any writer produces one.

A later release writes ``status="parked"`` with ``outcome["park"]["reason"]``
in place of the ``paused_*`` statuses. A daemon handoff can roll back to this
release, so it must already load such a row as the park it is: never
re-armed as pending work, its reason, owner and reservations read the same
way an older ``paused_*`` row's are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.stop_kinds import PARK_STATUSES, park_reason_of, park_rule
from argus.life.memory import (
    Backlog,
    BacklogItem,
    EventJournal,
    park_reason,
    planner_may_retire_park,
)
from argus.life.mission_outcome import mission_outcome_class, mission_outcome_dimensions
from argus.life.status import count_backlog_statuses


def _row(backlog: Backlog, item_id: str) -> BacklogItem:
    return next(item for item in backlog.all() if item.id == item_id)


def _parked(backlog: Backlog, reason: str, **fields) -> BacklogItem:
    item = backlog.add(BacklogItem.new(title=reason, objective="o", **fields))
    backlog.claim_next(owner="primary")
    return backlog.update(item.id, status="parked", outcome={"park": {"reason": reason}})


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("paused", ""),
        ("paused_budget", "budget_exhausted"),
        ("paused_cost", "cost_unreconciled"),
        ("paused_provider_cooldown", "provider_cooldown"),
        ("paused_provider_fence", "provider_fence"),
        ("paused_daemon_shutdown", "daemon_shutdown"),
        ("paused_operator", "operator_pause"),
        ("paused_external_work", "external_work"),
        ("research_incomplete", "research_open"),
        ("paused_no_breakthrough", "research_open"),
        ("exhausted_current_methods", "research_open"),
        ("infra_blocked", "infra"),
        ("paused_something_newer", ""),
        ("pending", None),
        ("running", None),
        ("done", None),
    ],
)
def test_an_older_status_names_its_park_reason(status: str, reason: str | None) -> None:
    assert park_reason_of(status) == reason
    if status != "paused_something_newer":
        assert (status in PARK_STATUSES) == (reason is not None)


def test_a_parked_row_carries_its_reason_and_survives_a_reload(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _parked(backlog, "external_work")

    reloaded = _row(Backlog(tmp_path / "backlog.jsonl"), item.id)
    assert reloaded.status == "parked"
    assert park_reason(reloaded) == "external_work"
    with pytest.raises(Exception, match="resume_paused"):
        backlog.update(item.id, status="pending")


def test_a_parked_row_keeps_its_rule(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    held = _parked(backlog, "external_work", node_key="train")
    backlog.add(BacklogItem.new(title="again", objective="o", node_key="train"))
    research = _parked(backlog, "research_open")
    fence = _parked(backlog, "provider_fence")
    question = _parked(backlog, "research_open")
    backlog.update(question.id, pending_question="Which split?")

    # External work keeps its reservation, so the same node is not claimed twice.
    assert park_rule(park_reason(held)).holds_resources
    assert backlog.claim_next(owner="worker-2") is None
    # Only parks nobody else releases are the Planner's to retire.
    assert planner_may_retire_park(_row(backlog, research.id))
    assert not planner_may_retire_park(_row(backlog, fence.id))
    assert not planner_may_retire_park(_row(backlog, question.id))
    assert not planner_may_retire_park(_row(backlog, held.id))
    assert count_backlog_statuses(backlog.all())[2] == 4


def test_an_automatic_resume_class_includes_parked_rows_with_its_reason(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    shutdown = _parked(backlog, "daemon_shutdown")
    fence = _parked(backlog, "provider_fence")

    resumed = backlog.resume_paused_statuses({"paused_daemon_shutdown"})

    assert [item.id for item in resumed] == [shutdown.id]
    assert _row(backlog, shutdown.id).status == "pending"
    assert _row(backlog, fence.id).status == "parked"


def test_parked_events_read_like_the_pauses_they_replace() -> None:
    assert mission_outcome_class("parked", False, "research_open") == "incomplete"
    assert mission_outcome_class("parked", False, "infra") == "blocked"
    assert mission_outcome_class("parked", False, "external_work") == "ended"
    dimensions = mission_outcome_dimensions(
        status="parked", success=False, park_reason="provider_cooldown",
    )
    assert dimensions["execution_status"] == "paused"

    entry = EventJournal._entry_from_event({
        "type": "life.mission.completed",
        "status": "parked",
        "park_reason": "budget_exhausted",
        "success": False,
    })
    assert entry is not None and entry.kind == "budget_pause"
