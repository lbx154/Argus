"""Whether the operator can be asked, and what was decided without asking.

Whether a question needs a person is the judgement of the role that raises
it: a role that needs the operator ends its turn with ``→ operator:
<question>`` (docs/role-handoff-design.md, "The baton").

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
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .mission_view import say

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


def render_autonomous_assumptions(
    state_root: Path | str, *, limit: int = 8, chinese: bool = False,
) -> str:
    rows = read_autonomous_assumptions(state_root)[-limit:]
    if not rows:
        return ""
    lines = [say("assumed_head", chinese)]
    for row in rows:
        conflict = str(row.get("conflict") or "").strip()[:300]
        reading = str(row.get("reading") or "").strip()[:300]
        verb = say("assumed_by_manager" if row.get("source") == "manager_decision" else "assumed_by_me", chinese)
        lines.append("- " + (
            say("assumed_choice", chinese, conflict=conflict, verb=verb, reading=reading)
            if reading and conflict
            else say("assumed_reading", chinese, verb=verb, reading=reading) if reading
            else say("assumed_conflict", chinese, conflict=conflict)
        ))
    return "\n".join(lines)


__all__ = [
    "OPERATOR_AVAILABLE_KNOB",
    "current_run_id",
    "operator_available",
    "operator_questions_can_be_asked",
    "read_autonomous_assumptions",
    "record_autonomous_assumption",
    "render_autonomous_assumptions",
    "start_autonomy_run",
]
