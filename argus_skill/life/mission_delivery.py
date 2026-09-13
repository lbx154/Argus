"""Durable mission completion envelopes and retrying their delivery.

Backlog owns publication of pending envelopes in its commit protocol. This
module never changes mission status and never calls a model. Acknowledgement
only removes pending delivery; the result remains in the settled backlog row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable

import portalocker

from ..core.event_catalog import EventType

PENDING_DIRECTORY = "mission-deliveries"
DELIVERY_LOCK = "mission-deliveries.lock"
EXPERIENCE_RETENTION = "mission-experience-retention.json"
MAX_PENDING_EXPERIENCES = 128
MAX_PENDING_EXPERIENCE_BYTES = 2 * 1024 * 1024
MAX_EXPERIENCE_RETRIES = 8
log = logging.getLogger(__name__)


def prepare_mission_delivery(
    *, item: Any, event: dict[str, Any], result: dict[str, Any],
    settled_experience: dict[str, Any] | None = None,
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
    if settled_experience is not None:
        record.update(
            version=2, publication_acknowledged=False,
            settled_experience=redact_secrets_record(settled_experience, known_values=known_secret_values()),
        )
    validate_mission_delivery(record)
    return record


def validate_mission_delivery(record: Any) -> None:
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] not in {1, 2}:
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
    timestamp = event.get("ts", 0)
    if type(timestamp) not in {int, float} or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("invalid mission delivery timestamp")
    json.dumps({key: record[key] for key in ("version", "id", "item_id", "attempt", "event", "result")}, allow_nan=False)


def _settled_experience(record: dict[str, Any]) -> Any:
    """Validate optional learning separately so damage cannot suppress delivery."""
    from .failure_experience import FailureExperience
    from .failure_experience_storage import ExperienceRepository

    row = record.get("settled_experience")
    if not isinstance(row, dict) or len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode()) > 128 * 1024:
        raise ValueError("missing or oversized settled experience")
    birth, identity, sources = row.get("created_at"), row.get("id"), row.get("source_refs")
    if (
        not isinstance(birth, (int, float)) or isinstance(birth, bool) or not math.isfinite(birth) or birth <= 0
        or not isinstance(identity, str) or not identity
        or row.get("mission_id") != record["item_id"]
        or type(row.get("revision")) is not int or row["revision"] != 1
        or row.get("state") != "active" or row.get("annotations") != []
        or not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], str)
        or not sources[0].startswith(f"mission:{record['item_id']}/attempt:")
    ):
        raise ValueError("invalid settled experience ownership")
    expected = f"exp:{float(birth).hex()}:{hashlib.sha256(sources[0].encode()).hexdigest()[:32]}"
    if identity != expected:
        raise ValueError("settled experience identity does not match its source")
    if row.get("status") in {"done", "success", "completed"} and not (
        record["event"].get("success") is True and record["result"].get("success") is True
        and record["result"].get("status") in {"done", "success", "completed"}
        and record["result"].get("iteration") is None
    ):
        raise ValueError("unaccepted completion cannot become successful experience")
    experience = FailureExperience.from_jsonable(row)
    ExperienceRepository.validate(experience)
    return experience


def _experience_key(record: dict[str, Any]) -> tuple[float, str]:
    return float(record["event"].get("ts") or 0), record["id"]


def _experience_retention(root: Path) -> dict[str, Any]:
    path = root / EXPERIENCE_RETENTION
    if not path.exists():
        return {"version": 1, "retired_through": [0.0, ""], "retired_count": 0,
                "reason": "pending_experience_capacity"}
    if path.stat().st_size > 4096:
        raise ValueError("experience retention metadata is oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    floor = value.get("retired_through") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
        or not isinstance(floor, list) or len(floor) != 2
        or type(floor[0]) not in {int, float} or not math.isfinite(floor[0]) or floor[0] < 0
        or not isinstance(floor[1], str)
        or (floor != [0.0, ""] and (len(floor[1]) != 64 or any(c not in "0123456789abcdef" for c in floor[1])))
        or type(value.get("retired_count")) is not int or not 0 <= value["retired_count"] < 2**63
        or value.get("reason") != "pending_experience_capacity"
    ):
        raise ValueError("experience retention metadata is corrupt")
    return value


def _bound_pending_experiences(backlog: Any, retention: dict[str, Any]) -> None:
    """Retire only acknowledged learning; persist its watermark before deletion."""
    from .memory import _atomic_rewrite_jsonl

    floor = tuple(retention["retired_through"])
    pending = []
    for record in backlog.pending_mission_deliveries():
        if record.get("publication_acknowledged") is not True:
            continue
        if _experience_key(record) <= floor:
            backlog.acknowledge_mission_delivery(record["id"])
        else:
            path = backlog.path.parent / PENDING_DIRECTORY / f"{record['id']}.json"
            pending.append((record, path.stat().st_size))
    pending.sort(key=lambda pair: _experience_key(pair[0]))
    total_bytes = sum(size for _record, size in pending)
    retired = []
    while len(pending) > MAX_PENDING_EXPERIENCES or total_bytes > MAX_PENDING_EXPERIENCE_BYTES:
        record, size = pending.pop(0)
        retired.append(record)
        total_bytes -= size
    if retired:
        retention = {
            **retention, "retired_through": list(_experience_key(retired[-1])),
            "retired_count": min(2**63 - 1, retention["retired_count"] + len(retired)),
        }
        _atomic_rewrite_jsonl(backlog.path.parent / EXPERIENCE_RETENTION, [retention])
        for record in retired:
            backlog.acknowledge_mission_delivery(record["id"])


def drain_mission_deliveries(
    backlog: Any, emit: Callable[[dict[str, Any]], bool],
    confirm_receipt: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Deliver pending records without re-running missions; return whether drained.

    The dispatch lock prevents duplicate concurrent publication. Learning uses
    canonical-only nonblocking writes and a separate bounded pending window;
    its failure never changes this function's delivery result.
    """
    root = backlog.path.parent
    root.mkdir(parents=True, exist_ok=True)
    with (root / DELIVERY_LOCK).open("a+b") as handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.AlreadyLocked:
            from .event_log import mission_delivery_was_persisted

            return all(record.get("publication_acknowledged") is True
                       and mission_delivery_was_persisted(root, record["id"])
                       for record in backlog.pending_mission_deliveries())
        try:
            from .event_log import JsonlEventSink, mission_delivery_was_persisted

            try:
                retention = _experience_retention(root)
            except (OSError, TypeError, ValueError, OverflowError):
                log.exception("experience retention recovery deferred; completion delivery remains independent")
                retention = None
            retries = 0
            for record in backlog.pending_mission_deliveries():
                captured = record["version"] == 1
                if not captured and retention is not None:
                    if _experience_key(record) <= tuple(retention["retired_through"]):
                        captured = True  # Explicit retention suppression, not new learning.
                    elif retries < MAX_EXPERIENCE_RETRIES:
                        try:
                            experience = _settled_experience(record)
                            retries += 1
                            from .failure_experience import FailureExperienceStore

                            FailureExperienceStore(root / "failure_experiences.jsonl").record_settled(experience)
                            captured = True
                        except Exception:
                            log.exception("settled experience remains pending; completion delivery continues")
                try:
                    persisted = mission_delivery_was_persisted(root, record["id"])
                    if record.get("publication_acknowledged") is not True or not persisted:
                        if not persisted and not emit(dict(record["event"])):
                            return False
                        if not mission_delivery_was_persisted(root, record["id"]):
                            # In-memory sink acceptance is not a durable ack.
                            if not JsonlEventSink(None, life_dir=root).append(record["event"]):
                                return False
                        if confirm_receipt is not None and not confirm_receipt(record["event"]):
                            return False
                    if captured:
                        if record.get("publication_acknowledged") is True:
                            try:
                                backlog.acknowledge_mission_delivery(record["id"])
                            except Exception:
                                log.exception("confirmed learning cleanup will retry")
                        else:
                            backlog.acknowledge_mission_delivery(record["id"])
                    elif record.get("publication_acknowledged") is not True:
                        try:
                            backlog.acknowledge_mission_delivery(record["id"], keep_for_experience=True)
                        except Exception:
                            log.exception("learning progress marker deferred after confirmed completion")
                except Exception:
                    log.exception("mission completion remains pending for retry")
                    return False
            if retention is not None:
                try:
                    _bound_pending_experiences(backlog, retention)
                except Exception:
                    log.exception("pending experience capacity cleanup will retry")
            return True
        finally:
            portalocker.unlock(handle)


__all__ = [
    "PENDING_DIRECTORY", "prepare_mission_delivery", "validate_mission_delivery",
    "drain_mission_deliveries",
]
