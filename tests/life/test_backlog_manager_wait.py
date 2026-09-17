from __future__ import annotations

from pathlib import Path

import pytest

from argus.life.memory import Backlog, BacklogItem


def running(backlog: Backlog) -> BacklogItem:
    item = backlog.add(
        BacklogItem.new(title="current work", objective="Keep the current direction.")
    )
    return backlog.update(
        item.id,
        status="running",
        pending_question="Which authorized source should we use?",
        started_ts=1000.0,
        finished_ts=1001.0,
        notes="Preserve these current notes.",
        last_error="previous result",
        outcome={"current": True},
    )


def test_same_question_parks_current_item_without_overwriting_its_contract(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = running(backlog)
    result = backlog.park_after_manager_wait(
        item.id,
        expected_question=item.pending_question,
        reason="Manager needs this decision.",
        outcome={"waiting": True},
    )
    assert result.status == "paused_operator"
    assert result.pending_question == item.pending_question
    assert result.objective == item.objective
    assert result.notes == item.notes
    assert result.started_ts == item.started_ts
    assert result.finished_ts == item.finished_ts
    assert result.last_error == "Manager needs this decision."
    assert result.outcome == {"waiting": True}
    assert backlog.all()[0].to_jsonable() == result.to_jsonable()


@pytest.mark.parametrize("new_question", ["", "A different pending question?"])
def test_answer_or_changed_question_wins_before_old_wait_settlement(
    tmp_path: Path, new_question: str
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = running(backlog)
    backlog.update(
        item.id, pending_question=new_question, objective="The operator's new direction."
    )
    result = backlog.park_after_manager_wait(
        item.id,
        expected_question=item.pending_question,
        reason="stale reason",
        outcome={"stale": True},
    )
    assert result.status == "pending"
    assert result.pending_question == new_question
    assert result.objective == "The operator's new direction."
    assert result.started_ts is None and result.finished_ts is None
    assert result.last_error == "previous result"
    assert result.outcome == {"current": True}


@pytest.mark.parametrize("same_question", [True, False])
def test_changed_control_or_evidence_cannot_park_even_an_unchanged_question(
    tmp_path: Path, same_question: bool,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = running(backlog)
    result = backlog.park_after_manager_wait(
        item.id,
        expected_question=item.pending_question if same_question else "",
        reason="stale wait",
        outcome={"stale": True},
        wait_current=False,
    )
    assert result.status == "pending"
    assert result.pending_question == item.pending_question
    assert result.started_ts is None and result.finished_ts is None
    assert result.outcome == item.outcome


@pytest.mark.parametrize("status", ["pending", "paused_operator", "done", "aborted", "superseded"])
def test_newer_state_or_archived_terminal_item_is_preserved(tmp_path: Path, status: str) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = running(backlog)
    current = backlog.update(item.id, status=status, outcome={"newer": True})
    before = {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")}
    result = backlog.park_after_manager_wait(
        item.id,
        expected_question=item.pending_question,
        reason="stale",
        outcome={"stale": True},
    )
    assert result.to_jsonable() == current.to_jsonable()
    assert {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")} == before


def test_unknown_item_is_not_created_and_empty_witness_is_rejected(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    assert (
        backlog.park_after_manager_wait("missing", expected_question="Question?", reason="wait")
        is None
    )
    with pytest.raises(ValueError, match="non-empty"):
        backlog.park_after_manager_wait("missing", expected_question="", reason="wait")
