"""Structured role handoff parsing for model-authored round summaries.

A role ends its turn with prose and one line naming the next holder, the
baton line of docs/role-handoff-design.md: ``→ reviewer``, ``→ engineer``,
``→ planner``, ``→ manager``, ``→ operator: <question>`` or ``→ done``. The older ``NEXT_OWNER=`` footer is still read when no such line
is present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from .operator_decision import normalize_agent_options, parse_agent_operator_options

HandoffOwner = Literal[
    "engineer", "reviewer", "planner", "manager", "operator", "done"
]

_EMPTY_VALUES = frozenset({"", "none", "n/a", "na", "null"})

# The control lines of the older Engineer footer. Every surface that shows a
# handoff to a person strips them, and the baton line, with ``strip_handoff``.
FOOTER_KEYS = (
    "MILESTONE_STATUS",
    "NEXT_OWNER",
    "OPERATOR_QUESTION",
    "OPERATOR_OPTIONS",
    "OPERATOR_NEED",
    "ASSUMPTION",
    "ROLE_DECISION",
)
# A ``RESULT=`` line's key, which a person never needs to read.
_RESULT_KEY = re.compile(
    r"(?im)^[ \t]*(?:[-*+][ \t]*)?[`*_]*(?:ARGUS_)?RESULT[`*_]*[ \t]*[:=][ \t]*"
)
# The holders a baton line may name, in English or by the role names the
# operator sees.
_HOLDERS = {
    "engineer": "engineer", "执行": "engineer",
    "reviewer": "reviewer", "复核": "reviewer",
    "planner": "planner", "规划": "planner",
    "manager": "manager", "统筹": "manager",
    "operator": "operator", "操作员": "operator",
    "done": "done", "完成": "done",
}
# One holder after an arrow, optionally with ``: <argument>``; a bullet, bold,
# backticks around the holder and a full-width colon are decoration.
_BATON = re.compile(
    r"^[\s>*-]*(?:→|->|=>)\s*[*`]*(?P<owner>[^\s:：*`]+)[*`]*"
    r"\s*(?:[:：]\s*(?P<arg>.*?))?[\s*]*$"
)


def _baton(line: str) -> re.Match[str] | None:
    """Match one baton line, unwrapping a line wrapped whole in backticks."""
    line = line.strip()
    if len(line) > 1 and line[0] == line[-1] == "`":
        line = line.strip("`")
    return _BATON.match(line)


@dataclass(frozen=True)
class EngineerHandoff:
    """The next role and any genuine operator decision requested by Engineer."""

    next_owner: HandoffOwner
    operator_question: str = ""
    operator_options: tuple[dict, ...] = ()
    source: str = "default"
    # The Engineer's own answer to "why can only the operator decide this?"
    # (one of ``core.autonomy.OPERATOR_NEEDS``), or "" when it named none.
    operator_need: str = ""

    @property
    def waits_for_operator(self) -> bool:
        return self.next_owner == "operator" and bool(self.operator_question)


def _named_value(
    message: str, name: str, *, limit: int = 500, keep_none: bool = False
) -> str:
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
            value = (
                ""
                if not keep_none
                and normalized.casefold().rstrip(".") in _EMPTY_VALUES
                else normalized[:limit]
            )
    return value


def resolve_engineer_handoff(
    *,
    next_owner: object,
    operator_question: object,
    operator_options: Sequence[dict] = (),
    operator_need: object = "",
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
    from .autonomy import normalize_operator_need

    need = normalize_operator_need(operator_need)

    if owner in {"reviewer", "planner", "manager"}:
        return EngineerHandoff(owner, source="structured")
    if owner == "operator" and question:
        return EngineerHandoff(
            "operator", question, options, source="structured", operator_need=need
        )
    if owner == "engineer" and not question:
        return EngineerHandoff("engineer", source="structured")
    if question:
        return EngineerHandoff(
            "operator", question, options, source="operator_question", operator_need=need
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
        operator_need=payload.get("operator_need"),
    )


def baton_lines(text: str) -> list[tuple[str, str]]:
    """Every ``→ <holder>[: <argument>]`` line of ``text``, in order.

    The holder is normalized, and "" when it is not one of the seven.
    """
    return [
        (_HOLDERS.get(match["owner"].casefold(), ""), (match["arg"] or "").strip())
        for match in map(_baton, str(text or "").splitlines())
        if match
    ]


def read_baton_line(text: str) -> EngineerHandoff | None:
    """The handoff a closing ``→ <holder>`` line names, or ``None`` without one.

    Only the decision region is read: after the last ``Decision:``, else the
    last five non-empty lines, so an arrow quoted from the task cannot route
    the work. An unknown holder, two lines that disagree, a ``NEXT_OWNER=``
    footer naming someone else, or ``operator:`` with nothing after it falls
    back to the Reviewer. Nothing unreadable is ever ``done``.
    """
    from .role_reply import decision_footer_text

    footer = decision_footer_text(text)
    lines = [line for line in footer.splitlines() if line.strip()]
    if footer == str(text or ""):
        lines = lines[-5:]
    named = set(baton_lines("\n".join(lines)))
    if not named:
        return None
    fallback = EngineerHandoff("reviewer", source="default")
    if len(named) > 1:
        return fallback
    ((owner, argument),) = named
    footer_owner = _named_value(footer, "NEXT_OWNER", limit=32).casefold()
    disagrees = footer_owner and footer_owner != {"done": "reviewer"}.get(owner, owner)
    if not owner or disagrees or (owner == "operator" and not argument):
        return fallback
    if owner == "operator":
        return EngineerHandoff(
            "operator",
            argument[:500],
            tuple(parse_agent_operator_options(footer)),
            source="structured",
            operator_need=_named_value(footer, "OPERATOR_NEED", limit=64, keep_none=True),
        )
    return EngineerHandoff(owner, source="structured")


def strip_handoff(text: str) -> str:
    """The prose of a handoff, as a person reads it.

    Footer control lines and the baton line go; a ``RESULT=`` line keeps its
    sentence, which was always written for the operator.
    """
    from .role_reply import strip_control_footer

    prose = "\n".join(
        line for line in _RESULT_KEY.sub("", str(text or "")).splitlines()
        if not _baton(line)
    )
    return strip_control_footer(prose, FOOTER_KEYS)


def parse_engineer_handoff(message: str) -> EngineerHandoff:
    """Read the handoff out of a round summary that carried no decision event.

    The baton line wins; the older footer is read only when there is none.
    """
    from .role_reply import decision_footer_text

    baton = read_baton_line(message)
    if baton is not None:
        return baton
    footer = decision_footer_text(message)
    return resolve_engineer_handoff(
        next_owner=_named_value(footer, "NEXT_OWNER", limit=32),
        operator_question=_named_value(footer, "OPERATOR_QUESTION"),
        operator_options=tuple(parse_agent_operator_options(footer)),
        # "none" is a classification here, not an empty value.
        operator_need=_named_value(footer, "OPERATOR_NEED", limit=64, keep_none=True),
    )


__all__ = [
    "FOOTER_KEYS",
    "EngineerHandoff",
    "HandoffOwner",
    "baton_lines",
    "decision_engineer_handoff",
    "parse_engineer_handoff",
    "read_baton_line",
    "resolve_engineer_handoff",
    "strip_handoff",
]
