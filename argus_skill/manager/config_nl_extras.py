"""Natural-language recognizers for the SAFE, beginner-obvious operator knobs —
the "green zone" beyond model/effort/backend (which keep their own handlers in
``repl.py``): the two USD budget caps and three on/off toggles.

Kept as a SELF-CONTAINED, PURE, table-driven module ON PURPOSE:

* **review** — the whitelist tables below are the *entire* story of what natural
  language can touch here; a reviewer reads one file.
* **trim** — delete a table row to drop one knob, or delete this whole file plus
  the single ``_maybe_handle_extra_config_text`` dispatch block in ``repl.py`` to
  drop the whole feature. Nothing else in the tree imports it.

``classify_extra_config`` is PURE — no ``os.environ`` write, no printing, no
import of ``repl`` — so it is trivially unit-testable and deletable. Applying the
change (set env + confirm + sync the settings file) is the caller's job.

Recognition is deliberately CONSERVATIVE: a false fire silently consumes the
operator's message, so a budget needs the amount to directly follow a value word
("改成 50" / "to 50" — so "从 30 改成 50" takes 50, and a stray number elsewhere
never counts) and to not be a year/percent/count; a toggle needs an on/off word
plus a specific target phrase (a bare "telegram" would fire on "open telegram to
read messages"); questions and ambiguous compound commands never fire.
"""
from __future__ import annotations

import re
from typing import Sequence

# ── whitelist ───────────────────────────────────────────────────────────────
# On/off toggles. Target phrases are specific on purpose (no bare "telegram").
_TOGGLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARGUS_SKILL_SAFE_MODE", ("安全模式", "safe mode", "safe-mode")),
    ("ARGUS_SKILL_SHOW_REASONING",
     ("推理显示", "显示推理", "思考过程", "show reasoning", "reasoning display")),
    ("ARGUS_SKILL_ENABLE_TELEGRAM",
     ("telegram 通知", "telegram通知", "tg 通知", "电报通知",
      "telegram notification", "telegram notifications", "telegram bridge",
      "telegram bot")),
)
# USD budget caps. Daily aliases matched before the broad "预算"/"budget".
_BUDGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARGUS_SKILL_DAILY_CAP_USD",
     ("每日预算", "每天预算", "日预算", "每天上限", "每日上限", "daily budget", "daily cap")),
    ("ARGUS_SKILL_PER_MISSION_CAP_USD",
     ("单任务预算", "单次预算", "每任务预算", "单个任务预算", "任务预算", "单次上限",
      "每次上限", "per mission", "per-mission", "预算", "budget")),
)

_ON = ("打开", "开启", "启用", "enable", "turn on")
_OFF = ("关闭", "关掉", "停用", "禁用", "disable", "turn off")
_QUESTION_TAIL = ("?", "？", "吗", "呢")
_QUESTION_MID = ("会不会", "是不是", "能不能", "要不要", "好不好", "是否")

# The amount MUST directly follow a value word, so "从 30 改成 50" takes 50 (not
# 30) and a stray number ("给 3 号任务…") is never grabbed.
_AMOUNT_RE = re.compile(
    r"(?:改成|改为|设成|设为|设置成|设置为|调成|调到|调为|=|到|至|为|to)"
    r"\s*[$￥]?\s*(\d+(?:\.\d+)?)"
)
# a number followed by one of these is a year/percent/count, not a dollar cap
_NON_MONEY_UNIT = ("年", "月", "日", "号", "%", "％", "个", "点", "时", "分",
                   "秒", "岁", "次", "轮", "步")


def _has_any(low: str, terms: Sequence[str]) -> bool:
    return any(t in low for t in terms)


def _is_question(low: str) -> bool:
    return low.endswith(_QUESTION_TAIL) or _has_any(low, _QUESTION_MID)


def _classify_toggle(low: str) -> tuple[str, str] | None:
    want_on = _has_any(low, _ON)
    want_off = _has_any(low, _OFF)
    if want_on == want_off:  # neither, or an ambiguous "开…关" — don't guess
        return None
    for env_name, aliases in _TOGGLES:
        if _has_any(low, aliases):
            return env_name, ("1" if want_on else "0")
    return None


def _classify_budget(low: str) -> tuple[str, str] | None:
    m = _AMOUNT_RE.search(low)
    if not m:
        return None
    if low[m.end():].lstrip()[:1] in _NON_MONEY_UNIT:  # e.g. "…改到 2026 年"
        return None
    amount = m.group(1)
    for env_name, aliases in _BUDGETS:
        if _has_any(low, aliases):
            return env_name, amount
    return None


def classify_extra_config(text: str) -> tuple[str, str] | None:
    """Map free text to ``(ARGUS_SKILL_* name, value)`` for a whitelisted safe
    knob, or ``None``. Pure; no side effects. Conservative (see module docstring):
    questions and ambiguous compound commands never fire."""
    low = (text or "").strip().casefold()
    if not low or _is_question(low):
        return None
    toggle = _classify_toggle(low)
    budget = _classify_budget(low)
    if toggle and budget:  # compound / ambiguous — don't guess or half-apply
        return None
    return toggle or budget


def whitelisted_env_names() -> tuple[str, ...]:
    """The env knobs this module lets natural language set — used to mark them in
    the ``/config`` settings view. (model/effort/backend live in their own repl
    handlers and are marked there.)"""
    return tuple(env for env, _ in _TOGGLES) + tuple(env for env, _ in _BUDGETS)
