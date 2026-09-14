"""Structured role handoff parsing for model-authored round summaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from .operator_decision import normalize_agent_options, parse_agent_operator_options

HandoffOwner = Literal["engineer", "reviewer", "operator"]

_EMPTY_VALUES = frozenset({"", "none", "n/a", "na", "null"})


@dataclass(frozen=True)
class EngineerHandoff:
    """The next role and any genuine operator decision requested by Engineer."""

    next_owner: HandoffOwner
    operator_question: str = ""
    operator_options: tuple[dict, ...] = ()
    source: str = "default"

    @property
    def waits_for_operator(self) -> bool:
        return self.next_owner == "operator" and bool(self.operator_question)


def _named_value(message: str, name: str, *, limit: int = 500) -> str:
    value = ""
    expected = name.casefold()
    for line in str(message or "").splitlines():
        normalized_line = line.strip()
        if (
            len(normalized_line) >= 2
            and normalized_line.startswith("`")
            and normalized_line.endswith("`")
        ):
            normalized_line = normalized_line[1:-1].strip()
        key, separator, candidate = normalized_line.partition("=")
        if separator and key.strip().casefold() == expected:
            normalized = candidate.strip()
            value = "" if normalized.casefold().rstrip(".") in _EMPTY_VALUES else normalized[:limit]
    return value


def resolve_engineer_handoff(
    *,
    next_owner: object,
    operator_question: object,
    operator_options: Sequence[dict] = (),
) -> EngineerHandoff:
    """Resolve the next role from handoff fields that are already separated.

    Both callers below reach this with three values. The difference is only
    where they came from: the Engineer's decision event states them, and a
    prose round summary has to be read for them.
    """
    owner = str(next_owner or "").strip().casefold()[:32]
    question = str(operator_question or "").strip()
    if question.casefold().rstrip(".") in _EMPTY_VALUES:
        question = ""
    question = question[:500]
    options = tuple(operator_options or ())

    if owner == "reviewer":
        return EngineerHandoff("reviewer", source="structured")
    if owner == "operator" and question:
        return EngineerHandoff("operator", question, options, source="structured")
    if owner == "engineer" and not question:
        return EngineerHandoff("engineer", source="structured")
    if question:
        return EngineerHandoff(
            "operator", question, options, source="operator_question"
        )
    return EngineerHandoff("reviewer", source="default")


def decision_engineer_handoff(payload: Mapping[str, object]) -> EngineerHandoff:
    """Read the handoff straight out of the Engineer's decision event.

    The runtime used to render this payload into ``NEXT_OWNER=`` lines and read
    them back. That put the model's own prose — rendered first — in a position
    to answer questions the payload had already answered.
    """
    raw_options = payload.get("operator_options")
    option_values = raw_options if isinstance(raw_options, (list, tuple)) else ()
    return resolve_engineer_handoff(
        next_owner=payload.get("next_owner"),
        operator_question=payload.get("operator_question"),
        operator_options=normalize_agent_options(option_values),
    )


def parse_engineer_handoff(message: str) -> EngineerHandoff:
    """Read the handoff out of a prose round summary that carried no decision."""
    from .role_reply import decision_footer_text

    footer = decision_footer_text(message)
    return resolve_engineer_handoff(
        next_owner=_named_value(footer, "NEXT_OWNER", limit=32),
        operator_question=_named_value(footer, "OPERATOR_QUESTION"),
        operator_options=tuple(parse_agent_operator_options(footer)),
    )


__all__ = [
    "EngineerHandoff",
    "HandoffOwner",
    "decision_engineer_handoff",
    "parse_engineer_handoff",
    "resolve_engineer_handoff",
]
