"""Durable, idempotent background messages shown in the operator conversation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .transcript import append_turn
from ..life.event_log import JsonlEventSink


def publish_operator_message(
    life_dir: Path | str,
    *,
    text: str,
    message_id: str,
    event_fields: dict[str, Any] | None = None,
) -> bool:
    """Append one Argus transcript turn and matching live event exactly once."""
    if not append_turn(life_dir, "argus", text, message_id=message_id):
        return False
    event = {
        "type": "ui.argus",
        "agent_layer": "manager",
        "message_id": message_id,
        "text": text,
    }
    event.update(event_fields or {})
    JsonlEventSink(None, life_dir=Path(life_dir)).append(event)
    return True

