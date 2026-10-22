from __future__ import annotations

import json

import pytest

from argus.core.operator_messages import (
    humanize_runtime_reason,
    publish_operator_message,
    render_operator_update,
)
from argus.core.transcript import read_turns


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


def test_brief_title_names_a_task_by_its_opening_clause() -> None:
    from argus.core.operator_messages import brief_title

    assert brief_title("Write the release notes") == "Write the release notes"
    assert brief_title(
        "strtools 的 truncate 收到两条互相冲突的需求，必须由我来拍板选一个，你们不能自己决定："
        "需求 A（文档组）：truncate 截断时不能把单词切成两半。"
    ) == "strtools 的 truncate 收到两条互相冲突的需求"
    assert len(brief_title("x" * 300)) == 50


def test_operator_update_explains_blocker_and_next_action() -> None:
    text = render_operator_update(
        title="run the H100 benchmark",
        status="blocked",
        reason="The H100 runner is unavailable.",
        next_action="Choose whether to wait or use a named H200-only track.",
        holder="operator",
    )

    assert text.splitlines()[0] == "Stuck for now: run the H100 benchmark."
    assert "Reason: The H100 runner is unavailable." in text
    assert "Your call:" in text
    assert text.strip() not in {"BLOCKED", "REVISE"}


@pytest.mark.parametrize(
    ("holder", "expected"),
    [
        # Trial s-6f0e6213: an Engineer's handoff to the Manager read
        # "接下来：我先查清原因，再找个能走通的办法", as if Argus would look into it.
        ("manager", "接下来：经理在定怎么继续。"),
        ("operator", "需要你定一下才能继续。"),
        ("", "接下来：我先查清原因，再找个能走通的办法。"),
    ],
)
def test_a_paused_task_says_who_holds_it_next(holder: str, expected: str) -> None:
    text = render_operator_update(
        title="为 timefmt 添加整小时格式化函数",
        status="parked",
        park_reason="research_open",
        reason="现有舍入规则冲突，需要先定下舍入方式。",
        holder=holder,
    )

    assert text.splitlines()[-1] == expected


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

    assert text.splitlines()[0] == "「验证内核性能」暂时做不下去了。"
    assert "原因：" in text
    assert "接下来：先运行单行诊断。" in text


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
    ("hint", "resume"),
    [
        ("继续论文", "点“运行”就能继续；在那之前不会自动重试"),
        ("Continue the paper", "click Run to continue; nothing retries on its own"),
    ],
)
def test_provider_fence_explains_explicit_recovery(hint: str, resume: str) -> None:
    text = render_operator_update(
        title="Research",
        status="parked",
        park_reason="provider_fence",
        reason="HTTP 402: Insufficient trial tokens for this request.",
        next_action="Check the trial quota.",
        language_hint=hint,
    )

    assert "HTTP 402" in text
    assert "Check the trial quota." in text
    assert resume in text
    assert "Argus 会诊断" not in text
    assert "Argus will diagnose" not in text


@pytest.mark.parametrize(
    ("title", "hint", "expected"),
    [
        ("通信论文", "继续论文", "模型服务连续 3 小时没有恢复"),
        ("Research", "Continue the paper", "3 hours of automatic retries"),
    ],
)
def test_a_spent_retry_window_is_explained_in_the_operator_language(
    title: str, hint: str, expected: str,
) -> None:
    from argus.core.autonomy import RETRY_WINDOW_SPENT_REASON

    text = render_operator_update(
        title=title,
        status="paused_provider_fence",
        reason=f"{RETRY_WINDOW_SPENT_REASON} (HTTP 503 Service Unavailable).",
        language_hint=hint,
    )

    assert expected in text
    assert RETRY_WINDOW_SPENT_REASON not in text


def test_operator_update_leads_with_result() -> None:
    assert render_operator_update(
        title="repair the parser",
        status="done",
        reason="18 focused tests passed.",
    ).splitlines() == [
        "Done: repair the parser.",
        "18 focused tests passed.",
    ]


@pytest.mark.parametrize('reason,expected', [
    ('query: HTTP 402 trial_budget_exhausted 试用额度不足', '试用额度不足'),
    ('Package extraction failed: TAR_ENTRY_ERROR(EFBIG) at sea-loader.js', '执行环境'),
    ('Process exited with code 1 before turn completion.', '模型运行中断'),
])
def test_trial_failures_explain_next_action_without_stack_traces(reason, expected):
    text = humanize_runtime_reason(reason, language_hint='请继续')
    assert expected in text
    assert '管理员' in text
    assert 'sea-loader' not in text and 'HTTP 402' not in text
