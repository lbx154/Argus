"""Read the team reports a project process leaves for the Manager session's owner."""
from __future__ import annotations

import json
from pathlib import Path


def team_reports(root: Path) -> list[dict]:
    """The team reports left in ``root``'s Manager inbox, oldest first: the text and its card fields."""
    records = sorted((json.loads(path.read_text(encoding="utf-8"))
                      for path in (Path(root) / "manager-deferred").glob("*.json")),
                     key=lambda record: record["created_at"])
    return [{"text": r["request"]["text"], "message_id": r["request"]["turn_id"], **r["request"]["team"]}
            for r in records]
