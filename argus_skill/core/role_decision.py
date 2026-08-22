"""Role decisions emitted during an agent turn."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

ROLE_DECISION_PREFIX = "ARGUS_ROLE_DECISION="
_ROLES = frozenset({"manager", "planner", "engineer", "reviewer"})


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
    """Extract decisions from assistant messages or nested JSON event lines."""
    decisions: list[dict[str, Any]] = []

    def visit(value: Any, *, allow_envelope: bool = False) -> None:
        if isinstance(value, dict):
            if (
                allow_envelope
                and value.get("role") in _ROLES
                and isinstance(value.get("payload"), dict)
            ):
                decisions.append(value)
                return
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, list):
            for nested in value:
                visit(nested)
            return
        if not isinstance(value, str):
            return

        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                visit(decoded)

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
            try:
                decision = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(decision, dict)
                and decision.get("role") in _ROLES
                and isinstance(decision.get("payload"), dict)
            ):
                decisions.append(decision)

    for value in values:
        visit(value, allow_envelope=isinstance(value, dict))
    return decisions


def latest_role_decision(result: Any, role: str) -> dict[str, Any] | None:
    """Return the latest decision for ``role`` from a runner result."""
    normalized_role = str(role or "").strip().lower()
    values: list[Any] = list(getattr(result, "role_decisions", None) or [])
    values.extend(getattr(result, "agent_messages", None) or [])
    values.extend(getattr(result, "stdout_lines", None) or [])
    for decision in reversed(extract_role_decisions(values)):
        if decision["role"] == normalized_role:
            return dict(decision["payload"])
    return None


def decision_event_instruction(role: str, payload_example: str) -> str:
    """Render the small shared process-decision instruction."""
    return (
        "When the decision is clear, immediately send this single-line event "
        "before any optional explanation:\n"
        f"{ROLE_DECISION_PREFIX}"
        f'{{"role":"{role}","payload":{payload_example}}}\n'
        "The Host saves this event. Any later response is plain language and is "
        "not parsed."
    )


__all__ = [
    "ROLE_DECISION_PREFIX",
    "decision_event_instruction",
    "encode_role_decision",
    "extract_role_decisions",
    "latest_role_decision",
]
