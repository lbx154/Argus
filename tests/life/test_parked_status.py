"""One park status: ``parked``, whose reason is data.

Every writer records ``status="parked"`` with ``outcome["park"]["reason"]``;
the eleven ``paused_*``-style statuses an older release wrote stay readable
and load as the park they name. A park holds only its own row and the rows
that depend on it, and the fact that ends it is the only thing that resumes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.stop_kinds import (
    OPERATOR_RESUMED_PARKS,
    RUNTIME_RESUMED_PARKS,
)
from argus.life.memory import (
    Backlog,
    BacklogItem,
    EventJournal,
    park_reason,
    planner_may_retire_park,
)
from argus.life.mission_outcome import mission_outcome_class, mission_outcome_dimensions
from argus.life.status import count_backlog_statuses

_LEGACY = [
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
]


def _row(backlog: Backlog, item_id: str) -> BacklogItem:
    return next(item for item in backlog.all() if item.id == item_id)


def _parked(backlog: Backlog, reason: str, **fields) -> BacklogItem:
    item = backlog.add(BacklogItem.new(title=reason, objective="o", **fields))
    backlog.claim_next(owner="primary")
    return backlog.update(item.id, park_reason=reason)


@pytest.mark.parametrize(("status", "reason"), [*_LEGACY, ("paused_something_newer", "")])
def test_an_older_row_loads_as_the_park_it_names(tmp_path: Path, status: str, reason: str) -> None:
    path = tmp_path / "backlog.jsonl"
    row = BacklogItem.new(title="old", objective="o").to_jsonable()
    row.update(status=status, outcome={"execution_status": "paused"})
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    loaded = _row(Backlog(path), row["id"])

    assert loaded.status == "parked"
    assert loaded.outcome == {"execution_status": "paused", "park": {"reason": reason}}
    assert park_reason(loaded) == reason


def test_a_parked_row_carries_its_reason_and_survives_a_reload(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _parked(backlog, "operator_pause")

    reloaded = _row(Backlog(tmp_path / "backlog.jsonl"), item.id)
    assert reloaded.status == "parked"
    assert park_reason(reloaded) == "operator_pause"
    with pytest.raises(Exception, match="resume_parked"):
        backlog.update(item.id, status="pending")


def test_a_parked_row_keeps_its_rule(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    held = _parked(backlog, "daemon_shutdown", node_key="train")
    research = _parked(backlog, "research_open")
    fence = _parked(backlog, "provider_fence")
    question = _parked(backlog, "research_open")
    backlog.update(question.id, pending_question="Which split?")

    # Only parks nobody else releases are the Planner's to retire.
    assert planner_may_retire_park(_row(backlog, research.id))
    assert not planner_may_retire_park(_row(backlog, fence.id))
    assert not planner_may_retire_park(_row(backlog, question.id))
    assert not planner_may_retire_park(_row(backlog, held.id))
    assert count_backlog_statuses(backlog.all())[2] == 4


def test_each_new_fact_resumes_only_the_parks_it_ends(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    for reason in (
        "daemon_shutdown", "provider_cooldown", "provider_fence",
        "operator_pause", "infra", "research_open",
    ):
        _parked(backlog, reason)
    question = _parked(backlog, "operator_question")
    backlog.update(question.id, pending_question="Which venue?")

    # A new pass ends the runtime's own parks, each with a fresh attempt.
    resumed = backlog.resume_parked(RUNTIME_RESUMED_PARKS)
    assert {item.title for item in resumed} == {"daemon_shutdown", "provider_cooldown"}
    assert all(item.status == "pending" and item.attempt == 2 for item in resumed)
    # An explicit Run ends what only the operator or the outside world held.
    resumed = backlog.resume_parked(OPERATOR_RESUMED_PARKS)
    assert {item.title for item in resumed} == {"provider_fence", "operator_pause", "infra"}
    # The Planner's park waits for the Planner or a resume of everything;
    # only its answer ends a question.
    assert [item.title for item in backlog.resume_parked()] == ["research_open"]
    assert _row(backlog, question.id).status == "parked"


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
