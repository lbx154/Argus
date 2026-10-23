"""Team reports left for the Manager session's owner.

The project process never runs the Manager's session; it leaves a report in
the project's ``manager-deferred`` inbox, and the web server that owns the
session answers it as its next turn (``webapi.deferred_messages``).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


def write_record(path: Path, record: dict[str, Any]) -> None:
    """Keep the queue's disk format private and replace only complete records."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".message-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _team_report_path(life_dir: Path, message_id: str) -> Path:
    return life_dir / "manager-deferred" / f"{quote(message_id, safe='')}.json"


def team_report_left(life_dir: Path, message_id: str) -> bool:
    """Whether the team report ``message_id`` was left for the Manager (answered or not)."""
    return _team_report_path(life_dir, message_id).exists()


def post_team_report(life_dir: Path, *, message_id: str, text: str, event_fields: dict[str, Any]) -> None:
    """Leave a team report for the Manager session's owner, once per ``message_id``."""
    path = _team_report_path(life_dir, message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_record(path, {"status": "waiting", "created_at": time.time(), "request": {
            "sid": life_dir.name, "global_root": str(life_dir.parent.parent),
            "text": text, "turn_id": message_id, "team": event_fields,
        }})
