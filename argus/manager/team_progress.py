"""What Argus recorded about the team's running work, told with an operator's message.

While the team works, the operator asks how it is going or nudges it on. The
Manager answers from the record: the milestones the Mission View computes
from ``events.jsonl`` (rounds, the Reviewer's verdicts in its own words,
restarts and holds) and the files written in the workspace since the
operator's previous message. Nothing here judges the work; it is what was
recorded, with times (2026-10-29: asked "继续做…" after an hour, the Manager
said only that it would keep going, while the record held a review that had
sent round 1 back and a third round running).
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Sequence

_IMAGE = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|pdf)$", re.IGNORECASE)
_SINCE_LINES = 12
_FILES_SHOWN = 12
_VERDICTS = {
    "done": "accepted the round",
    "engineer": "sent it back to the Engineer for another round",
    "planner": "asked the Planner for a new plan",
    "operator": "left a decision to the operator",
    "manager": "found the work stuck and handed it to the Manager",
    "blocked": "found the work stuck and handed it to the Manager",
    "no_progress": "saw no progress",
    "skipped": "did not review this round",
}


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _ago(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _verdict(row: dict[str, Any]) -> str:
    who = "the Engineer's own check" if str(row.get("source") or "").startswith("engineer") else "the Reviewer"
    said = _VERDICTS.get(str(row.get("status") or ""), "asked for another pass")
    reason = " ".join(str(row.get("reason") or "").split())
    return f"{who} {said}" + (f": \"{reason}\"" if reason else "")


def _line(row: dict[str, Any], titles: dict[str, str]) -> str:
    kind = row.get("kind")
    number = row.get("round")
    if kind == "task_started":
        return f"task {row.get('item_id')} started: {row.get('title') or titles.get(row.get('item_id'), '')}"
    if kind == "task_requeued":
        return f"task {row.get('item_id')}'s run stopped before it finished ({row.get('error')}); it started again"
    if kind == "round_started":
        return f"round {number} started"
    if kind == "round_finished":
        return f"round {number} {'failed' if row.get('failed') else 'finished and was handed over'}"
    if kind == "review":
        return f"review of round {number}: {_verdict(row)}"
    if kind == "files":
        return f"round {number} wrote " + ", ".join(row.get("paths") or ())
    said = " ".join(str(row.get("text") or row.get("reason") or "").split())
    return f"work stopped ({row.get('type')})" + (f": {said}" if said else "")


def _previous_operator_ts(life_dir: Path, body: str, now: float) -> float | None:
    """When the operator wrote before ``body``. The web writes ``body``'s own transcript row
    just before the turn, and the turn's message carries it with context around it."""
    from ..core.transcript import read_turns

    turns = [turn for turn in read_turns(life_dir) if turn.get("role") == "operator"]
    if turns and now - float(turns[-1].get("ts") or 0) < 120 and str(turns[-1].get("text") or "").strip() in body:
        turns = turns[:-1]
    return float(turns[-1].get("ts") or 0) or None if turns else None


def team_progress(
    life_dir: Path | str, workspace: Path | str | None, items: Sequence[Any], body: str, *,
    now: float | None = None,
) -> str:
    """The record of the running ``items`` as lines for the Manager, or ``""``."""
    from ..core.mission_view import load_mission_view
    from ..core.workspace_facts import files_written_since

    if not items:
        return ""
    life_dir = Path(life_dir)
    now = time.time() if now is None else now
    ids = {str(item.id) for item in items}
    titles = {str(item.id): str(item.title or "") for item in items}
    try:
        milestones = load_mission_view(life_dir).get("milestones") or ()
    except (OSError, RuntimeError, ValueError):  # an unreadable log leaves the facts out, never the turn
        return ""
    rows = [row for row in milestones if row.get("item_id") in ids]
    lines = [
        "What Argus recorded about this work; a status question or a nudge is answered with these "
        f"facts (team-handoff rules). Host time, now {_clock(now)}:"
    ]
    for item in items:
        own = [row for row in rows if row.get("item_id") == str(item.id)]
        starts = [float(row["ts"]) for row in own if row.get("kind") == "task_started"]
        started = starts[0] if starts else float(item.started_ts or 0) or None
        state = f"- task {item.id}, {item.title}:"
        if started:
            state += f" started {_clock(started)}, {_ago(now - started)} ago;"
        rounds = [row for row in own if row.get("kind") in {"round_started", "round_finished"}]
        if rounds and rounds[-1].get("kind") == "round_started":
            state += f" round {rounds[-1].get('round')} running since {_clock(rounds[-1]['ts'])} ({_ago(now - rounds[-1]['ts'])})."
        elif rounds:
            state += f" round {rounds[-1].get('round')} finished at {_clock(rounds[-1]['ts'])}; the next step has not started."
        lines.append(state.rstrip(";"))
        reviews = [row for row in own if row.get("kind") == "review"]
        if reviews:
            lines.append(f"  latest review ({_clock(reviews[-1]['ts'])}, round {reviews[-1].get('round')}): {_verdict(reviews[-1])}")
        else:
            lines.append("  no review of this task yet")
    since = _previous_operator_ts(life_dir, body, now) or (min(float(item.started_ts or now) for item in items))
    lines.append(f"Since the operator's previous message ({_clock(since)}, {_ago(now - since)} ago):")
    restarts = [row for row in rows if row.get("kind") == "task_requeued"]
    recent = [
        row for row in rows
        if float(row.get("ts") or 0) >= since
        # The workspace says which files changed; without it the rounds' writes do.
        and (row.get("kind") != "files" or not workspace)
        # A restart already says the task and its round started again.
        and not (row.get("kind") in {"task_started", "round_started"} and any(
            restart.get("item_id") == row.get("item_id") and 0 <= row["ts"] - restart["ts"] <= 60
            for restart in restarts))
    ]
    lines.extend(f"- {_clock(row['ts'])} {_line(row, titles)}" for row in recent[-_SINCE_LINES:])
    if not recent:
        lines.append("- no milestone recorded")
    if workspace:
        try:
            files, count, partial = files_written_since(workspace, since, limit=_FILES_SHOWN)
        except OSError:
            files, count, partial = [], 0, False
        if files:
            named = ", ".join(f"{path} (figure)" if _IMAGE.search(path) else path for path, _mtime in files)
            total = f"{count}{'+' if partial else ''}"
            lines.append(f"Files written in the workspace since then, newest first ({len(files)} of {total}): {named}")
        else:
            lines.append("No file in the workspace changed since then.")
    return "\n".join(lines)


__all__ = ["team_progress"]
