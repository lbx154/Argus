"""Pragmatic operator-intervention policy.

Argus should ask a person for choices that only a person can make, not for
recoverable engineering decisions. Which questions those are is a judgement,
and it belongs to the role that raises the question: the Engineer states an
``operator_need`` with its question, and the Reviewer states one with a
decision request (or an ``authority_impact`` with a replan). This module reads
those structured answers. It does not scan question prose for words: a
sentence such as "production credentials are unnecessary" says nothing about
whether the operator is needed, and a keyword match on it parked a headless run
for hours.

The policy fails safe. When an operator is available, a question its raiser
did not classify goes to the operator. Only a run that declared no operator
available (``--no-operator``) treats an unclassified question as the team's
own: there nobody would answer it. In that mode the Manager settles questions
of interpretation itself and records the assumption; a need for an action only
the operator can enable (real credentials, spending, an irreversible or
outward-facing step) ends the work as blocked, without a question.

One deterministic check remains, :func:`operator_only_action`, and it is
deliberately about *actions*, not questions: before a plan alternative replaces
the current plan, an alternative that proposes force-pushing, publishing or
deploying, deleting data outside the workspace, spending money, or using
production credentials goes to the operator (or blocks when there is none),
whatever label it carries. A mislabeled replan can otherwise turn a
"technical" alternative into an irreversible act with nobody asked; the cost of
a false positive is one question, the cost of a false negative is not
recoverable. That asymmetry is why this is the one exception to "no
deterministic gates over judgement", and why it stays narrow.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

AUTONOMY_MODES = frozenset({"cautious", "pragmatic", "autonomous"})

# What a role may name as the reason only the operator can decide.
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
# The explicit "this does not need the operator" answer.
NO_OPERATOR_NEED = "none"

# Label vocabulary, read from a short classification value (never from prose).
# The earliest label in the value wins, so "credentials|spending" and
# "credentials (staging deploy key)" both read as credentials.
_NEED_LABELS: tuple[tuple[str, str], ...] = (
    ("credentials", "credentials"),
    ("credential", "credentials"),
    ("secrets", "credentials"),
    ("secret", "credentials"),
    ("spending", "spending"),
    ("spend", "spending"),
    ("money", "spending"),
    ("budget", "spending"),
    ("irreversible", "irreversible_or_external"),
    ("external", "irreversible_or_external"),
    ("scope", "scope_or_authority"),
    ("authority", "scope_or_authority"),
    ("none", NO_OPERATOR_NEED),
    ("technical", NO_OPERATOR_NEED),
)
_NEED_RE = re.compile(
    r"(?<![a-z])("
    + "|".join(re.escape(label) for label, _ in _NEED_LABELS)
    + r")(?![a-z])"
)
_NEED_ALIASES = dict(_NEED_LABELS)

OPERATOR_AVAILABLE_KNOB = "ARGUS_SKILL_OPERATOR_AVAILABLE"
BOUNDED_OPERATOR_WAIT_EXIT_KNOB = "ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN"
BOUNDED_OPERATOR_WAIT_EXIT_DEFAULT_MINUTES = 30.0
OPERATOR_AVAILABILITY_FILENAME = "operator_availability.json"
AUTONOMOUS_ASSUMPTIONS_FILENAME = "autonomous_assumptions.jsonl"
OPERATOR_WAIT_EXIT_FILENAME = "operator_wait_exit.json"

# Model-visible instruction for a decision the operator would normally make,
# in a run that has no operator to make it.
AUTONOMOUS_ASSUMPTION_INSTRUCTION = (
    "No operator is available to answer this, so do not wait for one. An "
    "assumption may settle only what a requirement means: choose the most "
    "defensible interpretation of the conflicting or unclear requirement within "
    "the stated objective and continue on it. Never assume facts, data, "
    "measurements, or results. Never substitute placeholder credentials or "
    "mocked services for something the task requires; if a required input is "
    "unavailable, end the mission blocked and say what is missing. Record the "
    "assumption and the conflict that forced it in CHECKPOINT.md and the run "
    "report, not inside machine-graded deliverables. Do not spend money, use "
    "real credentials, or take irreversible or outward-facing actions on it."
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
    """Read a role's classification value.

    Returns one of :data:`OPERATOR_NEEDS`, :data:`NO_OPERATOR_NEED`, or ``""``
    when the value names no label at all (an unclassified question).
    """
    text = re.sub(r"[_\-\s]+", " ", str(value or "").strip().lower())
    match = _NEED_RE.search(text)
    return _NEED_ALIASES[match.group(1)] if match else ""


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


def persist_operator_availability(life_dir: Path | str) -> None:
    """Record this run's declaration so ``--resume`` and respawns keep it."""
    explicit = str(os.environ.get(OPERATOR_AVAILABLE_KNOB, "") or "").strip()
    if not explicit:
        return
    path = Path(life_dir) / OPERATOR_AVAILABILITY_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"available": operator_available(), "updated_at": time.time()})
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def adopt_persisted_operator_availability(life_dir: Path | str) -> bool | None:
    """Apply a project's recorded declaration unless this process set one.

    An explicit environment value always wins; otherwise a project that was
    started with ``--no-operator`` stays without an operator when resumed.
    """
    if str(os.environ.get(OPERATOR_AVAILABLE_KNOB, "") or "").strip():
        return None
    try:
        payload = json.loads(
            (Path(life_dir) / OPERATOR_AVAILABILITY_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or "available" not in payload:
        return None
    available = bool(payload.get("available"))
    if not available:
        os.environ[OPERATOR_AVAILABLE_KNOB] = "false"
    return available


def bounded_operator_wait_exit_seconds(
    *, env: Mapping[str, str] | None = None
) -> float:
    """How long a bounded run with only an operator question left may wait.

    When no operator is available nobody can answer, so there is no grace at
    all. Otherwise the knob (minutes) applies; 0 or less waits indefinitely.
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
    unclassified_requires_operator: bool | None = None,
) -> OperatorIntervention:
    """Decide whether a question truly needs a person.

    The decision follows the raising role's own structured classification:
    an ``operator_need`` (from the Engineer or the Reviewer's decision
    request) or ``authority_impact`` (from a Reviewer replan). ``reason`` and
    ``next_action`` are context for callers and are never searched for words.

    A question nobody classified fails safe: it needs the operator whenever
    one is available. ``unclassified_requires_operator`` overrides that only
    for callers that merely describe an outcome rather than route a question.
    ``cautious`` mode asks on every explicit question because the operator
    chose that.
    """
    del reason, next_action  # prose is context, not a classification
    selected_mode = normalize_autonomy_mode(mode or resolve_autonomy_mode())
    report = planner_report if isinstance(planner_report, Mapping) else {}
    authority = str(report.get("authority_impact") or "").strip().lower()
    need = normalize_operator_need(operator_need) or normalize_operator_need(
        report.get("operator_need")
    )

    def decision(required: bool, why: str) -> OperatorIntervention:
        return OperatorIntervention(
            required,
            selected_mode,
            why,
            authority,
            need if need in OPERATOR_NEEDS else "",
        )

    if not str(question or "").strip():
        return decision(False, "no operator question")
    if selected_mode == "cautious":
        return decision(True, "cautious mode asks on every explicit question")
    if need in OPERATOR_NEEDS:
        return decision(
            True, f"the raising role classified this as needing the operator ({need})"
        )
    if need == NO_OPERATOR_NEED:
        return decision(False, "the raising role classified this as the team's decision")
    if authority == "operator":
        return decision(True, "Reviewer marked an operator-owned decision")
    if authority in {"technical", "manager_contract"}:
        return decision(False, "technical or Manager-owned choice is recoverable")
    fail_safe = (
        operator_available()
        if unclassified_requires_operator is None
        else bool(unclassified_requires_operator)
    )
    if fail_safe:
        return decision(
            True,
            "the question carries no classification, so it goes to the operator",
        )
    return decision(
        False,
        "the question carries no classification and no operator is available",
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


# --- the one action-level backstop -------------------------------------------
_NEGATION_BEFORE = re.compile(
    r"\b(?:not|no|never|without|avoid|avoiding|don't|do not|must not|instead of)"
    r"(?:\W+\w+){0,3}\W*$"
)
_NEGATION_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,3}(?:is |are )?(?:unnecessary|not needed|not required)\b"
)
_OPERATOR_ONLY_ACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "irreversible_or_external",
        re.compile(
            r"\bforce[- ]?push\w*|\bpush\s+(?:-f|--force)\b|"
            r"\brewrit\w*\s+(?:the\s+)?(?:protected|published|shared|main|release)\s+"
            r"(?:\w+\s+)?history\b"
        ),
    ),
    (
        "irreversible_or_external",
        re.compile(
            r"\b(?:publish(?:es|ing)?|releas\w+\s+(?:it\s+|the\s+\w+\s+)?(?:publicly|to\s+"
            r"(?:pypi|npm|production|the\s+public|users))|deploy\w*\s+(?:it\s+)?to\s+"
            r"(?:production|prod|staging|the\s+live)|deploy\w*\s+(?:the\s+\w+\s+)?"
            r"(?:to\s+)?production|submit\w*\s+(?:it\s+)?to\s+the\s+(?:venue|journal|"
            r"conference))\b"
        ),
    ),
    (
        "irreversible_or_external",
        re.compile(
            r"\b(?:delet\w*|overwrit\w*|drop\w*|wip\w*|truncat\w*|rm\s+-rf)\b[^.;\n]{0,60}"
            r"\b(?:outside\s+the\s+workspace|production|operator(?:'s)?\s+data|"
            r"user\s+data|customer\s+data|shared\s+(?:data|storage|bucket)|"
            r"the\s+(?:database|bucket))\b"
        ),
    ),
    (
        "spending",
        re.compile(
            r"\b(?:purchas\w*|buy(?:ing)?|pay(?:ing)?\s+for|spend\w*\s+(?:more\s+)?"
            r"(?:money|budget|\$)|increas\w*\s+(?:the\s+)?budget|paid\s+(?:tier|plan|"
            r"api|service))\b"
        ),
    ),
    (
        "credentials",
        re.compile(
            r"\b(?:production|prod|operator(?:'s)?|real|live)\s+(?:api\s+)?"
            r"(?:credentials?|keys?|tokens?|secrets?|passwords?)\b"
        ),
    ),
)


def operator_only_action(text: Any) -> str:
    """The operator-only need a proposed *action* implies, or ``""``.

    Applied only to a plan alternative about to replace the plan, never to a
    question. A negated mention ("do not publish", "production credentials
    are unnecessary") is not a proposal. See the module docstring for why this
    narrow check exists.
    """
    lowered = str(text or "").lower()
    if not lowered.strip():
        return ""
    for need, pattern in _OPERATOR_ONLY_ACTIONS:
        for match in pattern.finditer(lowered):
            before = lowered[max(0, match.start() - 40): match.start()]
            after = lowered[match.end(): match.end() + 40]
            if _NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(after):
                continue
            return need
    return ""


# --- assumptions recorded when nobody could be asked -------------------------
def record_autonomous_assumption(
    state_root: Path | str,
    *,
    item_id: str,
    conflict: str,
    source: str,
    key: str = "",
) -> dict[str, Any]:
    """Append one decision the team settled without an operator."""
    row = {
        "ts": time.time(),
        "key": str(key or ""),
        "item_id": str(item_id or ""),
        "conflict": str(conflict or "").strip()[:1200],
        "source": str(source or ""),
        "resolution": (
            "settled on the most defensible interpretation; the chosen reading is "
            "recorded in CHECKPOINT.md and the run report"
        ),
    }
    path = Path(state_root) / AUTONOMOUS_ASSUMPTIONS_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


def read_autonomous_assumptions(state_root: Path | str) -> list[dict[str, Any]]:
    path = Path(state_root) / AUTONOMOUS_ASSUMPTIONS_FILENAME
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("conflict") or "").strip():
            rows.append(row)
    return rows


def render_autonomous_assumptions(state_root: Path | str, *, limit: int = 8) -> str:
    rows = read_autonomous_assumptions(state_root)
    if not rows:
        return ""
    shown = rows[-limit:]
    lines = [
        f"- {str(row.get('conflict') or '')[:300]}" for row in shown
    ]
    return "Decided without an operator (assumptions to review):\n" + "\n".join(lines)


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
    "NO_OPERATOR_NEED",
    "OPERATOR_ACTION_NEEDS",
    "OPERATOR_AVAILABLE_KNOB",
    "OPERATOR_NEEDS",
    "OperatorIntervention",
    "adopt_persisted_operator_availability",
    "assess_operator_intervention",
    "autonomous_operator_resolution",
    "bounded_operator_wait_exit_seconds",
    "normalize_autonomy_mode",
    "normalize_operator_need",
    "operator_available",
    "operator_only_action",
    "persist_operator_availability",
    "read_autonomous_assumptions",
    "record_autonomous_assumption",
    "render_autonomous_assumptions",
    "resolve_autonomy_mode",
    "technical_continuation",
]
