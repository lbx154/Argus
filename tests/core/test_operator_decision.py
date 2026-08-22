from __future__ import annotations

import pytest

from argus_skill.core.operator_decision import (
    build_operator_decision,
    parse_agent_operator_options,
    selected_decision_text,
)
from argus_skill.life.memory import Backlog, BacklogItem


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
            "requires_note": False,
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


def test_chinese_decision_uses_operator_language_and_human_reason() -> None:
    card = build_operator_decision(
        item_id="i-zh",
        title="验证内核性能",
        reason="row exceeded timeout_s=300",
        question="是否继续使用更小的诊断 shape？",
        options=[{
            "id": "small-shape",
            "label": "先运行单行诊断",
            "description": "使用更小的诊断 shape 后再决定。",
            "requires_note": False,
        }],
    )

    assert [row["label"] for row in card["options"]] == ["先运行单行诊断"]
    assert "300 秒" in card["reason"]
    assert "不代表方案错误" in card["reason"]


def test_option_selection_is_direct_and_custom_requires_text() -> None:
    card = build_operator_decision(
        item_id="i",
        title="t",
        reason="r",
        question="q",
        options=[
            {
                "id": "fallback",
                "label": "Use fallback",
                "description": "Use fallback.",
                "requires_note": False,
            },
            {
                "id": "custom",
                "label": "Describe another route",
                "description": "",
                "requires_note": True,
            },
        ],
    )

    assert selected_decision_text(card, "fallback", "") == "Use fallback."
    assert card["options"][1]["id"] == "option-2"
    with pytest.raises(ValueError, match="requires guidance"):
        selected_decision_text(card, "option-2", "")
    assert selected_decision_text(card, "option-2", "Try B") == "Try B"
    with pytest.raises(ValueError, match="requires an answer"):
        selected_decision_text(card, "custom", "")
    assert selected_decision_text(card, "custom", "Try B") == "Try B"


def test_missing_agent_options_stays_freeform_without_host_choices() -> None:
    card = build_operator_decision(
        item_id="i",
        title="t",
        reason="r",
        question="What should I do?",
    )

    assert card["options"] == []
    assert card["options_source"] == "none"
    with pytest.raises(ValueError, match="requires an answer"):
        selected_decision_text(card, "custom", "")
    assert selected_decision_text(card, "custom", "Wait for access") == "Wait for access"


def test_markdown_wrapped_operator_options_remain_structured() -> None:
    options = parse_agent_operator_options(
        "`OPERATOR_QUESTION=Choose A or B`\n"
        "`OPERATOR_OPTIONS=route-a :: Use A :: Continue with A; "
        "route-b :: Use B :: Continue with B`"
    )

    assert [option["id"] for option in options] == ["route-a", "route-b"]
    assert [option["label"] for option in options] == ["Use A", "Use B"]


def test_legacy_agent_option_can_require_guidance() -> None:
    [option] = parse_agent_operator_options(
        "OPERATOR_OPTIONS=provide-details :: true :: Provide constraints ::"
    )
    card = build_operator_decision(
        item_id="i",
        title="t",
        reason="r",
        question="Which constraints apply?",
        options=[option],
    )

    with pytest.raises(ValueError, match="requires guidance"):
        selected_decision_text(card, "provide-details", "")
    assert selected_decision_text(card, "provide-details", "Keep the API stable") == (
        "Keep the API stable"
    )


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
    assert blocked.operator_decision["resolved_from_revision"] == 1
    assert blocked.operator_decision["continuation_item_id"] == continuation.id
    assert blocked.operator_decision["resume_requested"] is True
    assert blocked.operator_decision["resolution_id"] == "decision-item:r1"
    assert continuation.status == "pending"


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
        status="paused_operator",
        pending_question="choose",
        operator_decision=card,
    )

    blocked, continuation = backlog.continue_with_operator_reply(
        item.id,
        "Use B",
        manager_decision="Use B",
        decision_option="custom",
        decision_id="decision-some-other-item",
        decision_note="Use B",
    )

    assert blocked is not None and continuation is None
    rows = backlog.all()
    assert len(rows) == 1
    assert rows[0].pending_question == "choose"
    assert rows[0].operator_decision["status"] == "pending"


def test_failed_decision_write_leaves_original_card_retriable(tmp_path, monkeypatch) -> None:
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
    original_save = backlog._save

    def fail_write(_items) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(backlog, "_save", fail_write)
    with pytest.raises(OSError, match="injected write failure"):
        backlog.continue_with_operator_reply(
            item.id,
            "Use B",
            manager_decision="Use B",
            decision_option="custom",
            decision_id=card["id"],
            decision_note="Use B",
        )

    persisted = Backlog(backlog.path).all()
    assert len(persisted) == 1
    assert persisted[0].pending_question == "choose"
    assert persisted[0].operator_decision["status"] == "pending"

    monkeypatch.setattr(backlog, "_save", original_save)
    _blocked, continuation = backlog.continue_with_operator_reply(
        item.id,
        "Use B",
        manager_decision="Use B",
        decision_option="custom",
        decision_id=card["id"],
        decision_note="Use B",
    )
    assert continuation is not None
    assert len(backlog.all()) == 2
