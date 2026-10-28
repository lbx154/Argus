from __future__ import annotations

import pytest

from argus.core.operator_messages import (
    first_sentence,
    humanize_runtime_reason,
    render_operator_update,
)


def test_brief_title_names_a_task_by_its_opening_clause() -> None:
    from argus.core.operator_messages import brief_title

    assert brief_title("Write the release notes") == "Write the release notes"
    assert brief_title(
        "strtools 的 truncate 收到两条互相冲突的需求，必须由我来拍板选一个，你们不能自己决定："
        "需求 A（文档组）：truncate 截断时不能把单词切成两半。"
    ) == "strtools 的 truncate 收到两条互相冲突的需求"
    assert len(brief_title("x" * 300)) == 50


@pytest.mark.parametrize(
    ("holder", "expected"),
    [
        # Trial s-6f0e6213: an Engineer's handoff to the Manager read
        # "接下来：我先查清原因，再找个能走通的办法", as if Argus would look into it.
        ("manager", "接下来：经理在定怎么继续。"),
        ("planner", "接下来：规划者在重新安排。"),
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


_REVIEW = (
    "format_hours 尚未交付；现有整单位函数的舍入规则不一致，需要先明确小时函数应遵循哪一种规则。\n\n"
    "独立运行 `python -m pytest -q` 得到 6 passed，但现有测试完全没有覆盖小时函数。\n\n"
    "请明确小时函数采用舍弃小数部分还是半数进一，再交由实现方补齐。"
)


@pytest.mark.parametrize(
    ("holder", "next_line"),
    [
        # Trial s-757b9dac: the Reviewer's `→ manager` gave its whole review as
        # the reason and again as what comes next.
        ("manager", "接下来：经理在定怎么继续。"),
        # A next action that only repeats the reason is no next action.
        ("", "接下来：我先查清原因，再找个能走通的办法。"),
    ],
)
def test_a_handed_on_review_says_its_first_sentence_once(holder: str, next_line: str) -> None:
    text = render_operator_update(
        title="为 timefmt 添加整小时格式化函数",
        status="parked",
        park_reason="research_open",
        reason=_REVIEW,
        next_action=_REVIEW,
        holder=holder,
    )

    assert text.splitlines() == [
        "「为 timefmt 添加整小时格式化函数」暂时做不下去了。",
        "原因：format_hours 尚未交付；现有整单位函数的舍入规则不一致，需要先明确小时函数应遵循哪一种规则。",
        next_line,
    ]


def test_a_reason_in_markdown_gives_its_first_sentence_as_plain_text() -> None:
    # Trial s-f0e1b2f0: the sentence was cut at its "。", inside the bold, and
    # the notice read "原因：**尚未实现：取整规则存在歧义。".
    text = render_operator_update(
        title="为 sizefmt 添加整数 MiB 格式化函数",
        status="blocked",
        reason="**尚未实现：取整规则存在歧义。** `sizefmt/core.py` 中，`format_kib` 舍弃小数。",
        holder="manager",
    )

    assert text.splitlines()[1] == "原因：尚未实现：取整规则存在歧义。"
    # A name is not markup: the marks go, the dunder stays.
    assert first_sentence("从 `sizefmt/__init__.py` 导出`format_mib`。其余未改。") == "从 sizefmt/__init__.py 导出format_mib。"


def test_stale_decision_and_workspace_lock_are_humanized() -> None:
    assert "latest state" in humanize_runtime_reason(
        "stale decision: campaign changed since the question was created"
    )
    assert "reuse or wait for it" in humanize_runtime_reason(
        "executor failed to start: workdir /tmp/p is already owned by active session s-1"
    )


@pytest.mark.parametrize(
    ("hint", "resume"),
    [
        ("继续论文", "点「继续运行」就能接着做；在那之前不会自动重试"),
        ("Continue the paper", "click Resume to continue; nothing retries on its own"),
    ],
)
def test_provider_fence_explains_explicit_recovery(hint: str, resume: str) -> None:
    text = render_operator_update(
        title="Research",
        status="parked",
        park_reason="provider_fence",
        reason="HTTP 402: Payment Required",
        next_action="Check the provider's billing.",
        language_hint=hint,
    )

    assert "HTTP 402" in text
    assert "Check the provider's billing." in text
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


@pytest.mark.parametrize('reason,expected', [
    ('Package extraction failed: TAR_ENTRY_ERROR(EFBIG) at sea-loader.js', '执行环境'),
    ('Process exited with code 1 before turn completion.', '模型运行中断'),
])
def test_trial_failures_explain_next_action_without_stack_traces(reason, expected):
    text = humanize_runtime_reason(reason, language_hint='请继续')
    assert expected in text
    assert '管理员' in text
    assert 'sea-loader' not in text and 'HTTP 402' not in text


@pytest.mark.parametrize(
    ("hint", "said", "resume"),
    [
        ("继续论文", "试用额度已用完，任务已暂停，文件都保留着。", "额度补上后点「继续运行」就能接着做"),
        ("Continue the paper", "The trial allowance is used up, so the task is paused; all files are kept.",
         "Once the allowance is topped up, click Resume to continue"),
    ],
)
def test_a_spent_trial_allowance_is_said_plainly(hint: str, said: str, resume: str) -> None:
    """2026-10-28 trial: a 402 from the trial meter read as the model service
    restricting calls, sent the person to an administrator, and named a Run
    button the page did not show."""
    for reason in (
        "审阅者的会话在得出结论前就结束了。 技术记录：exit=1; fatal_error=query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。",
        "HTTP 402: Insufficient trial tokens for this request.",
        'HTTP 402 {"error": {"code": "trial_budget_exhausted"}}',
    ):
        assert humanize_runtime_reason(reason, language_hint=hint) == said
        text = render_operator_update(
            title="Research", status="parked", park_reason="provider_fence", reason=reason, language_hint=hint,
        )
        assert text.splitlines() == [said, text.splitlines()[1]]
        assert resume in text
        for absent in ("管理员", "administrator", "模型服务", "model service", "技术记录", "HTTP 402"):
            assert absent not in text
