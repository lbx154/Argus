"""The baton line a role ends its turn with.

A role ends its turn with prose and one line naming the next holder, the
baton line of docs/role-handoff-design.md: ``→ reviewer``, ``→ engineer``,
``→ planner``, ``→ manager``, ``→ operator: <question>`` or ``→ done``. That
line is the only handoff the host reads; the choices a question offers are
listed under it, one ``- <answer>`` line each.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .contract_resources import contract_schema_path
from .models import ReviewStatus

#: How a round settles on each holder a handoff names: the Engineer works on,
#: the Planner replans, the Manager or the operator decides, or the candidate
#: is accepted. The Reviewer is reached inside the round.
HANDOFF_STATUS: dict[str, ReviewStatus] = {
    "engineer": "continue", "planner": "replan_requested",
    "manager": "blocked", "operator": "blocked", "done": "done",
}
#: The rungs an unchanged return climbs, one at a time (invariant 3).
LADDER = ("engineer", "reviewer", "planner", "manager", "operator")

# A ``RESULT=`` line's key, which a person never needs to read.
_RESULT_KEY = re.compile(
    r"(?im)^[ \t]*(?:[-*+][ \t]*)?[`*_]*(?:ARGUS_)?RESULT[`*_]*[ \t]*[:=][ \t]*"
)
# The holders a baton line may name, in English or by the role names the
# operator sees; the older Chinese names stay readable. The frontend hides
# these lines by the same names (generated from the same contract).
_HOLDERS = {
    name: holder
    for holder, names in json.loads(
        contract_schema_path("baton_holders.json").read_text(encoding="utf-8")
    ).items()
    for name in names
}
# One holder after an arrow, optionally with ``: <argument>``; a bullet, bold,
# backticks around the holder and a full-width colon are decoration.
_BATON = re.compile(
    r"^[\s>*-]*(?:→|->|=>)\s*[*`]*(?P<owner>[^\s:：*`]+)[*`]*"
    r"\s*(?:[:：]\s*(?P<arg>.*?))?[\s*]*$"
)


# A choice listed under an operator question: a bulleted or numbered list item.
_CHOICE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?P<choice>\S.*?)\s*$")
#: How a role offers the operator choices, said once for every role's prompt.
OPERATOR_CHOICES_RULE = (
    "Ask on the `→ operator:` line itself; any answers to pick from go one per line below: "
    "`→ operator: Keep it?`, `- Yes`, `- No`."
)


def _baton(line: str) -> re.Match[str] | None:
    """Match one baton line, unwrapping a line wrapped whole in backticks."""
    line = line.strip()
    if len(line) > 1 and line[0] == line[-1] == "`":
        line = line.strip("`")
    return _BATON.match(line)


def _choice(line: str) -> str:
    """The answer a list line offers, as written; "" for any other line."""
    match = None if _baton(line) else _CHOICE.match(line)
    return match["choice"] if match else ""


def _list_start(lines: list[str]) -> int:
    """Where the list closing ``lines`` (blank lines aside) begins."""
    start = len(lines)
    while start and (not lines[start - 1].strip() or _choice(lines[start - 1])):
        start -= 1
    return start


def listed_choices(text: str) -> tuple[str, ...]:
    """The answers a question offers, listed one per line at the close of ``text``."""
    lines = str(text or "").splitlines()
    return tuple(filter(None, map(_choice, lines[_list_start(lines):])))


def _choices_start(lines: list[str]) -> int:
    """Where the choices listed under a closing operator question begin.

    ``len(lines)`` when the closing list does not sit directly under an
    operator line: then it is prose, not choices.
    """
    start = _list_start(lines)
    asked = _baton(lines[start - 1]) if start else None
    if asked and _HOLDERS.get(asked["owner"].casefold()) == "operator":
        return start
    return len(lines)


@dataclass(frozen=True)
class Handoff:
    """The holder a baton line names (one of ``_HOLDERS``), and the question an operator handoff asks.

    ``options`` are the answers listed under that question, as the role wrote
    them; an open question has none.
    """

    holder: str
    question: str = ""
    options: tuple[str, ...] = ()


def baton_lines(text: str) -> list[tuple[str, str]]:
    """Every ``→ <holder>[: <argument>]`` line of ``text``, in order.

    The holder is normalized, and "" when it is not one of the seven.
    """
    return [
        (_HOLDERS.get(match["owner"].casefold(), ""), (match["arg"] or "").strip())
        for match in map(_baton, str(text or "").splitlines())
        if match
    ]


def read_baton_line(text: str) -> Handoff | None:
    """The handoff a closing ``→ <holder>`` line names, or ``None`` when none can be read.

    Only the decision region is read: after the last ``Decision:``, else the
    last five non-empty lines above the choices an operator question lists,
    so an arrow quoted from the task cannot route the work. No line, an
    unknown holder or two lines that disagree is unreadable; nothing
    unreadable is ever ``done``. ``operator:`` with nothing after it asks, as
    written, the last line of prose above it (above ``Decision:`` when there
    is one) if it lists choices, and is unreadable otherwise. A list that
    cannot be read as choices leaves the question standing without them.
    """
    from .role_reply import _DECISION_FOOTER, decision_footer_text

    source = str(text or "")
    footer = decision_footer_text(source)
    lines = [line for line in footer.splitlines() if line.strip()]
    start = _choices_start(lines)
    lines, choices = lines[:start], tuple(map(_choice, lines[start:]))
    if footer == source:
        lines = lines[-5:]
    named = set(baton_lines("\n".join(lines)))
    if len(named) != 1:
        return None
    ((holder, argument),) = named
    if holder == "operator" and not argument and choices:
        prose = source[:len(source) - len(footer)] if footer != source else "\n".join(lines[:-1])
        argument = next((line.strip() for line in reversed(prose.splitlines())
                         if line.strip() and not _DECISION_FOOTER.match(line)), "")
    if not holder or (holder == "operator" and not argument):
        return None
    return Handoff(holder, argument[:500], choices) if holder == "operator" else Handoff(holder)


def strip_handoff(text: str) -> str:
    """The prose of a handoff, as a person reads it.

    The baton line with the choices listed under it and the ``Decision:``
    footer the host reads (``ACTION=hold``, …) go;
    a ``RESULT=`` line keeps its sentence, which was always written for the
    operator.
    """
    from .role_reply import _DECISION_FOOTER, decision_footer_text

    source = str(text or "")
    footer = decision_footer_text(source)
    if footer != source:
        source = "\n".join([source[: len(source) - len(footer)],
                            *(line for line in footer.splitlines() if _RESULT_KEY.match(line))])
    lines = _RESULT_KEY.sub("", source).splitlines()
    return "\n".join(
        line for line in lines[:_choices_start(lines)]
        if not (_baton(line) or _DECISION_FOOTER.match(line))
    ).strip()


__all__ = [
    "HANDOFF_STATUS",
    "LADDER",
    "OPERATOR_CHOICES_RULE",
    "Handoff",
    "baton_lines",
    "listed_choices",
    "read_baton_line",
    "strip_handoff",
]
