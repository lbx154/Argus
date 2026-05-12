"""Pre-mission classifier: separate conversational chat from real tasks.

Background. The original REPL piped every operator message through the
full mission pipeline:

    matcher → distill (on miss) → engineer round-loop → reviewer →
    skill writeback → critic iteration

That's correct for "build a Python package with strict gates", but it
is a $0.10 + 30-second misfire for "hello" or "你能干什么". In one
trace the engineer ran ``pwd && ls && rg --files && sed README.md``
just to answer a greeting, then the reviewer rejected it for "doing
unrelated repo inspection" and forced a redo round.

This module is a conservative classifier: false negatives (treating
chat as a task) are fine — that's existing behavior. False positives
(treating a real task as chat) skip the round-loop and would lose
work, so the rules below intentionally bail out on any signal of
"actual engineering" (file paths, code verbs, long messages).

Public surface:

* ``is_conversational(text)`` — bool, the classifier itself.
* ``build_chat_prompt(...)`` — render the codex system+user prompt for
  the chat fast-path (no Verification block, no tool use).
"""
from __future__ import annotations

import re

# Hard upper bound. Real tasks almost always carry context/spec that
# blows past this. Greetings and capability questions stay below.
_CHAT_MAX_CHARS = 60

# Imperative coding verbs — if the user uses any of these we hand off
# to the full mission pipeline, no exceptions.
_CODE_VERB_EN = re.compile(
    r"\b(?:implement|build|create|fix|refactor|run|test|debug|write|add|"
    r"remove|update|patch|deploy|install|migrate|optimize|profile|"
    r"benchmark|review|analyze|generate|integrate|merge|"
    r"compile|train|investigate|scaffold|setup|set\s+up|configure|"
    r"port|rewrite|extract|convert|parse|render|serialize)\b",
    re.IGNORECASE,
)
_CODE_VERB_ZH = re.compile(
    r"(?:写一个|写个|写一下|实现|构建|创建|修复|改一下|改一改|改成|"
    r"调试|增加|删除|更新|部署|安装|迁移|优化|清理|"
    r"重构|生成|分析|检查|审查|集成|合并|训练|"
    r"重启|跑一下|跑个|跑下|测一下|测试|帮我做|帮我写|"
    r"做一个|做个|帮忙|帮我|完成|继续|接着|执行|"
    r"提取|转换|解析|渲染|配置|搭建|开发|编译|编写|"
    r"打开|关闭(?!了))"
)

# File / repo references — definitely a task, never chat.
_FILE_REF = re.compile(
    r"(?:^|\s|[`'\"])(?:/|\.{1,2}/|src/|tests?/|"
    r"\.\w{1,5}\b|"
    r"\b(?:repo|module|codebase|project|package|library|service|app|"
    r"function|class|method|variable|api|endpoint|database|table)\b)",
    re.IGNORECASE,
)
_FILE_REF_ZH = re.compile(
    r"(代码|项目|工作区|目录|文件|函数|类|方法|变量|接口|数据库|表|模块|包|库)"
)

