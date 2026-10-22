"""Small, human-readable operator decision cards.

Cards live on the blocked backlog item, so the question, options, and resolution
share the backlog's existing lock and persistence. IDs are readable item-based
labels; revisions are plain integers.
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterable, Mapping


def _human_reason(reason: str, *, language_hint: str) -> str:
    from .operator_messages import humanize_runtime_reason

    return humanize_runtime_reason(reason, language_hint=language_hint)


def normalize_option_id(value: Any) -> str:
    """The id an option's raw ``id`` becomes on a card ("" when none survives)."""
    raw_id = str(value or "").strip().casefold()
    return re.sub(r"[^a-z0-9_-]+", "-", raw_id).strip("-_")


def normalize_agent_options(
    options: Iterable[Mapping[str, Any] | str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    rows: Iterable[Mapping[str, Any] | str] = (
        (options,) if isinstance(options, str) else options
    )
    for index, row in enumerate(rows):
        if isinstance(row, str):
            row = {"label": row}
        elif not isinstance(row, Mapping):
            continue
        label = str(row.get("label") or "").strip()[:160]
        if not label:
            continue
        option_id = normalize_option_id(row.get("id"))
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
        })
        if len(normalized) >= 8:
            break
    return normalized


def build_operator_decision(
    *,
    item_id: str,
    title: str,
    reason: str,
    question: str,
    options: Iterable[Mapping[str, Any] | str] = (),
    evidence: Iterable[Mapping[str, Any]] = (),
    project_id: str = "",
    previous_decision: Mapping[str, Any] | None = None,
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
    same_item = previous_decision is not None and previous_decision.get("item_id") == item_id
    previous_id = str(previous_decision.get("id") or "") if same_item else ""
    if (
        same_item
        and previous_decision is not None
        and previous_decision.get("status") == "pending"
        and str(previous_decision.get("question") or "").strip() == card["question"]
        and list(previous_decision.get("options") or []) == agent_options
    ):
        # Rebuilding an unanswered card must not make an old question look new.
        # Older cards have no recorded time; keep that absence truthful too.
        if previous_id:
            card["id"] = previous_id
        for key in ("asked_at", "question_revision", "superseded_decision_ids"):
            if key in previous_decision:
                card[key] = previous_decision[key]
    else:
        card["asked_at"] = time.time()
        if previous_id and previous_decision is not None:
            # A different question on the same item gets a new id, and the old
            # one is remembered as superseded: an answer written for the old
            # question (whose option ids may now mean something else) is then
            # rejected as stale instead of applied to the new one.
            question_revision = int(previous_decision.get("question_revision", 1) or 1) + 1
            superseded = [
                str(value)
                for value in previous_decision.get("superseded_decision_ids", ())
                if str(value)
            ]
            card["id"] = f"decision-{item_id}-q{question_revision}"
            card["question_revision"] = question_revision
            card["superseded_decision_ids"] = [*superseded, previous_id][-16:]
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
    selected_text = str(option.get("description") or option.get("label") or "").strip()
    return f"{selected_text}\n\nNote: {note}" if note else selected_text


__all__ = [
    "build_operator_decision",
    "normalize_agent_options",
    "selected_decision_text",
]
