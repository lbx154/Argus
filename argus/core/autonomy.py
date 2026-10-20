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

The policy fails safe where it matters: an Engineer that explicitly hands a
question to the operator without a usable label reaches the operator while one
may be asked. Every other unclassified question stays with the team, as it
always did, so ordinary technical choices do not interrupt anyone.

Whether the operator can be asked at all is one switch,
``ARGUS_SKILL_OPERATOR_AVAILABLE``: ``--no-operator`` turns it off for one
run, and telling the front door not to ask (or to ask again) saves it. While
it is off the Manager holds the operator's authority: it settles questions of
interpretation itself and records the assumption, and it decides every parked
operator question in the operator's place, an action only the operator could
approve (spending, an irreversible or outward-facing step) included.
Credentials it does not hold stay out of reach.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model_visible_text import LABELLED_FIXTURE_CARVE_OUT

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
AUTONOMOUS_ASSUMPTIONS_FILENAME = "autonomous_assumptions.jsonl"
# How long Argus keeps retrying an outside failure on its own (a provider
# outage, a rate limit, an unreachable backend) before it stops retrying and
# tells the operator. Operator decision, 2026-10-19: three hours.
AUTOMATIC_RETRY_WINDOW_SECONDS = 3 * 3600.0
# The stop reason of a task whose automatic retries used the whole window. It
# waits for an explicit Run, and the circuit its park opens holds every other
# model call meanwhile (life/runtime_failure_circuit.py).
RETRY_WINDOW_SPENT_REASON = (
    "Automatic retries ran for three hours and the model service did not recover"
)

# Model-visible instruction for a question of meaning the operator would
# normally settle, in a run where the operator cannot be asked.
AUTONOMOUS_ASSUMPTION_INSTRUCTION = (
    "The operator cannot be asked about this, so do not wait for them. An "
    "assumption may settle only what a requirement means: choose the most "
    "defensible interpretation of the conflicting or unclear requirement within "
    "the stated objective and continue on it. An interpretation may settle "
    "ambiguity between requirements; it never drops or explains away an "
    "explicit requirement or reported symptom. Never assume facts, data, "
    "measurements, or results. Never substitute placeholder credentials or "
    "mocked services in what is delivered; if a required input is unavailable "
    "and the task does not say it arrives only at grading or deploy time, end "
    "the mission blocked and say what is missing. "
    + LABELLED_FIXTURE_CARVE_OUT
    + " Record the "
    "assumption and the conflict that forced it in CHECKPOINT.md and the run "
    "report, not inside machine-graded deliverables. An assumption never "
    "authorizes spending money, real credentials, or an irreversible or "
    "outward-facing action: ask for such an action, naming its operator need, "
    "and the Manager decides it in the operator's place."
)


@dataclass(frozen=True)
class OperatorIntervention:
    required: bool
    reason: str
    authority_impact: str
    operator_need: str = ""


def normalize_operator_need(value: Any) -> str:
    """Read a role's classification value.

    Returns one of :data:`OPERATOR_NEEDS`, :data:`NO_OPERATOR_NEED`, or ``""``
    when the value names no label at all (an unclassified question).
    """
    text = re.sub(r"[_\-\s]+", " ", str(value or "").strip().lower())
    match = _NEED_RE.search(text)
    return _NEED_ALIASES[match.group(1)] if match else ""


def operator_available(*, env: Mapping[str, str] | None = None) -> bool:
    """Whether the operator can be asked now: the one switch.

    Headless entry points (a benchmark harness, ``--no-operator``) declare
    ``ARGUS_SKILL_OPERATOR_AVAILABLE=false`` for their run; telling the front
    door not to ask saves it. The default is that someone can answer, because
    an interactive user who launched the run usually can.
    """
    from .knobs import _FALSE_VALUES, resolve_knob

    raw = resolve_knob(OPERATOR_AVAILABLE_KNOB, "true", env=env).value
    return str(raw or "").strip().lower() not in _FALSE_VALUES


def operator_questions_can_be_asked(state_root: Path | str | None = None) -> bool:
    """Whether an operator question raised now reaches someone who answers.

    The same answer for every project (``state_root`` is accepted for callers
    that have one). Whenever this is false the Manager holds the operator's
    authority and decides in their place, so a decision always has a decider.
    """
    del state_root
    return operator_available()


