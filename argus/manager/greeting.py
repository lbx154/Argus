"""Answer a bare greeting without a model call."""
from __future__ import annotations

import re

_GREETINGS = frozenset({
    "你好", "您好", "你好啊", "你好呀", "您好啊", "哈喽", "嗨", "早", "早上好", "早安",
    "中午好", "下午好", "晚上好", "晚安", "在吗", "在不在",
    "hi", "hello", "hey", "hiya", "yo", "good morning", "good afternoon", "good evening",
})
# Whitespace, punctuation and emoji carry no meaning in a greeting.
_NOISE = re.compile(r"[\s,.!?;:~，。！？；：、～…\-]+|[\U0001F300-\U0001FAFF☀-➿️]")


def greeting_reply(text: str) -> str:
    """One short greeting in the message's language, or "" when it is not a bare greeting."""
    cleaned = _NOISE.sub("", str(text or "")).lower()
    if cleaned not in _GREETINGS:
        return ""
    if any("㐀" <= ch <= "鿿" for ch in cleaned):
        return "你好！我在。你想研究什么问题、处理哪项任务，还是看看哪个项目的进展？"
    return "Hi! I'm here. What would you like to research, which task should I take on, or which project should I show you?"
