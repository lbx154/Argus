"""Shared inbox helpers for operator guidance.

The CLI, Web API, and cockpit all need the same inbox semantics:

* durably queue guidance in the bounded SQLite inbox protocol
* emit a structured ``life.inbox.queued`` event to ``events.jsonl``
* count pending messages and retained delivery envelopes
* claim, freeze, accept, acknowledge, and settle messages explicitly
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..life.event_log import JsonlEventSink
from ._inbox_protocol import (  # noqa: F401 — shared public inbox protocol
    InboxBusy,
    InboxClaim,
    InboxError,
    InboxLeaseLost,
    InboxMigrationRequired,
    InboxPartialLine,
    InboxPressure,
    InboxProtocolError,
    accept_inbox_claim,
    acknowledge_inbox_claim,
    claim_inbox_message,
    count_durable_inbox_messages,
    enqueue_inbox_message,
    freeze_inbox_decision,
    latest_durable_inbox_timestamp,
    migrate_legacy_inbox,
    pending_inbox_status,
    release_inbox_claim,
    renew_inbox_claim,
    settle_inbox_claim,
)

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


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def count_pending_inbox_messages(life_dir: Path | str) -> int:
    return count_durable_inbox_messages(life_dir)


def drain_inbox_messages(
    life_dir: Path | str,
    *,
    limit: int = 10,
    current_stage: str | None = None,
) -> list[str]:
    """Reject the pre-acceptance cursor advance used by older built-in consumers.

    Host-supplied plain callbacks retain their own weaker delivery contract;
    they must not use this durable source as an implicit destructive reader.
    """
    raise InboxProtocolError("raw inbox drain is disabled; use the claim/accept/ACK protocol")


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
    enqueue_inbox_message(root, text, source=source, stage=stage)
    try:
        from ..core.file_lock import bounded_file_lock_wait

        with bounded_file_lock_wait(timeout_seconds=0.2):
            recorded = JsonlEventSink(None, life_dir=Path(life_dir)).append({
                "type": EventType.LIFE_INBOX_QUEUED,
                "text": text,
                "source": source,
                "stage": stage.strip().lower(),
            })
        if not recorded:
            log.warning("inbox accepted; advisory event was not written")
    except Exception:  # noqa: BLE001 — enqueue already committed durably
        log.warning("inbox queued but advisory event could not be written", exc_info=True)



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
