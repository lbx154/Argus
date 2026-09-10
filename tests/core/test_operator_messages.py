from __future__ import annotations

import json

import pytest

from argus_skill.core.operator_messages import (
    budget_refusal_reply,
    humanize_runtime_reason,
    publish_operator_message,
    render_operator_update,
)
from argus_skill.core.transcript import read_turns


def test_budget_reply_explains_known_limit_without_claiming_backend_failure() -> None:
    reason = "refused before start: global daily budget exhausted ($0.000000 available)"
    reply = budget_refusal_reply(reason, language_hint="继续推进任务")
    assert reply is not None
    assert "已达到全局日预算上限" in reply
    assert reason in reply
    assert "backend is unavailable" not in reply
    assert "argus doctor --deep" not in reply


def test_budget_refusal_reply_distinguishes_exhaustion_and_accounting_failure() -> None:
    exhausted = budget_refusal_reply("refused before start: global daily budget exhausted")
    assert exhausted is not None and "global daily budget" in exhausted
    unavailable = budget_refusal_reply("cost control unavailable: invalid state")
    assert unavailable is not None and "accounting" in unavailable
    assert budget_refusal_reply("authentication failed") is None


def test_publish_operator_message_is_idempotent_across_transcript_and_event(tmp_path) -> None:
    assert publish_operator_message(
        tmp_path,
        text="Team completed.",
        message_id="team-summary-1",
    )
    assert not publish_operator_message(
        tmp_path,
        text="Team completed.",
        message_id="team-summary-1",
    )

    assert [turn["text"] for turn in read_turns(tmp_path)] == ["Team completed."]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["message_id"] for event in events if event["type"] == "ui.argus"] == [
        "team-summary-1",
    ]


def test_operator_update_explains_blocker_and_next_action() -> None:
    text = render_operator_update(
        title="run the H100 benchmark",
        status="blocked",
        reason="The H100 runner is unavailable.",
        next_action="Choose whether to wait or use a named H200-only track.",
        user_action_required=True,
    )

    assert text.splitlines()[0] == "Cannot continue yet: run the H100 benchmark."
    assert "Reason: The H100 runner is unavailable." in text
    assert "Your decision:" in text
    assert text.strip() not in {"BLOCKED", "REVISE"}


def test_runtime_timeout_is_explained_without_claiming_the_idea_failed() -> None:
    text = humanize_runtime_reason(
        "row exceeded timeout_s=300",
        language_hint="优化内核",
    )

    assert "300 秒" in text
    assert "不代表方案错误" in text
    assert "最小诊断" in text


def test_stale_decision_and_workspace_lock_are_humanized() -> None:
    assert "latest state" in humanize_runtime_reason(
        "stale decision: campaign changed since the question was created"
    )
    assert "starting a duplicate" in humanize_runtime_reason(
        "executor failed to start: workdir /tmp/p is already owned by active session s-1"
    )


def test_operator_update_follows_chinese_project_language() -> None:
    text = render_operator_update(
        title="验证内核性能",
        status="blocked",
        reason="row exceeded timeout_s=300",
        next_action="先运行单行诊断。",
    )

    assert text.splitlines()[0] == "暂时无法继续：验证内核性能。"
    assert "原因：" in text
    assert "下一步：先运行单行诊断。" in text


def test_operator_abort_is_not_rendered_as_a_failure_or_retry() -> None:
    text = render_operator_update(
        title="Analyze app.py",
        status="aborted",
        reason="The operator requested this mission be aborted.",
        language_hint="用户要求取消任务。",
    )

    assert text.startswith("已取消：Analyze app.py。")
    assert "未能完成" not in text
    assert "原因" not in text
    assert "下一步" not in text


@pytest.mark.parametrize(
    ("title", "hint", "waiting", "continuation"),
    [
        ("通信论文", "继续修订论文", "正在等待后台任务完成", "自动继续"),
        ("communication paper", "Continue revising", "Waiting for background work", "automatically"),
    ],
)
def test_healthy_background_wait_does_not_request_failure_recovery(
    title: str, hint: str, waiting: str, continuation: str,
) -> None:
    text = render_operator_update(
        title=title,
        status="paused_external_work",
        reason="healthy subagent job-1 is still running; released the mission slot",
        language_hint=hint,
    )

    assert title in text
    assert waiting in text
    assert continuation in text
    assert "未能完成" not in text
    assert "Could not complete" not in text
    assert "diagnose" not in text
    assert "job-1" not in text
    assert "mission slot" not in text


def test_operator_update_leads_with_result() -> None:
    assert render_operator_update(
        title="repair the parser",
        status="done",
        reason="18 focused tests passed.",
    ).splitlines() == [
        "Completed: repair the parser.",
        "Reason: 18 focused tests passed.",
    ]
