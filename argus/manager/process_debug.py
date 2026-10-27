"""The Manager's rung of the ladder: debugging a stuck process, not deciding the work.

When work climbs to the Manager (docs/role-handoff-design.md, "The baton",
invariant 3) the team is stuck. Two kinds of stuck have two owners:

- the work's content is stuck (an approach rejected, no viable idea): the
  Planner replans, and with no route left that fits what the person wants
  the question goes to the person;
- the process is stuck (the same error repeating, an environment, tool,
  credential or quota failure, unchanged work handed back and forth): the
  Manager debugs it.

At this rung the Manager gets a "process stuck" brief built from the event log
(what repeated, the errors, the last changes) and answers with a diagnosis and
one of three moves: fixed it, so the holder retries (``to=team``); the Planner
replans around the diagnosis (``to=planner``); or the person decides what only
they can (``to=operator``). It may not decide what the person wants, write the
plan, accept a candidate, or declare a stage or the project done; those stay
with the person, the Planner, the Reviewer and the completion gate.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

#: The moves the Manager has at its rung, by the supervise form's ``to``.
RUNG_MOVES = {"team": "fixed_and_retry", "planner": "to_planner", "operator": "to_operator"}

_ERROR_FIELDS = ("error", "error_message", "fatal_error", "failure_reason", "stop_reason", "last_error")
_CHANGE_TYPES = {
    "round.main.completed", "round.review.completed", "life.mission.completed",
    "life.planner.verdict", "life.planner.idle",
}
_TAIL_BYTES = 400_000


def is_process_stuck(event: dict[str, Any]) -> bool:
    """Whether ``event`` puts stuck work on the Manager's rung."""
    return bool(event.get("process_stuck"))


def _tail_events(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / "events.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines()[1 if size > _TAIL_BYTES else 0:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _short(text: Any, limit: int = 240) -> str:
    return " ".join(str(text or "").split())[:limit]


def process_stuck_brief(root: Path | str, event: dict[str, Any], *, limit: int = 6) -> str:
    """What the Manager reads at its rung: what repeated, the errors, the last changes."""
    item_id = str(event.get("item_id") or "")
    rows = [row for row in _tail_events(Path(root)) if not item_id or str(row.get("item_id") or "") == item_id]
    errors = [
        _short(row.get(field))
        for row in rows
        for field in _ERROR_FIELDS
        if str(row.get(field) or "").strip()
    ]
    repeated = [(text, count) for text, count in Counter(errors).most_common(3) if count > 1]
    changes = [
        f"- {row.get('type')}: {_short(row.get('summary') or row.get('reason') or row.get('text'))}"
        for row in rows if row.get("type") in _CHANGE_TYPES
    ][-limit:]
    lines = [
        "## Process stuck brief",
        f"Why it is yours: {_short(event.get('consult_reason') or event.get('reason'), 400) or 'the work climbed to you'}",
    ]
    if repeated:
        lines.append("What repeated:")
        lines.extend(f"- {count}x {text}" for text, count in repeated)
    if errors:
        lines.append("Errors, newest last:")
        lines.extend(f"- {text}" for text in list(dict.fromkeys(errors))[-limit:])
    if changes:
        lines.append("Last changes:")
        lines.extend(changes)
    lines.append(
        "Debug the process: read the events, logs, errors and role transcripts, find the cause, fix the "
        "environment or configuration within your permissions (a package, a path, a timeout, a backend or "
        "model per settings, a stuck role session), then answer with your diagnosis in note and one move: "
        "to=team (fixed: the holder retries), to=planner (the Planner replans around your diagnosis), or "
        "to=operator (only what needs the person: credentials, money, scope, an irreversible step). What the "
        "person wants, the plan itself, accepting a candidate and the stage are not yours to decide here."
    )
    return "\n".join(lines)


def rung_refusal(payload: dict[str, Any], *, operator_can_be_asked: bool) -> str:
    """Why the Manager's answer at its rung is not a process fix; "" when it is one."""
    to, note = payload["to"], payload.get("note", "").strip()
    if to not in RUNG_MOVES:
        return (
            "At this rung you debug the process: hand it to=team once fixed, to=planner with your "
            "diagnosis, or to=operator for what only they can decide."
        )
    if to in {"team", "planner"} and not note:
        return f"to={to} needs your diagnosis in note: the cause you found and what you fixed or what to plan around."
    if payload.get("assumptions") and operator_can_be_asked:
        return (
            "Which reading of the operator's words holds is theirs to decide, not yours at this rung: "
            "leave assumptions empty, or ask them with to=operator."
        )
    if "accept" in (payload.get("risk") or {}):
        return "Accepting a candidate's risk is not a process fix: leave risk out at this rung."
    return ""


def record_diagnosis(root: Path | str, record: dict[str, Any]) -> dict[str, Any]:
    """Write the Manager's diagnosis onto the stuck task, and resume it when the Manager fixed the cause.

    The diagnosis goes on the task's own record (its notes) and in the
    supervision receipt's effects, whose event names the task, so how the task
    was made shows it. ``to=team`` re-arms the parked task for its holder with the diagnosis
    to read; ``to=planner`` leaves it for the Planner to replan around.
    """
    from ..life.memory import Backlog

    root = Path(root)
    decision = record.get("decision") or {}
    item_id = str((record.get("trigger") or {}).get("item_id") or "")
    diagnosis = " ".join(str(decision.get("directive") or decision.get("reason") or "").split())
    move = RUNG_MOVES.get(str(decision.get("handoff") or ""), "")
    effects: dict[str, Any] = {"diagnosis": diagnosis, "move": move}
    if item_id and move == "fixed_and_retry":
        resumed = Backlog(root / "backlog.jsonl").resume_one(item_id, notes=f"Manager's diagnosis: {diagnosis}")
        effects["resumed_item_id"] = resumed.id if resumed is not None else ""
    return effects


__all__ = ["RUNG_MOVES", "is_process_stuck", "process_stuck_brief", "record_diagnosis", "rung_refusal"]
