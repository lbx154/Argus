"""Small, human-readable operator decision cards.

Cards live on the blocked backlog item, so the question, options, and resolution
share the backlog's existing lock and persistence. IDs are readable item-based
labels; revisions are plain integers.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping


def _human_reason(reason: str, *, language_hint: str) -> str:
    from .operator_messages import humanize_runtime_reason

    return humanize_runtime_reason(reason, language_hint=language_hint)


def normalize_agent_options(
    options: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, row in enumerate(options):
        if not isinstance(row, Mapping):
            continue
        label = str(row.get("label") or "").strip()[:160]
        if not label:
            continue
        raw_id = str(row.get("id") or "").strip().casefold()
        option_id = re.sub(r"[^a-z0-9_-]+", "-", raw_id).strip("-_")
        if not option_id:
            option_id = f"option-{index + 1}"
        elif option_id == "custom":
            option_id = f"option-{index + 1}"
        base_id = option_id
        suffix = 2
        while option_id in used_ids:
            option_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(option_id)
        normalized.append({
            "id": option_id,
            "label": label,
            "description": str(row.get("description") or "").strip()[:1000],
            "requires_note": bool(row.get("requires_note")),
        })
        if len(normalized) >= 8:
            break
    return normalized


def parse_agent_operator_options(message: str) -> list[dict[str, Any]]:
    text = "\n".join(
        (
            stripped[1:-1].strip()
            if len(stripped := line.strip()) >= 2
            and stripped.startswith("`")
            and stripped.endswith("`")
            else line
        )
        for line in str(message or "").splitlines()
    )
    match = re.search(r"(?im)^\s*OPERATOR_OPTIONS\s*=\s*", text)
    if match is None:
        return []
    raw = text[match.end():]
    next_field = re.search(r"(?m)^\s*[A-Z][A-Z0-9_]{2,}\s*=", raw)
    if next_field is not None:
        raw = raw[:next_field.start()]
    raw = raw.strip()
    if raw.casefold().rstrip(".") in {"", "none", "n/a", "na", "null"}:
        return []
    if raw.startswith("["):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return normalize_agent_options(
            row for row in payload if isinstance(row, Mapping)
        )
    options: list[dict[str, Any]] = []
    for encoded in raw.split(";"):
        parts = [part.strip() for part in encoded.split("::")]
        if len(parts) == 3:
            option_id, label, description = parts
            requires_note = False
        elif len(parts) == 4:
            option_id, raw_requires_note, label, description = parts
            requires_note = raw_requires_note.casefold() == "true"
        else:
            continue
        options.append({
            "id": option_id,
            "label": label,
            "description": description,
            "requires_note": requires_note,
        })
    return normalize_agent_options(options)


def build_operator_decision(
    *,
    item_id: str,
    title: str,
    reason: str,
    question: str,
    options: Iterable[Mapping[str, Any]] = (),
    evidence: Iterable[Mapping[str, Any]] = (),
    project_id: str = "",
) -> dict[str, Any]:
    agent_options = normalize_agent_options(options)
    card: dict[str, Any] = {
        "id": f"decision-{item_id}",
        "item_id": item_id,
        "revision": 1,
        "status": "pending",
        "title": title.strip() or "Operator decision required",
        "reason": _human_reason(
            reason,
            language_hint=f"{title}\n{question}",
        ),
        "question": question.strip(),
        "evidence": [
            {
                "label": str(row.get("label") or row.get("why") or "Evidence"),
                "path": str(row.get("path") or row.get("ref") or ""),
                "summary": str(row.get("summary") or row.get("why") or ""),
            }
            for row in evidence
            if isinstance(row, Mapping)
        ],
        "options": agent_options,
        "options_source": "agent" if agent_options else "none",
        "selected_option": "",
        "note": "",
    }
    if project_id.strip():
        card["project_id"] = project_id.strip()
    return card


def selected_decision_text(card: Mapping[str, Any], option_id: str, note: str) -> str:
    note = note.strip()
    if option_id == "custom":
        if not note:
            raise ValueError("this decision requires an answer")
        return note
    option = next(
        (
            row
            for row in card.get("options", [])
            if isinstance(row, Mapping) and str(row.get("id")) == option_id
        ),
        None,
    )
    if option is None:
        raise ValueError("unknown decision option")
    requires_note = bool(option.get("requires_note"))
    if requires_note and not note:
        raise ValueError("this option requires guidance")
    description = str(option.get("description") or "").strip()
    if requires_note and not description:
        return note
    selected_text = description or str(option.get("label") or "").strip()
    return f"{selected_text}\n\nOperator note: {note}" if note else selected_text


__all__ = [
    "build_operator_decision",
    "normalize_agent_options",
    "parse_agent_operator_options",
    "selected_decision_text",
]
