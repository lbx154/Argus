"""The Manager decides a parked operator question in the operator's place.

Only while the operator cannot be asked (principle 8): they said not to be, or
the run has none. The Manager names the card and one of its options in typed
fields (its own answer only on a card that offers none); the host applies that
choice on the path an operator's choice takes and records it in the Manager's
name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_FIELDS = ("DECISION_ID", "OPTION", "NOTE", "REASON", "EVIDENCE_REFS")


@dataclass(frozen=True)
class ProxyChoice:
    decision_id: str
    option: str
    note: str
    reason: str
    evidence_refs: tuple[str, ...]


def _option_ids(card: Mapping[str, Any]) -> list[str]:
    """What the Manager may choose on ``card``: one of its options, its own answer where it has none."""
    ids = [
        str(row.get("id") or "")
        for row in card.get("options", ())
        if isinstance(row, Mapping)
    ]
    return ids or ["custom"]


def decide_for_operator(
    manager: Any,
    card: Mapping[str, Any],
    *,
    why: str,
    context: str = "",
    on_event: Any = None,
) -> ProxyChoice | None:
    """The Manager's choice on ``card``, or ``None`` when it named no valid one."""
    from ..core.operator_decision import normalize_option_id, selected_decision_text
    from ..core.role_reply import read_key_values

    fields = read_key_values(
        manager.run_turn(
            _prompt(card, why=why, context=context),
            on_event=on_event,
            run_label="manager-proxy-decision",
        ),
        _FIELDS,
    )
    choice = ProxyChoice(
        decision_id=fields.get("DECISION_ID", "").strip(),
        option=normalize_option_id(fields.get("OPTION")),
        note=fields.get("NOTE", "").strip(),
        reason=fields.get("REASON", "").strip(),
        evidence_refs=tuple(
            ref.strip() for ref in fields.get("EVIDENCE_REFS", "").split(";") if ref.strip()
        ),
    )
    if (
        choice.decision_id != str(card.get("id") or "")
        or not choice.reason
        or choice.option not in _option_ids(card)
    ):
        return None
    try:
        selected_decision_text(card, choice.option, choice.note)
    except ValueError:
        return None
    return choice


def _prompt(card: Mapping[str, Any], *, why: str, context: str) -> str:
    options = "\n".join(
        f"- {row.get('id')}: {row.get('label')}"
        + (f" — {row.get('description')}" if row.get("description") else "")
        for row in card.get("options", ())
        if isinstance(row, Mapping)
    )
    if "custom" in _option_ids(card):
        options += "\n- custom: your own answer, written in NOTE"
    evidence = "\n".join(
        f"- {row.get('path')}: {row.get('summary') or row.get('label') or ''}"
        for row in card.get("evidence", ())
        if isinstance(row, Mapping) and row.get("path")
    )
    return (
        f"You are the project Manager. {why}, so you hold the operator's authority "
        "and decide this question in their place. That authority covers spending, "
        "irreversible or outward-facing steps and releases; credentials you do not "
        "hold stay out of reach, so choose a way forward without them. Decide as "
        "the operator would for their objective. Your decision is recorded in your "
        "name and listed for the operator to review.\n\n"
        + (f"{context.strip()}\n\n" if context.strip() else "")
        + f"Question: {card.get('question')}\n"
        + f"Why it was asked: {card.get('reason')}\n"
        + f"Options:\n{options}\n"
        + (f"Evidence:\n{evidence}\n" if evidence else "")
        + "\nEnd your reply with these fields, each on its own line in the form KEY: value:\n"
        f"DECISION_ID: {card.get('id')}\n"
        "OPTION: the id of your choice\n"
        "NOTE: guidance for the team (required for custom)\n"
        "REASON: why this is the operator's best choice here\n"
        "EVIDENCE_REFS: semicolon-separated paths you relied on\n"
    )
