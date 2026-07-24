"""Parse the Reviewer's minimal verdict."""

from __future__ import annotations

import json
from typing import Any

from ..core.models import ReviewDecision

_STATUSES = {"done", "continue", "blocked", "replan_requested"}


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_json_objects(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    direct = _load_json(text)
    if direct is not None:
        candidates.append(direct)
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        extracted = _load_json(text[left : right + 1])
        if extracted is not None and extracted not in candidates:
            candidates.append(extracted)
    return candidates


def parse_decision_text(
    text: str,
    *,
    allow_research_pause: bool = False,
) -> ReviewDecision | None:
    """Return a verdict only when all four control fields are valid."""
    _ = allow_research_pause
    for parsed in _candidate_json_objects(_strip_markdown_fences(text)):
        status = str(parsed.get("status") or "").strip().lower()
        reason = parsed.get("reason")
        next_action = parsed.get("next_action")
        operator_question = parsed.get("operator_question")
        if status not in _STATUSES:
            continue
        if not isinstance(reason, str) or not reason.strip():
            continue
        if not isinstance(next_action, str):
            continue
        if operator_question is not None and not isinstance(operator_question, str):
            continue
        return ReviewDecision(
            status=status,
            reason=reason.strip(),
            next_action=next_action.strip(),
            operator_question=str(operator_question or "").strip(),
        )
    return None


def _find_decision_in_messages(
    messages: list[str],
    *,
    allow_research_pause: bool = False,
) -> ReviewDecision | None:
    _ = allow_research_pause
    for message in reversed(messages):
        decision = parse_decision_text(message)
        if decision is not None:
            return decision
    if len(messages) > 1:
        return parse_decision_text("\n".join(messages))
    return None


__all__ = ["_find_decision_in_messages", "parse_decision_text"]
