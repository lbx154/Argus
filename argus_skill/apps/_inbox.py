"""Shared inbox helpers for operator guidance.

The CLI, Web API, and cockpit all need the same inbox semantics:

* queue guidance to ``inbox.jsonl``
* emit a structured ``life.inbox.queued`` event to ``events.jsonl``
* count unread guidance without advancing ``inbox.offset``
* drain pending messages for the supervisor without crashing on bad lines
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..life.event_log import JsonlEventSink

INBOX_FILE = "inbox.jsonl"
OFFSET_FILE = "inbox.offset"


def inbox_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / INBOX_FILE


def inbox_offset_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / OFFSET_FILE


def _read_offset(path: Path) -> int:
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def _write_offset(path: Path, offset: int) -> None:
    try:
        path.write_text(str(max(0, offset)), encoding="utf-8")
    except OSError:
        return


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _read_inbox_messages(
    life_dir: Path | str,
    *,
    advance: bool,
    limit: int | None = None,
) -> list[str]:
    inbox = inbox_path(life_dir)
    offset_file = inbox_offset_path(life_dir)
    if not inbox.exists():
        return []
    offset = _read_offset(offset_file)
    messages: list[str] = []
    try:
        with inbox.open("rb") as fh:
            fh.seek(offset)
            while True:
                raw = fh.readline()
                if not raw:
                    break
                new_offset = fh.tell()
                if advance:
                    _write_offset(offset_file, new_offset)
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                text = obj.get("text") if isinstance(obj, dict) else None
                if not isinstance(text, str):
                    continue
                text = text.strip()
                if not text:
                    continue
                messages.append(text)
                if limit is not None and len(messages) >= limit:
                    break
    except OSError:
        return []
    return messages


def count_pending_inbox_messages(life_dir: Path | str) -> int:
    return len(_read_inbox_messages(life_dir, advance=False))


def drain_inbox_messages(life_dir: Path | str, *, limit: int = 10) -> list[str]:
    return _read_inbox_messages(life_dir, advance=True, limit=max(1, limit))


def queue_inbox_message(life_dir: Path | str, text: str, *, source: str) -> None:
    inbox = inbox_path(life_dir)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "text": text}
    with inbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    JsonlEventSink(None, life_dir=Path(life_dir)).append({
        "type": "life.inbox.queued",
        "text": text,
        "source": source,
    })


def format_inbox_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type", ""))
    if event_type == "life.inbox.queued":
        text = str(event.get("text", "") or "").strip()
        if not text:
            return None
        source = str(event.get("source", "") or "").strip()
        label = "📥 life.inbox.queued"
        if source:
            label += f" · {source}"
        return f"{label} · {_truncate(text, 120)}"

    if event_type == "life.inbox.drained":
        raw_messages = event.get("messages", [])
        messages = [
            str(message).strip()
            for message in raw_messages
            if isinstance(message, str) and message.strip()
        ] if isinstance(raw_messages, list) else []
        count = int(event.get("count", 0) or 0)
        if not count:
            count = len(messages)
        if not count:
            return None
        preview = ", ".join(_truncate(message, 60) for message in messages[:3])
        suffix = f" · {preview}" if preview else ""
        plural = "message" if count == 1 else "messages"
        return f"📤 life.inbox.drained · {count} {plural}{suffix}"

    return None
