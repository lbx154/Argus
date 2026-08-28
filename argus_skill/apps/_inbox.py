"""Shared inbox helpers for operator guidance.

The CLI, Web API, and cockpit all need the same inbox semantics:

* queue guidance to ``inbox.jsonl``
* emit a structured ``life.inbox.queued`` event to ``events.jsonl``
* count unread guidance without advancing ``inbox.offset``
* drain pending messages for the supervisor without crashing on bad lines
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..life.event_log import JsonlEventSink

INBOX_FILE = "inbox.jsonl"
OFFSET_FILE = "inbox.offset"
log = logging.getLogger(__name__)


def _stage_token(stage: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(stage or "").strip().lower()).strip("-")


def inbox_path(life_dir: Path | str, stage: str = "") -> Path:
    token = _stage_token(stage)
    return Path(life_dir) / (f"inbox.{token}.jsonl" if token else INBOX_FILE)


def inbox_offset_path(life_dir: Path | str, stage: str = "") -> Path:
    token = _stage_token(stage)
    return Path(life_dir) / (f"inbox.{token}.offset" if token else OFFSET_FILE)


def _read_offset(path: Path) -> int:
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def _write_offset(path: Path, offset: int) -> bool:
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(max(0, offset)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        log.warning("failed to persist inbox offset: %s", path)
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


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
    stage: str = "",
) -> list[str]:
    inbox = inbox_path(life_dir, stage)
    offset_file = inbox_offset_path(life_dir, stage)
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
                if advance and not _write_offset(offset_file, new_offset):
                    break
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
    total = len(_read_inbox_messages(life_dir, advance=False))
    root = Path(life_dir)
    try:
        staged = list(root.glob("inbox.*.jsonl"))
    except OSError:
        staged = []
    for path in staged:
        stage = path.name[len("inbox.") : -len(".jsonl")]
        total += len(_read_inbox_messages(life_dir, advance=False, stage=stage))
    return total


def drain_inbox_messages(
    life_dir: Path | str,
    *,
    limit: int = 10,
    current_stage: str | None = None,
) -> list[str]:
    messages = _read_inbox_messages(
        life_dir,
        advance=True,
        limit=max(1, limit),
    )
    remaining = max(0, max(1, limit) - len(messages))
    if remaining and str(current_stage or "").strip():
        messages.extend(_read_inbox_messages(
            life_dir,
            advance=True,
            limit=remaining,
            stage=str(current_stage or "").strip(),
        ))
    return messages


def queue_inbox_message(
    life_dir: Path | str,
    text: str,
    *,
    source: str,
    stage: str = "",
) -> None:
    root = Path(life_dir)
    from ..core.operator_context import import_deterministic_credential

    global_root = (
        root.parent.parent
        if root.parent.name == "projects"
        else None
    )
    text, _credential = import_deterministic_credential(
        root,
        text,
        global_root=global_root,
    )
    inbox = inbox_path(life_dir, stage)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "text": text}
    with inbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    JsonlEventSink(None, life_dir=Path(life_dir)).append({
        "type": EventType.LIFE_INBOX_QUEUED,
        "text": text,
        "source": source,
        "stage": stage.strip().lower(),
    })


def format_inbox_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type", ""))
    if event_type == "life.inbox.queued":
        text = str(event.get("text", "") or "").strip()
        if not text:
            return None
        source = str(event.get("source", "") or "").strip()
        stage = str(event.get("stage", "") or "").strip()
        label = "📥 life.inbox.queued"
        if source:
            label += f" · {source}"
        if stage:
            label += f" · stage={stage}"
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
