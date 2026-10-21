"""Settings an operator may choose before a project's first task.

They travel with the request as plain operator text, so the Manager reads them
through the same channel as the request itself; nothing else interprets them.
"""
from __future__ import annotations

from typing import Any

from .mission_view import uses_cjk

TARGET_LEVELS = ("auto", "exploratory", "publishable")
DELIVERABLES = ("any", "report", "code", "paper", "data")

_DELIVERABLE_TEXT = {
    "report": ("一份书面报告（结论、证据和局限）", "a written report (conclusions, evidence, limitations)"),
    "code": ("可以运行的代码和使用说明", "runnable code with usage notes"),
    "paper": ("论文稿", "a paper draft"),
    "data": ("数据或结果表格", "data or result tables"),
}


def normalize_launch_settings(raw: Any) -> dict[str, Any] | None:
    """Return validated launch settings, or ``None`` when nothing was chosen."""
    if not isinstance(raw, dict):
        return None
    hours_raw = raw.get("first_result_hours")
    hours: float | None = None
    if hours_raw not in (None, "", 0):
        try:
            hours = float(hours_raw)
        except (TypeError, ValueError):
            hours = None
        if hours is not None and not 0.5 <= hours <= 24.0 * 30:
            hours = None
    level = str(raw.get("target_level") or "auto").strip().lower()
    if level not in TARGET_LEVELS:
        level = "auto"
    deliverable = str(raw.get("deliverable") or "any").strip().lower()
    if deliverable not in DELIVERABLES:
        deliverable = "any"
    hold = bool(raw.get("hold"))
    if hours is None and level == "auto" and deliverable == "any" and not hold:
        return None
    return {
        "first_result_hours": hours,
        "target_level": level,
        "deliverable": deliverable,
        "hold": hold,
    }


def _hours_text(hours: float, *, chinese: bool) -> str:
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} 天" if chinese else (f"{days} day" if days == 1 else f"{days} days")
    whole = int(hours) if float(hours).is_integer() else hours
    return f"{whole} 小时" if chinese else f"{whole} hours"


def launch_note(settings: dict[str, Any] | None, *, language_hint: str = "") -> str:
    """The chosen settings as operator text appended to the request body."""
    if not settings:
        return ""
    chinese = uses_cjk(language_hint) if language_hint else True
    lines: list[str] = []
    hours = settings.get("first_result_hours")
    if hours:
        span = _hours_text(float(hours), chinese=chinese)
        lines.append(
            f"第一个可以检查的成果要在开始后 {span}内交付（可以是阶段成果），之后再继续深入。"
            if chinese else
            f"Deliver the first result I can check within {span} of starting "
            "(an interim result is fine), then continue."
        )
    level = settings.get("target_level")
    if level == "exploratory":
        lines.append(
            "研究定位：探索性。先做有限、可验证的调查，不以可发表的原创成果为目标。"
            if chinese else
            "Research ambition: exploratory. A finite, verifiable investigation; "
            "publication-level original work is not the goal."
        )
    elif level == "publishable":
        lines.append(
            "研究定位：以可发表的原创成果为目标。"
            if chinese else
            "Research ambition: publication-level original work."
        )
    deliverable = settings.get("deliverable")
    if deliverable in _DELIVERABLE_TEXT:
        zh, en = _DELIVERABLE_TEXT[deliverable]
        lines.append(f"成果形式：{zh}。" if chinese else f"Output form: {en}.")
    if not lines:
        return ""
    heading = "【开工设置（我在界面上选的）】" if chinese else "[Launch settings I chose in the UI]"
    return "\n\n" + heading + "\n" + "\n".join(f"- {line}" for line in lines)


def launch_target_level_stated(text: str) -> bool:
    """Whether the request carries a research ambition chosen in launch settings."""
    body = str(text or "")
    return (
        ("【开工设置" in body and "研究定位：" in body)
        or ("[Launch settings" in body and "Research ambition:" in body)
    )
