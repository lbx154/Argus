"""Durable single-record outbox for planner verdict delivery."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..core.atomic_replace import write_atomic

OUTBOX_FILE = "planner-verdict-outbox.json"
OUTBOX_VERSION = 1


def planner_verdict_delivery_id() -> str:
    """A fresh id for one verdict; the outbox keeps it across restarts."""
    return f"verdict-{uuid.uuid4().hex}"


def load_planner_verdict_outbox(root: Path | str) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            (Path(root) / OUTBOX_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != OUTBOX_VERSION
        or not isinstance(payload.get("event"), dict)
        or not str(payload.get("delivery_id") or "")
    ):
        return None
    return payload


def write_planner_verdict_outbox(
    root: Path | str,
    *,
    event: Mapping[str, Any],
    outcome: bool | str,
    terminal_signature: str = "",
    delivered: bool = False,
) -> dict[str, Any]:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    delivery_id = str(event.get("delivery_id") or planner_verdict_delivery_id())
    stored_event = dict(event)
    stored_event["delivery_id"] = delivery_id
    payload = {
        "version": OUTBOX_VERSION,
        "delivery_id": delivery_id,
        "event": stored_event,
        "outcome": outcome,
        "terminal_signature": str(terminal_signature or ""),
        "delivered": bool(delivered),
    }
    write_atomic(
        directory / OUTBOX_FILE,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        mode=0o600,
    )
    return payload


def mark_planner_verdict_delivered(
    root: Path | str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    return write_planner_verdict_outbox(
        root,
        event=dict(record["event"]),
        outcome=record["outcome"],
        terminal_signature=str(record.get("terminal_signature") or ""),
        delivered=True,
    )


def clear_planner_verdict_outbox(root: Path | str) -> None:
    try:
        (Path(root) / OUTBOX_FILE).unlink()
    except FileNotFoundError:
        pass


def planner_verdict_was_persisted(
    root: Path | str,
    delivery_id: str,
) -> bool:
    directory = Path(root)
    for path in sorted(directory.glob("events.jsonl*")):
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if delivery_id not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        event.get("type") == "life.planner.verdict"
                        and event.get("delivery_id") == delivery_id
                    ):
                        return True
        except OSError:
            continue
    return False


__all__ = [
    "OUTBOX_FILE",
    "clear_planner_verdict_outbox",
    "load_planner_verdict_outbox",
    "mark_planner_verdict_delivered",
    "planner_verdict_delivery_id",
    "planner_verdict_was_persisted",
    "write_planner_verdict_outbox",
]
