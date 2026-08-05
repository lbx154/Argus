"""Small, human-readable operator decision cards.

Cards live on the blocked backlog item, so the question, options, and resolution
share the backlog's existing lock and persistence. IDs are readable item-based
labels; revisions are plain integers.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def build_operator_decision(
    *,
    item_id: str,
    title: str,
    reason: str,
    question: str,
    recommendation: str = "",
    evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    if recommendation.strip():
        options.append({
            "id": "recommended",
            "label": "Use the recommended next step",
            "description": recommendation.strip(),
            "requires_note": False,
        })
    options.extend([
        {
            "id": "custom",
            "label": "Give different guidance",
            "description": question.strip(),
            "requires_note": True,
        },
        {
            "id": "stop",
            "label": "Stop this campaign",
            "description": "Keep the current work and stop automatic continuation.",
            "requires_note": False,
        },
    ])
    return {
        "id": f"decision-{item_id}",
        "item_id": item_id,
        "revision": 1,
        "status": "pending",
        "title": title.strip() or "Operator decision required",
        "reason": reason.strip(),
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
        "options": options,
        "selected_option": "",
        "note": "",
    }


def selected_decision_text(card: Mapping[str, Any], option_id: str, note: str) -> str:
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
    note = note.strip()
    if bool(option.get("requires_note")) and not note:
        raise ValueError("this option requires guidance")
    if option_id == "custom":
        return note
    description = str(option.get("description") or "").strip()
    return f"{description}\n\nOperator note: {note}" if note else description


__all__ = ["build_operator_decision", "selected_decision_text"]
