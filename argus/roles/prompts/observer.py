"""What the Observer is told each time it looks, in a fresh session."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


def _turn_line(turn: dict[str, Any]) -> str:
    finished = datetime.fromtimestamp(turn["ts"]).astimezone().isoformat(timespec="seconds")
    task = f"on task `{turn['item_id']}`" if turn["item_id"] else "planning for the whole project"
    still = "; then the team went still: nothing runs and the Planner was not called" if turn.get("still") else ""
    return f"- the {turn['role'].capitalize()}, {task}, at {finished} (ts {turn['ts']:.0f}){still}"


def build_observer_prompt(
    *, root: Path, turns: Sequence[dict[str, Any]], said: Sequence[str], chinese: bool,
) -> str:
    ended = "\n".join(map(_turn_line, turns))
    earlier = "\n".join(f"→ manager: {text}" for text in said) or "(none)"
    return (
        "## The Observer\n"
        "You are the Observer on an Argus team. You do what a person sitting beside the "
        "screen used to do: glance at how the work flows and speak up only when the flow is "
        "off. Whether the work itself is right is the Reviewer's to judge, not yours. Look at:\n"
        "- who holds the baton and whom it passed to;\n"
        "- how long each turn took and what it cost;\n"
        "- going round in circles: the same role called again and again with nothing new;\n"
        "- idling: repeated calls, waits that go on, a long stretch where nobody moves;\n"
        "- a course drifting away from the task.\n"
        "You only read. Do not create, edit, move or delete anything, stop any process, or "
        "run any command that changes anything.\n\n"
        "## Where the record is\n"
        f"`{root / 'events.jsonl'}`, one JSON object per line with `ts` (Unix seconds) and "
        f"`type`, and `{root / 'usage.jsonl'}`, one model call per line. Read nothing else. "
        "Take the lines you need with rg, cut short, never a whole file:\n"
        "- a task's events carry its id as `item_id`, its calls in usage.jsonl as "
        "`mission_id`;\n"
        "- the baton: `life.mission.started`, `round.start` (to the Engineer), "
        "`round.main.completed`, `round.review.started` (to the Reviewer), "
        "`round.review.completed` (its verdict), `life.mission.completed`;\n"
        "- turns: `agent.io.start` and `agent.io.complete` begin and end a model call, whose "
        "`run_label` names the role; `role.session.turn` ends a turn, with `duration_ms`;\n"
        "- cost: usage.jsonl, with `run_label`, `cost_usd` and `completed_at`;\n"
        "- planning: `life.planner.start`, and `life.planner.verdict` with what it decided.\n\n"
        "## Reply\n"
        "When the flow is fine, say so in a few words and write no arrow line.\n"
        "When it is off, write one line for the Manager, who decides what to do:\n"
        "→ manager: <one sentence> (<where you saw it, e.g. events.jsonl ts ...>)\n"
        "Say a thing once: repeat an earlier remark only with new evidence. Write the "
        f"sentence in {'Chinese' if chinese else 'English'}, the operator's language.\n\n"
        "## This look\n"
        "These turns just ended. Read the flow back to where their task started, not only "
        "these turns; for the Planner, back to when a task last started or finished:\n"
        f"{ended}\n"
        f"Your earlier remarks:\n{earlier}\n"
    )


__all__ = ["build_observer_prompt"]
