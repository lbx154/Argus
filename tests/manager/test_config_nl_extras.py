"""The natural-language whitelist for the safe "green zone" knobs (budget caps +
on/off toggles). Pure classifier — no env, no I/O — so it's exhaustively unit
testable and deletable in one file."""
from __future__ import annotations

import pytest

from argus_skill.manager.config_nl_extras import classify_extra_config


@pytest.mark.parametrize(
    "text, expected",
    [
        # toggles
        ("打开安全模式", ("ARGUS_SKILL_SAFE_MODE", "1")),
        ("把安全模式关掉", ("ARGUS_SKILL_SAFE_MODE", "0")),
        ("开启推理显示", ("ARGUS_SKILL_SHOW_REASONING", "1")),
        ("把思考过程关闭", ("ARGUS_SKILL_SHOW_REASONING", "0")),
        ("打开 telegram 通知", ("ARGUS_SKILL_ENABLE_TELEGRAM", "1")),
        ("关闭电报通知", ("ARGUS_SKILL_ENABLE_TELEGRAM", "0")),
        ("enable safe mode", ("ARGUS_SKILL_SAFE_MODE", "1")),
        # budgets (daily aliases win over the broad 预算/budget)
        ("把单任务预算改成 50", ("ARGUS_SKILL_PER_MISSION_CAP_USD", "50")),
        ("把预算改成 20 美元", ("ARGUS_SKILL_PER_MISSION_CAP_USD", "20")),
        ("每日预算设为 100", ("ARGUS_SKILL_DAILY_CAP_USD", "100")),
        ("把每天上限调成 200.5", ("ARGUS_SKILL_DAILY_CAP_USD", "200.5")),
        # the amount follows the value word, so this takes 50 (not the old 30)
        ("把预算从 30 改成 50", ("ARGUS_SKILL_PER_MISSION_CAP_USD", "50")),
        ("set per mission budget to 50", ("ARGUS_SKILL_PER_MISSION_CAP_USD", "50")),
    ],
)
def test_classify_hits(text: str, expected: tuple[str, str]) -> None:
    assert classify_extra_config(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "安全模式是打开的吗？",        # question, not a command
        "推理显示现在是开还是关",       # question-ish, no clear verb+target on/off
        "帮我打开这个文件",           # "打开" but no whitelisted target
        "开始跑 kernelbench",        # "开" substring, no target
        "这个任务的预算大概多少",       # 预算 but no config verb / number
        "关于预算我们聊聊",           # 预算 but no verb + number
        "把模型换成 gpt-5.5",        # model switch — handled by its own handler
        "把 argus 后端换成 claude",  # backend switch — its own handler
        # regression: substring / first-number / question / compound false-fires
        "把任务限制在20个以内,预算别管",   # "限" is not a verb; number is a count
        "把 telegram 打开看看新消息",     # bare "telegram" no longer an alias
        "开启 telegram 通知会不会很吵",   # mid-sentence question (会不会)
        "预算改到 2026 年再说",          # number is a year (年 unit)
        "预算控制在总额 20% 以内",        # number is a percent; no value word
        "let me set up a budget review at 3",  # "set up", number not after a value word
        "给 3 号任务把预算算一下",         # "3" is an index, no amount value word
        "启用安全模式并关闭 telegram 通知",  # both on and off — ambiguous
        "打开安全模式并把预算改成 50",      # compound toggle+budget — don't half-apply
        "",
    ],
)
def test_classify_does_not_misfire(text: str) -> None:
    assert classify_extra_config(text) is None
