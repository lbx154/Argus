"""Pragmatic operator-intervention policy.

Argus should ask a person for choices that only a person can make, not for
recoverable engineering decisions. Which questions those are is a judgement,
and it belongs to the role that raises the question: the Engineer states an
``operator_need`` with its question and the Reviewer states an
``authority_impact``. This module only reads those structured answers. It does
not scan prose for words: a sentence such as "production credentials are
unnecessary" says nothing about whether the operator is needed, and a keyword
match on it parked a headless run for hours.

A run can also declare that no operator is available at all (headless and
benchmark runs). Then nobody is asked: the Manager settles the question itself,
records the assumption it made, and keeps going; only a need the team has no
way to meet on its own (real credentials, spending, an irreversible or
outward-facing action) ends the work as blocked, without a question.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

AUTONOMY_MODES = frozenset({"cautious", "pragmatic", "autonomous"})

# What a role may name as the reason only the operator can decide. Anything
# else, including an empty value, is a choice the team can make itself.
OPERATOR_NEEDS = frozenset(
    {
        "credentials",
        "spending",
        "irreversible_or_external",
        "scope_or_authority",
    }
)
# Needs the team cannot meet by choosing an interpretation: the action itself
# requires something only the operator holds or may approve.
OPERATOR_ACTION_NEEDS = frozenset(
    {"credentials", "spending", "irreversible_or_external"}
)

OPERATOR_AVAILABLE_KNOB = "ARGUS_SKILL_OPERATOR_AVAILABLE"
BOUNDED_OPERATOR_WAIT_EXIT_KNOB = "ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN"
BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES = 30.0

# Model-visible instruction for a decision the operator would normally make,
# in a run that has no operator to make it.
AUTONOMOUS_ASSUMPTION_INSTRUCTION = (
    "No operator is available to answer this. Do not wait for one. Choose the "
    "most defensible interpretation of the conflicting or unclear requirement "
    "within the stated objective, and continue on it. State it as an explicit "
    "assumption, and record the assumption together with the conflict that "
    "forced it in the deliverable or its report, so a reader can see what was "
    "assumed and why. Do not spend money, use real credentials, or take "
    "irreversible or outward-facing actions on that assumption."
)


@dataclass(frozen=True)
class OperatorIntervention:
    required: bool
    mode: str
    reason: str
    authority_impact: str
    operator_need: str = ""


def normalize_autonomy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in AUTONOMY_MODES else "pragmatic"


def normalize_operator_need(value: Any) -> str:
    """The role's own classification, or ``""`` when it named none we know."""
    need = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return need if need in OPERATOR_NEEDS else ""


def resolve_autonomy_mode(*, env: Mapping[str, str] | None = None) -> str:
    """Resolve the operator-facing autonomy mode through the normal knob layer."""
    from .knobs import resolve_knob

    return normalize_autonomy_mode(
        resolve_knob(
            "ARGUS_SKILL_AUTONOMY_MODE",
            "pragmatic",
            env=env,
        ).value
    )


def operator_available(*, env: Mapping[str, str] | None = None) -> bool:
    """Whether this run has an operator who can answer questions.

    Headless entry points (a benchmark harness, ``--no-operator``) declare
    ``ARGUS_SKILL_OPERATOR_AVAILABLE=false``. The default is that someone can
    answer, because an interactive user who launched the run usually can.
    """
    from .knobs import _FALSE_VALUES, resolve_knob

    raw = resolve_knob(OPERATOR_AVAILABLE_KNOB, "true", env=env).value
    return str(raw or "").strip().lower() not in _FALSE_VALUES


