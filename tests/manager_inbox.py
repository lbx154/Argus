"""Read what the team told the operator in a project's chat."""
from __future__ import annotations

import json
from pathlib import Path


def team_reports(root: Path) -> list[dict]:
    """The team's messages in ``root``'s chat, oldest first: the text and its card fields."""
    path = Path(root) / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [event for event in events if event.get("type") == "ui.argus" and event.get("team_message")]
