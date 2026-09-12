"""Durable mission completion envelopes and retrying their delivery.

Backlog owns publication of pending envelopes in its commit protocol. This
module never changes mission status and never calls a model. Acknowledgement
only removes pending delivery; the result remains in the settled backlog row.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

import portalocker

from ..core.event_catalog import EventType

PENDING_DIRECTORY = "mission-deliveries"
DELIVERY_LOCK = "mission-deliveries.lock"
log = logging.getLogger(__name__)


def prepare_mission_delivery(
    *, item: Any, event: dict[str, Any], result: dict[str, Any],
) -> dict[str, Any]:
    from .event_log import JsonlEventSink

    identity = f"{item.id}:{int(item.attempt)}:{int(item.iteration_cycles_done)}:{item.started_ts}"
    delivery_id = hashlib.sha256(identity.encode()).hexdigest()
    event = JsonlEventSink._normalize({
        **event, "event_id": f"mission-{delivery_id}", "mission_delivery_id": delivery_id,
    })
    from ..core.secret_guard import known_secret_values, redact_secrets_record

    record = {
        "version": 1, "id": delivery_id, "item_id": item.id,
        "attempt": int(item.attempt), "event": event,
        "result": redact_secrets_record(result, known_values=known_secret_values()),
    }
    validate_mission_delivery(record)
    return record


def validate_mission_delivery(record: Any) -> None:
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
        raise ValueError("unsupported mission delivery record")
    if (
        not isinstance(record.get("item_id"), str) or not record["item_id"]
        or type(record.get("attempt")) is not int or record["attempt"] <= 0
    ):
        raise ValueError("invalid mission delivery ownership")
    key = record.get("id")
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid mission delivery id")
    event = record.get("event")
    if (
        not isinstance(event, dict)
        or event.get("type") != EventType.LIFE_MISSION_COMPLETED
        or event.get("mission_delivery_id") != key
        or event.get("event_id") != f"mission-{key}"
        or event.get("event_validation")
        or event.get("item_id") != record.get("item_id")
        or not isinstance(record.get("result"), dict)
        or record["result"].get("item_id") != record.get("item_id")
    ):
        raise ValueError("invalid mission delivery envelope")
    json.dumps(record, allow_nan=False)


def drain_mission_deliveries(
    backlog: Any, emit: Callable[[dict[str, Any]], bool],
    confirm_receipt: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Deliver pending records without re-running missions; return whether drained.

    The dispatch lock prevents two supervisors from publishing the same pending
    record concurrently. Neither the backlog lock nor the events lock is held
    while invoking callbacks. A failed sink/ack leaves the record for retry.
    """
    root = backlog.path.parent
    root.mkdir(parents=True, exist_ok=True)
    with (root / DELIVERY_LOCK).open("a+b") as handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.AlreadyLocked:
            return not backlog.pending_mission_deliveries()
        try:
            from .event_log import JsonlEventSink, mission_delivery_was_persisted

            for record in backlog.pending_mission_deliveries():
                try:
                    persisted = mission_delivery_was_persisted(root, record["id"])
                    if not persisted and not emit(dict(record["event"])):
                        return False
                    if not mission_delivery_was_persisted(root, record["id"]):
                        # Embedders may supply an in-memory sink. Its acceptance
                        # is not a durable acknowledgement of this outbox.
                        if not JsonlEventSink(None, life_dir=root).append(record["event"]):
                            return False
                    if confirm_receipt is not None and not confirm_receipt(record["event"]):
                        return False
                    backlog.acknowledge_mission_delivery(record["id"])
                except Exception:
                    log.exception("mission completion remains pending for retry")
                    return False
            return True
        finally:
            portalocker.unlock(handle)


__all__ = [
    "PENDING_DIRECTORY", "prepare_mission_delivery", "validate_mission_delivery",
    "drain_mission_deliveries",
]
