"""Terminal-only role color helpers."""

from __future__ import annotations

from typing import Any

ROLE_COLOR: dict[str, str] = {
    "manager": "cyan",
    "planner": "magenta",
    "engineer": "green",
    "reviewer": "yellow",
}
ROLE_COLOR_BOLD: dict[str, str] = {
    "manager": "bold_cyan",
    "planner": "bold_magenta",
    "engineer": "bold_green",
    "reviewer": "bold_yellow",
}


def _paint(theme: Any, method: str, text: str) -> str:
    if theme is None or not text:
        return text
    fn = getattr(theme, method, None)
    if not callable(fn):
        return text
    try:
        return str(fn(text))
    except Exception:  # noqa: BLE001 - display color must never break output
        return text


def role_paint(theme: Any, role: str, text: str, *, bold: bool = True) -> str:
    """Paint ``text`` in the role's signature terminal hue."""
    table = ROLE_COLOR_BOLD if bold else ROLE_COLOR
    method = table.get((role or "").strip().lower())
    return _paint(theme, method, text) if method else text


__all__ = ["ROLE_COLOR", "ROLE_COLOR_BOLD", "role_paint"]
