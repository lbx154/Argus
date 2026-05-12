"""Tests for the chat-vs-task classifier.

The classifier is conservative by design: false negatives (a chat
treated as a task) are fine, false positives (a real task treated as
chat) would silently skip the engineer round-loop. Each assertion below
is anchored to either a clear chat opener (must short-circuit) or a
realistic task fragment (must NOT short-circuit).
"""
from __future__ import annotations

import pytest

from argus_skill.life.router import build_chat_prompt, is_conversational

# ---------- positives: clear chat -----------------------------------------

CHAT_MESSAGES_EN = [
    "hello",
    "Hi",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "sure",
    "got it",
    "who are you",
    "what can you do",
    "what are you",
    "are you alive",
    "can you help",
    "do you have memory?",
]

CHAT_MESSAGES_ZH = [
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "早上好",
    "晚安",
    "你是什么",
    "你是谁",
    "你叫什么",
    "你能干嘛",
    "你能做什么",
    "你有什么能力",
    "你有什么本事",
    "你会什么",
    "你的名字是什么",
    "你的能力",
    "你支持中文吗",
    "你具有什么能力",
    "你是否具有24小时运行的能力",
    "你觉得呢",
    "你认为对吗",
    "介绍一下你自己",
    "自我介绍一下",
    "谢谢",
    "多谢",
    "辛苦了",
    "好的",
    "好",
    "行",
    "知道了",
    "明白",
    "收到",
    "嗯嗯",
]


@pytest.mark.parametrize("msg", CHAT_MESSAGES_EN + CHAT_MESSAGES_ZH)
def test_chat_messages_classified_as_conversational(msg: str) -> None:
    assert is_conversational(msg), f"expected chat: {msg!r}"


# ---------- negatives: clear engineering tasks ---------------------------

TASK_MESSAGES = [
    # English imperatives.
    "implement a binary tree",
    "build a Python package called foo",
    "fix the bug in src/app.py",
    "refactor the auth module",
    "run the tests in tests/test_user.py",
    "debug the failing CI step",
    "write a CLI for parsing JSON",
    "add a new endpoint",
    "remove unused imports",
    "update the dependency list",
    "deploy the staging env",
    "install pytest-cov and configure it",
    "migrate the database schema",
    "optimize the inner loop",
    "review the PR diff",
    "compile the C extension",
    "train a small classifier on mnist",
    "investigate why the daemon hangs",
    # Chinese imperatives.
    "写一个 Python 包",
    "实现一个二叉树",
    "改一下 src/loop.py 的逻辑",
    "修复测试里的 bug",
    "增加一个新的命令",
    "重构 auth 模块",
    "跑一下测试看看",
    "帮我做一个 CLI",
    "把这个函数提取到工具模块",
    "清理 dead code",
    "重启 daemon",
    "训练一个小模型",
    "做个消融实验",
    # File / code references — never chat even if no verb.
    "src/main.py",
    "tests/test_user.py 在哪",
    "this codebase has a bug",
    "in the repo, where is X",
    "what does the auth module do",
    "改 argus_skill/loop.py",
    "看一下 argus_skill 模块",
]


@pytest.mark.parametrize("msg", TASK_MESSAGES)
def test_task_messages_not_classified_as_conversational(msg: str) -> None:
    assert not is_conversational(msg), f"expected task: {msg!r}"


# ---------- edge cases ----------------------------------------------------

def test_empty_string_is_not_chat() -> None:
    assert is_conversational("") is False
    assert is_conversational("   ") is False


def test_multiline_message_is_not_chat() -> None:
    # Multi-line messages always carry intent that warrants the
    # full pipeline — even if the first line looks like a greeting.
    msg = "hello\nplease implement a feature"
    assert is_conversational(msg) is False


def test_long_message_is_not_chat_even_if_friendly() -> None:
    msg = (
        "你好啊，能不能麻烦你帮我看一下 src 目录里 loop.py 这个文件"
        "是不是有什么问题"
    )
    assert is_conversational(msg) is False


def test_short_unknown_token_is_chat() -> None:
    # Conservative fallback: a < 6-char token with no verb/path is
    # almost certainly a chat ack ("OK!", "👍", "嗯").
    assert is_conversational("👍") is True
    assert is_conversational("嗯") is True


# ---------- prompt builder ------------------------------------------------

def test_build_chat_prompt_contains_user_message() -> None:
    out = build_chat_prompt(objective="你好")
    assert "你好" in out
    assert "CHAT mode" in out
    # Must NOT carry the engineer's Verification template.
    assert "Verification" not in out or "OFF" in out
    assert "## Required output" not in out


def test_build_chat_prompt_includes_identity_when_given() -> None:
    out = build_chat_prompt(objective="who are you", identity_card="I am argus.")
    assert "I am argus." in out
    assert "who are you" in out


def test_build_chat_prompt_omits_identity_when_blank() -> None:
    out = build_chat_prompt(objective="hi", identity_card="   ")
    assert "Identity context" not in out
