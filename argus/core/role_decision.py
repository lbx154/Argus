"""Role decisions emitted during an agent turn."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

ROLE_DECISION_PREFIX = "ARGUS_ROLE_DECISION="
_ROLES = frozenset({"manager", "planner", "engineer", "reviewer"})


def _decode_json_value(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        decoded, end = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    trailing = raw[end:].strip()
    if trailing and not set(trailing) <= {"}", "]", "`"}:
        return None
    return decoded


def encode_role_decision(role: str, payload: dict[str, Any]) -> str:
    """Encode one decision for the Host event stream."""
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in _ROLES:
        raise ValueError(f"unknown Argus role: {role!r}")
    if not isinstance(payload, dict):
        raise TypeError("role decision payload must be an object")
    return ROLE_DECISION_PREFIX + json.dumps(
        {"role": normalized_role, "payload": payload},
        ensure_ascii=True,
        separators=(",", ":"),
    )


def extract_role_decisions(values: Iterable[Any]) -> list[dict[str, Any]]:
    """Extract direct decision events from assistant-authored values.

    Tool-result envelopes are deliberately opaque here. A marker printed by a
    tool is evidence the role saw, not a decision authored by the role.
    """
    decisions: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if (
                value.get("role") in _ROLES
                and isinstance(value.get("payload"), dict)
            ):
                decisions.append(value)
            return
        if not isinstance(value, str):
            return

        for line in value.splitlines():
            marker = line.find(ROLE_DECISION_PREFIX)
            if marker < 0:
                continue
            raw = (
                line[marker + len(ROLE_DECISION_PREFIX) :]
                .strip()
                .strip("`")
                .strip()
            )
            decision = _decode_json_value(raw)
            if decision is None:
                continue
            if (
                isinstance(decision, dict)
                and decision.get("role") in _ROLES
                and isinstance(decision.get("payload"), dict)
            ):
                decisions.append(decision)

    for value in values:
        visit(value)
    return decisions


def latest_role_decision(result: Any, role: str) -> dict[str, Any] | None:
    """Return the first decision event for ``role`` from its own output."""
    normalized_role = str(role or "").strip().lower()
    values: list[Any] = list(getattr(result, "role_decisions", None) or [])
    values.extend(getattr(result, "agent_messages", None) or [])
    for decision in extract_role_decisions(values):
        if decision["role"] == normalized_role:
            return dict(decision["payload"])
    return None


def decision_footer_instruction(example: str) -> str:
    """Ask for natural reasoning followed by the minimum actionable footer."""
    return (
        "Reason naturally, then end with only the Host actions below "
        "(replace examples; omit unused lines):\nDecision:\n"
        + str(example or "").strip()
    )


__all__ = [
    "ROLE_DECISION_PREFIX",
    "decision_footer_instruction",
    "encode_role_decision",
    "extract_role_decisions",
    "latest_role_decision",
]
