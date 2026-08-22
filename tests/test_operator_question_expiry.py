"""A pending operator card must not outlive the mission that raised it.

Both resolvers -- ``continue_with_operator_reply`` and
``stop_for_operator_decision`` -- refuse an item whose ``pending_question`` is
empty. So a card left ``pending`` after its item ended is unanswerable, and the
cockpit keeps offering it. One sat that way on a failed mission for a day.
"""
from __future__ import annotations

from argus_skill.life.memory import Backlog, BacklogItem


def _paused_with_card(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = BacklogItem.new(title="t", objective="o")
    backlog.add(item)
    backlog.update(
        item.id,
        status="paused_operator",
        pending_question="May I create ./.venv?",
        operator_decision={"id": "d1", "status": "pending", "revision": 1},
    )
    return backlog, item.id


def test_ending_a_mission_expires_the_question_it_could_not_get_answered(tmp_path):
    backlog, item_id = _paused_with_card(tmp_path)

    backlog.mark_failed(item_id, error="gave up")

    ended = next(row for row in backlog.all() if row.id == item_id)
    assert ended.pending_question == ""
    assert ended.operator_decision["status"] == "expired"
    # Expiry is a revision like any other, so a stale answer cannot race it.
    assert ended.operator_decision["revision"] == 2
    assert ended.operator_decision["resolved_from_revision"] == 1


def test_a_live_question_survives_every_non_terminal_update(tmp_path):
    backlog, item_id = _paused_with_card(tmp_path)

    backlog.update(item_id, last_error="still waiting")

    waiting = next(row for row in backlog.all() if row.id == item_id)
    assert waiting.pending_question == "May I create ./.venv?"
    assert waiting.operator_decision["status"] == "pending"


def test_answering_still_resolves_rather_than_expires(tmp_path):
    backlog, item_id = _paused_with_card(tmp_path)

    blocked, continuation = backlog.continue_with_operator_reply(item_id, "yes")

    assert continuation is not None
    assert blocked.operator_decision["status"] == "resolved"
