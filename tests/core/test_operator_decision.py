from __future__ import annotations

import pytest

from argus.core.operator_decision import build_operator_decision, selected_decision_text
from argus.life.memory import Backlog, BacklogItem


def test_new_question_records_asked_at_without_prior_card(monkeypatch) -> None:
    monkeypatch.setattr("argus.core.operator_decision.time.time", lambda: 200.0)

    card = build_operator_decision(
        item_id="item", title="Choose", reason="Blocked", question="Use A?",
    )

    assert card["asked_at"] == 200.0


@pytest.mark.parametrize("recorded_time", [100.0, None])
def test_rebuilding_same_pending_question_preserves_recorded_time(
    monkeypatch, recorded_time,
) -> None:
    previous = {
        "item_id": "item", "status": "pending", "question": "Use A?",
    }
    if recorded_time is not None:
        previous["asked_at"] = recorded_time
    monkeypatch.setattr("argus.core.operator_decision.time.time", lambda: 200.0)

    card = build_operator_decision(
        item_id="item", title="Updated title", reason="Updated reason",
        question=" Use A? ", previous_decision=previous,
    )

    assert card.get("asked_at") == recorded_time
    assert ("asked_at" in card) == (recorded_time is not None)


@pytest.mark.parametrize("changed", [
    {"question": "Use B?"},
    {"status": "resolved"},
    {"item_id": "other-item"},
])
def test_new_question_does_not_inherit_another_questions_time(monkeypatch, changed) -> None:
    previous = {
        "item_id": "item", "status": "pending", "question": "Use A?",
        "asked_at": 100.0, **changed,
    }
    monkeypatch.setattr("argus.core.operator_decision.time.time", lambda: 200.0)

    card = build_operator_decision(
        item_id="item", title="Choose", reason="Blocked", question="Use A?",
        previous_decision=previous,
    )

    assert card["asked_at"] == 200.0


def test_card_is_readable_and_uses_item_identity() -> None:
    card = build_operator_decision(
        item_id="item-7",
        title="Choose a route",
        reason="The current API is unavailable.",
        question="Use the local fallback?",
        options=[{
            "id": "local-fallback",
            "label": "Use the local fallback",
            "description": "Keep the same acceptance check.",
        }],
        evidence=[{"ref": "logs/run.txt", "why": "provider refusal"}],
        project_id="s-project",
    )

    assert card["id"] == "decision-item-7"
    assert card["revision"] == 1
    assert card["project_id"] == "s-project"
    assert "campaign_generation" not in card
    assert card["options_source"] == "agent"
    assert [row["id"] for row in card["options"]] == ["local-fallback"]
    assert card["evidence"] == [{
        "label": "provider refusal",
        "path": "logs/run.txt",
        "summary": "provider refusal",
    }]


def test_option_selection_is_direct_and_custom_requires_text() -> None:
    card = build_operator_decision(
        item_id="i",
        title="t",
        reason="r",
        question="q",
        options=[
            {"id": "fallback", "label": "Use fallback", "description": "Use fallback."},
            {"id": "custom", "label": "Describe another route"},
        ],
    )

    assert selected_decision_text(card, "fallback", "") == "Use fallback."
    # "custom" is the written answer, so an option that claims it is renamed.
    assert card["options"][1]["id"] == "option-2"
    assert selected_decision_text(card, "option-2", "") == "Describe another route"
    assert selected_decision_text(card, "option-2", "Today") == "Describe another route\n\nNote: Today"
    with pytest.raises(ValueError, match="requires an answer"):
        selected_decision_text(card, "custom", "")
    assert selected_decision_text(card, "custom", "Try B") == "Try B"


def test_backlog_resolves_card_and_resumes_the_task_it_parked(tmp_path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = backlog.add(BacklogItem.new(title="blocked", objective="work", item_id="item"))
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="blocked",
        question="choose",
    )
    backlog.update(
        item.id,
        park_reason="operator_pause",
        pending_question="choose",
        operator_decision=card,
    )

    resumed, answered = backlog.resume_with_operator_reply(
        item.id,
        "Use B",
        manager_decision="Use B",
        decision_option="custom",
    )

    assert resumed is not None and answered
    assert resumed.operator_decision["status"] == "resolved"
    assert resumed.operator_decision["selected_option"] == "custom"
    assert resumed.operator_decision["resolved_from_revision"] == 1
    assert resumed.operator_decision["resume_requested"] is True
    assert resumed.operator_decision["resolution_id"] == "decision-item:r1"
    # The task it parked resumes, not failed and not replaced.
    assert [(row.id, row.status) for row in backlog.all()] == [(item.id, "pending")]
    assert resumed.pending_question == ""
    assert resumed.attempt == 2
    assert "Use B" in resumed.objective


def test_decision_identity_mismatch_makes_no_backlog_change(tmp_path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = backlog.add(BacklogItem.new(title="blocked", objective="work", item_id="item"))
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="blocked",
        question="choose",
    )
    backlog.update(
        item.id,
        park_reason="operator_pause",
        pending_question="choose",
        operator_decision=card,
    )

    unchanged, answered = backlog.resume_with_operator_reply(
        item.id,
        "Use B",
        manager_decision="Use B",
        decision_option="custom",
        decision_id="decision-some-other-item",
        decision_note="Use B",
    )

    assert unchanged is not None and not answered
    rows = backlog.all()
    assert len(rows) == 1
    assert rows[0].pending_question == "choose"
    assert rows[0].operator_decision["status"] == "pending"
