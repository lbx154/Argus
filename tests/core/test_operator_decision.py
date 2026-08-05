from __future__ import annotations

import pytest

from argus_skill.core.operator_decision import (
    build_operator_decision,
    selected_decision_text,
)
from argus_skill.life.memory import Backlog, BacklogItem


def test_card_is_readable_and_uses_item_identity() -> None:
    card = build_operator_decision(
        item_id="item-7",
        title="Choose a route",
        reason="The current API is unavailable.",
        question="Use the local fallback?",
        recommendation="Use the local fallback and keep the same acceptance check.",
        evidence=[{"ref": "logs/run.txt", "why": "provider refusal"}],
    )

    assert card["id"] == "decision-item-7"
    assert card["revision"] == 1
    assert [row["id"] for row in card["options"]] == ["recommended", "custom", "stop"]
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
        recommendation="Use fallback.",
    )

    assert selected_decision_text(card, "recommended", "") == "Use fallback."
    with pytest.raises(ValueError, match="requires guidance"):
        selected_decision_text(card, "custom", "")
    assert selected_decision_text(card, "custom", "Try B") == "Try B"


def test_backlog_persists_and_resolves_card_with_continuation(tmp_path) -> None:
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
        status="paused_operator",
        pending_question="choose",
        operator_decision=card,
    )

    blocked, continuation = backlog.continue_with_operator_reply(
        item.id,
        "Use B",
        manager_decision="Use B",
        decision_option="custom",
    )

    assert blocked is not None and continuation is not None
    assert blocked.operator_decision["status"] == "resolved"
    assert blocked.operator_decision["selected_option"] == "custom"
    assert continuation.status == "pending"
