"""What the team tells the operator, and the decisions it puts to the Manager.

A result, failure or question goes straight into the project's chat when it
happens; the Manager reads what was said since its last turn at the start of
its next one. A decision only the Manager makes is pushed to the web server
that owns the Manager's session (``manager-endpoint.json``), which runs the
turn at once; with no server it waits for the Manager's next turn.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .transcript import read_turns

log = logging.getLogger(__name__)

_ENDPOINT = "manager-endpoint.json"
_READ_THROUGH = "manager-read-through.json"
_CONTEXT_LIMIT = 5


def _write_json(path: Path, record: dict[str, Any], mode: int = 0o644) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


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


def publish_manager_endpoint(global_root: Path | str, *, url: str, token: str) -> None:
    """Where a project process reaches the Manager's session owner; only they read it."""
    path = Path(global_root) / _ENDPOINT
    if _read_json(path) != {"url": url, "token": token}:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, {"url": url, "token": token}, mode=0o600)


def push_to_manager(life_dir: Path | str, *, message_id: str, text: str) -> bool:
    """Have the Manager's session owner run this turn now; False when no owner answers."""
    life_dir = Path(life_dir)
    endpoint = _read_json(life_dir.parent.parent / _ENDPOINT)
    if not endpoint.get("url"):
        return False
    request = urllib.request.Request(
        f"{str(endpoint['url']).rstrip('/')}/api/projects/{quote(life_dir.name, safe='')}/manager/team-turn",
        data=json.dumps({"message_id": message_id, "text": text}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {endpoint['token']}"} if endpoint.get("token") else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - the local owner
            return 200 <= response.status < 300
    except OSError:
        log.info("Manager session owner unreachable; %s waits for its next turn", message_id, exc_info=True)
        return False