# Explicit chat starts. Each pattern matches the message head only;
# the leading character class strips lightweight punctuation.
_LEAD = r"^[\s\.\?!,。？！，~～]*"
_CHAT_PATTERNS = [re.compile(_LEAD + p, re.IGNORECASE) for p in (
    r"hi\b",
    r"hello\b",
    r"hey\b",
    r"yo\b",
    r"thanks?\b",
    r"thank\s+you\b",
    r"thx\b",
    r"ok\b",
    r"okay\b",
    r"sure\b",
    r"cool\b",
    r"nice\b",
    r"got\s+it\b",
    r"who\s+are\s+you\b",
    r"what\s+can\s+you\b",
    r"what\s+are\s+you\b",
    r"are\s+you\b",
    r"can\s+you\b",
    r"do\s+you\b",
)]
_CHAT_PATTERNS_ZH = [re.compile(_LEAD + p) for p in (
    r"你好",
    r"您好",
    r"嗨",
    r"哈喽",
    r"哈罗",
    r"早安",
    r"早上好",
    r"中午好",
    r"下午好",
    r"晚上好",
    r"晚安",
    r"你是(?!不是要|否要)",
    r"你叫(?!我)",
    r"你能(?!不能|否)",
    r"你有(?:什么|哪些|啥)",
    r"你会(?:什么|哪些|啥|不会)",
    r"你的(?:名字|身份|能力|功能|特长|长处|定位|作用|用途)",
    r"你支持",
    r"你支不支持",
    r"你具(?:有|备)",
    r"你是否(?:具|有|能|能够|可以)",
    r"你觉得",
    r"你认为",
    r"你想",
    r"介绍(?:一下|下)?(?:你|一下你)",
    r"自我介绍",
    r"谢谢",
    r"多谢",
    r"辛苦了?",
    r"好(?:的|啊|呀|嘞)?[\s。.!?]*$",
    r"行(?:啊|呀)?[\s。.!?]*$",
    r"知道了?[\s。.!?]*$",
    r"明白了?[\s。.!?]*$",
    r"收到[\s。.!?]*$",
    r"嗯+[\s。.!?]*$",
    r"啊+[\s。.!?]*$",
)]

_ALL_CHAT_PATTERNS = _CHAT_PATTERNS + _CHAT_PATTERNS_ZH


def is_conversational(text: str) -> bool:
    """Return True iff ``text`` should bypass the full mission pipeline.

    Conservative — any whiff of a real task (file paths, code verbs,
    long messages, multi-line) returns False so the operator's
    engineering work is never accidentally short-circuited.
    """
    text = (text or "").strip()
    if not text:
        return False
    if "\n" in text:
        return False
    if len(text) > _CHAT_MAX_CHARS:
        return False
    if _FILE_REF.search(text):
        return False
    if _FILE_REF_ZH.search(text):
        return False
    if _CODE_VERB_EN.search(text):
        return False
    if _CODE_VERB_ZH.search(text):
        return False
    for pat in _ALL_CHAT_PATTERNS:
        if pat.search(text):
            return True
    # Fallback: very short single-utterance ack ("ok", "好", "👍").
    # Limited to <= 6 chars and <= 2 tokens to avoid catching task
    # fragments like "fix bug" (which the verb regex also catches).
    if len(text) <= 6 and len(text.split()) <= 2:
        return True
    return False


_CHAT_SYSTEM_INSTRUCTIONS = (
    "## You are in CHAT mode\n"
    "The operator sent a brief conversational message — a greeting, "
    "capability question, or short ack. Reply directly in 1-3 "
    "sentences in the same language they used. Match their register: "
    "concise, plain prose, no boilerplate.\n\n"
    "Hard rules:\n"
    "1. Do NOT inspect the workspace, list files, or run any shell "
    "command. Do NOT invoke any tool.\n"
    "2. Do NOT add `## Verification`, `## Summary`, or any structured "
    "section. The reviewer is OFF.\n"
    "3. Reply with prose only. No code fences, no markdown headings, "
    "no bullet lists unless the user explicitly asked for a list.\n"
    "4. If the user asks about your capabilities, say what argus-skill "
    "does in plain terms (supervises a coding agent end-to-end with a "
    "skill cache, runs missions on a 7×24 daemon, etc.) — keep it "
    "short.\n"
)


def build_chat_prompt(*, objective: str, identity_card: str = "") -> str:
    """Render the full prompt sent to codex on the chat fast-path."""
    sections: list[str] = []
    if identity_card.strip():
        sections.append("## Identity context\n" + identity_card.strip())
    sections.append(_CHAT_SYSTEM_INSTRUCTIONS)
    sections.append("## User message\n" + objective.strip())
    return "\n\n".join(sections)


__all__ = [
    "is_conversational",
    "build_chat_prompt",
]
