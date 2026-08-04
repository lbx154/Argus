"""ANSI terminal rendering for live headless-runtime events.

The Ink/Web cockpit owns interactive UI. This module only decorates events
written by ``LifeStderrSink`` and teammate processes; semantic event formatting
stays in :mod:`argus_skill.cli.event_format`.
"""

from __future__ import annotations

import os
from typing import Any

from ..core.event_catalog import EventType, canonical_event_type
from ..core.secret_guard import redact_secrets_text
from .event_format import _strip_shell_wrapper, _trunc, format_event_message
from .theme import Theme

_REVIEW_STATUS_COLOR = {
    "✅": "bold_green",
    "↻": "yellow",
    "⛔": "bold_red",
    "🚫": "bold_red",
}


def _colorize_first_line(line: str, color_method: str, theme: Theme) -> str:
    """Apply a colour to the first line; leave the rest untouched."""
    if "\n" in line:
        head, _, rest = line.partition("\n")
        return getattr(theme, color_method)(head) + "\n" + rest
    return getattr(theme, color_method)(line)


def _round_index_from_event(event: dict[str, Any]) -> int | None:
    idx = event.get("round_index")
    return idx if isinstance(idx, int) and idx > 0 else None


def _event_succeeded(event: dict[str, Any]) -> bool:
    success = event.get("success")
    if isinstance(success, bool):
        return success
    status = str(event.get("status") or "").lower()
    if status:
        return status in {"done", "success", "completed"}
    return "success=False" not in str(event.get("text") or "")


def render_event_for_terminal(event: dict[str, Any], *, theme: Theme) -> str:
    """Render one canonical event as terminal text, with fail-soft coloring."""
    kind = canonical_event_type(event.get("type"))

    if kind == EventType.ENGINEER_PROGRESS:
        return _render_engineer_progress_terminal(event, theme=theme)

    if kind == EventType.ROUND_START:
        return "\n" + theme.hr(f"Round {_round_index_from_event(event) or '?'}")

    body = format_event_message(event)
    if kind == EventType.LIFE_MISSION_STARTED:
        return "\n" + theme.hr("Mission") + "\n" + theme.bold_cyan(body)
    if kind == EventType.LOOP_START:
        return _colorize_first_line(body, "bold_cyan", theme)
    if kind in {EventType.LOOP_DONE, EventType.LIFE_MISSION_COMPLETED}:
        method = "bold_green" if _event_succeeded(event) else "bold_red"
        return _colorize_first_line(body, method, theme)
    if kind == EventType.LIFE_MISSION_FAILED:
        return _colorize_first_line(body, "bold_red", theme)
    if kind == EventType.ROUND_MAIN_COMPLETED:
        return _colorize_first_line(body, "bold_blue", theme)
    if kind == EventType.ROUND_REVIEW_COMPLETED:
        first = body.split("\n", 1)[0]
        method = next(
            (color for icon, color in _REVIEW_STATUS_COLOR.items() if icon in first),
            "bold_blue",
        )
        return _colorize_first_line(body, method, theme)
    if kind == EventType.LIFE_PLANNER_VERDICT:
        return _colorize_first_line(body, "magenta", theme)
    return body


_SHOW_REASONING = os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _render_engineer_progress_terminal(event: dict[str, Any], *, theme: Theme) -> str:
    """Render model speech prominently and operations as dim single lines."""
    kind = str(event.get("kind") or "").strip()
    text = redact_secrets_text(str(event.get("text") or "")).strip()
    if not text and kind not in {"file_change", "command_execution", "tool_use"}:
        return ""

    if kind == "reasoning":
        if not _SHOW_REASONING:
            return ""
        return theme.dim("  ⋯ " + _trunc(_first_line(text), 200))

    if kind in {"assistant_message", "agent_message", "message"}:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        bar = theme.cyan("▌")
        return "\n".join(f"{bar} {theme.bold(_trunc(line, 240))}" for line in lines)

    if kind == "command_execution":
        action = redact_secrets_text(str(event.get("action_summary") or "")).strip()
        if action:
            return theme.dim("  ▸ " + _trunc(action, 200))
        command = _strip_shell_wrapper(_first_line(text))
        return theme.dim("  ▸ $ " + _trunc(command, 200))
    if kind == "tool_use":
        return theme.dim("  ▸ ⚙ " + _trunc(_first_line(text), 200))
    if kind == "file_change":
        return theme.dim("  ▸ ✎ " + _trunc(_first_line(text), 200))
    return theme.dim("  ▸ " + _trunc(_first_line(text) or kind, 200))


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), text.strip())


__all__ = ["render_event_for_terminal"]