def assess_operator_intervention(
    *,
    question: str,
    reason: str = "",
    next_action: str = "",
    planner_report: Mapping[str, Any] | None = None,
    operator_need: str = "",
    unclassified_requires_operator: bool | None = None,
) -> OperatorIntervention:
    """Decide whether a question truly needs a person.

    The decision follows the raising role's own structured classification:
    an ``operator_need`` (from the Engineer or the Reviewer's decision
    request) or ``authority_impact`` (from a Reviewer replan). ``reason`` and
    ``next_action`` are context for callers and are never searched for words.

    A question nobody classified stays with the team, as it always has,
    except where the caller passes ``unclassified_requires_operator``: the
    Engineer's explicit ``→ operator`` hand-off without a usable
    label fails safe to the operator while one may be asked.
    """
    del reason, next_action  # prose is context, not a classification
    report = planner_report if isinstance(planner_report, Mapping) else {}
    authority = str(report.get("authority_impact") or "").strip().lower()
    need = normalize_operator_need(operator_need) or normalize_operator_need(
        report.get("operator_need")
    )

    def decision(required: bool, why: str) -> OperatorIntervention:
        return OperatorIntervention(
            required,
            why,
            authority,
            need if need in OPERATOR_NEEDS else "",
        )

    if not str(question or "").strip():
        return decision(False, "no operator question")
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
    if unclassified_requires_operator:
        return decision(
            True,
            "an explicit hand-off to the operator carries no classification, "
            "so it goes to the operator",
        )
    return decision(False, "reversible technical choice stays with Argus")


# --- what was decided without asking the operator, per run -------------------
RUN_ID_ENV = "ARGUS_AUTONOMY_RUN_ID"


def current_run_id() -> str:
    return str(os.environ.get(RUN_ID_ENV, "") or "")


def start_autonomy_run() -> str:
    """Begin a run scope: records written before it no longer appear."""
    import uuid

    run_id = uuid.uuid4().hex[:16]
    os.environ[RUN_ID_ENV] = run_id
    return run_id


def _append_row(path: Path, row: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_run_rows(path: Path) -> list[dict[str, Any]]:
    run_id = current_run_id()
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
        if isinstance(row, dict) and str(row.get("run_id") or "") == run_id:
            rows.append(row)
    return rows


def record_autonomous_assumption(
    state_root: Path | str,
    *,
    item_id: str,
    conflict: str = "",
    reading: str = "",
    source: str,
    key: str = "",
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Append one decision taken without asking the operator, for this run.

    ``conflict`` is what forced a decision; ``reading`` is what the team or the
    Manager actually chose, in its own words, when it has stated one.
    """
    row = {
        "ts": time.time(),
        "run_id": current_run_id(),
        "key": str(key or ""),
        "item_id": str(item_id or ""),
        "conflict": str(conflict or "").strip()[:1200],
        "reading": str(reading or "").strip()[:1200],
        "source": str(source or ""),
        "evidence_refs": [str(ref) for ref in evidence_refs if str(ref).strip()],
    }
    if not (row["conflict"] or row["reading"]):
        return row
    path = Path(state_root) / AUTONOMOUS_ASSUMPTIONS_FILENAME
    identity = row["key"] or (row["source"] + ":" + row["conflict"] + ":" + row["reading"])
    for previous in _read_run_rows(path):
        previous_identity = str(previous.get("key") or "") or (
            str(previous.get("source") or "")
            + ":" + str(previous.get("conflict") or "")
            + ":" + str(previous.get("reading") or "")
        )
        if previous_identity == identity:
            return previous
    _append_row(path, row)
    return row


def read_autonomous_assumptions(state_root: Path | str) -> list[dict[str, Any]]:
    return [
        row
        for row in _read_run_rows(Path(state_root) / AUTONOMOUS_ASSUMPTIONS_FILENAME)
        if str(row.get("conflict") or row.get("reading") or "").strip()
    ]


def render_autonomous_assumptions(state_root: Path | str, *, limit: int = 8) -> str:
    rows = read_autonomous_assumptions(state_root)[-limit:]
    if not rows:
        return ""
    lines = []
    for row in rows:
        conflict = str(row.get("conflict") or "").strip()
        reading = str(row.get("reading") or "").strip()
        verb = "Manager decided" if row.get("source") == "manager_decision" else "assumed"
        if reading and conflict:
            lines.append(f"- {conflict[:300]} -> {verb}: {reading[:300]}")
        elif reading:
            lines.append(f"- {verb}: {reading[:300]}")
        else:
            lines.append(f"- {conflict[:300]} (the chosen reading is in CHECKPOINT.md)")
    return "Decided without asking the operator (review these):\n" + "\n".join(lines)


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
    "NO_OPERATOR_NEED",
    "OPERATOR_ACTION_NEEDS",
    "OPERATOR_AVAILABLE_KNOB",
    "OPERATOR_NEEDS",
    "OperatorIntervention",
    "current_run_id",
    "assess_operator_intervention",
    "normalize_operator_need",
    "operator_available",
    "operator_questions_can_be_asked",
    "read_autonomous_assumptions",
    "record_autonomous_assumption",
    "render_autonomous_assumptions",
    "start_autonomy_run",
    "technical_continuation",
]
