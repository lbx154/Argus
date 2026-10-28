"""What the team tells the operator.

A result, failure or question goes straight into the project's chat when it
happens; the Manager reads what was said since its last turn at the start of
its next one. No team decision waits on a Manager turn: stages move through
the completion gate, and a stuck process reaches the Manager's supervision.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .atomic_replace import write_atomic
from .transcript import read_turns

log = logging.getLogger(__name__)

_READ_THROUGH = "manager-read-through.json"
_CONTEXT_LIMIT = 5


def _write_json(path: Path, record: dict[str, Any], mode: int = 0o644) -> None:
    write_atomic(path, json.dumps(record), mode=mode)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def show_team_message(life_dir: Path | str, *, text: str, message_id: str, event_fields: dict[str, Any]) -> bool:
    """Put what the team has to say in the project's chat now, once per ``message_id``."""
    from .operator_messages import publish_operator_message

    return publish_operator_message(
        life_dir, text=text, message_id=message_id, event_fields={**event_fields, "team_message": True},
    )


def team_message_told(life_dir: Path | str, message_id: str) -> bool:
    return any(turn.get("message_id") == message_id for turn in read_turns(life_dir))


def unread_team_messages(life_dir: Path | str) -> tuple[str, float]:
    """What the team told the operator since the Manager's last turn, and how far that reads."""
    through = float(_read_json(Path(life_dir) / _READ_THROUGH).get("through") or 0)
    rows = [turn for turn in read_turns(life_dir)
            if turn.get("team_message") and float(turn.get("ts") or 0) > through][-_CONTEXT_LIMIT:]
    if not rows:
        return "", through
    return ("The team told the operator this in the chat since your last turn (for your context):\n\n"
            + "\n\n".join(str(turn.get("text") or "") for turn in rows)), max(float(turn["ts"]) for turn in rows)


def mark_team_messages_read(life_dir: Path | str, through: float) -> None:
    path = Path(life_dir) / _READ_THROUGH
    if through > float(_read_json(path).get("through") or 0):
        _write_json(path, {"through": through})