def bounded_operator_wait_exit_seconds(
    *, env: Mapping[str, str] | None = None
) -> float:
    """How long a bounded run with only an operator question left may wait.

    When no operator is available nobody can answer, so there is no grace at
    all. Otherwise the knob (minutes) applies; 0 or less waits indefinitely,
    which is the behaviour an operator who wants it can still choose.
    """
    if not operator_available(env=env):
        return 0.0
    from .knobs import resolve_knob

    raw = resolve_knob(
        BOUNDED_OPERATOR_WAIT_EXIT_KNOB,
        str(BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES),
        env=env,
    ).value
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        minutes = BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES
    if minutes != minutes:  # NaN
        minutes = BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES
    if minutes <= 0:
        return -1.0
    return minutes * 60.0


def assess_operator_intervention(
    *,
    question: str,
    reason: str = "",
    next_action: str = "",
    planner_report: Mapping[str, Any] | None = None,
    mode: str | None = None,
    operator_need: str = "",
) -> OperatorIntervention:
    """Decide whether a blocked/replan question truly needs a person.

    The decision follows the raising role's own structured classification:
    ``authority_impact == "operator"`` from the Reviewer or an
    ``operator_need`` from the Engineer. ``reason`` and ``next_action`` are
    context for callers and are never searched for words. ``cautious`` mode
    asks on every explicit question because the operator chose that.
    """
    del reason, next_action  # prose is context, not a classification
    selected_mode = normalize_autonomy_mode(mode or resolve_autonomy_mode())
    report = planner_report if isinstance(planner_report, Mapping) else {}
    authority = str(report.get("authority_impact") or "").strip().lower()
    need = normalize_operator_need(operator_need or report.get("operator_need"))

    if not str(question or "").strip():
        return OperatorIntervention(False, selected_mode, "no operator question", authority, need)
    if selected_mode == "cautious":
        return OperatorIntervention(
            True, selected_mode, "cautious mode asks on every explicit question", authority, need
        )
    if need:
        return OperatorIntervention(
            True,
            selected_mode,
            f"the raising role classified this as needing the operator ({need})",
            authority,
            need,
        )
    if authority == "operator":
        return OperatorIntervention(
            True, selected_mode, "Reviewer marked an operator-owned decision", authority, need
        )
    if authority in {"technical", "manager_contract"}:
        return OperatorIntervention(
            False, selected_mode, "technical or Manager-owned choice is recoverable", authority, need
        )
    return OperatorIntervention(
        False,
        selected_mode,
        "the raising role did not classify this as needing the operator",
        authority,
        need,
    )


def autonomous_operator_resolution(operator_need: Any) -> str:
    """How a run without an operator settles a question that needed one.

    ``"blocked"`` when the need is an action only the operator can enable;
    ``"assume"`` otherwise, meaning the Manager picks the most defensible
    interpretation, records it, and continues.
    """
    return (
        "blocked"
        if normalize_operator_need(operator_need) in OPERATOR_ACTION_NEEDS
        else "assume"
    )


def technical_continuation(
    *,
    question: str,
    reason: str = "",
    next_action: str = "",
) -> str:
    """Turn a non-operator blocker into a concrete Planner instruction."""
    action = str(next_action or "").strip()
    if action:
        return action
    why = str(reason or question or "the current route stalled").strip()
    return (
        "Replan this as a reversible technical problem. Diagnose the current "
        f"failure ({why}), try the smallest informative check first, and choose "
        "a different in-scope route without waiting for operator confirmation."
    )


__all__ = [
    "AUTONOMOUS_ASSUMPTION_INSTRUCTION",
    "AUTONOMY_MODES",
    "BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES",
    "BOUNDED_OPERATOR_WAIT_EXIT_KNOB",
    "OPERATOR_ACTION_NEEDS",
    "OPERATOR_AVAILABLE_KNOB",
    "OPERATOR_NEEDS",
    "OperatorIntervention",
    "assess_operator_intervention",
    "autonomous_operator_resolution",
    "bounded_operator_wait_exit_seconds",
    "normalize_autonomy_mode",
    "normalize_operator_need",
    "operator_available",
    "resolve_autonomy_mode",
    "technical_continuation",
]
