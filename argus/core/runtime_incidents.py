"""Durable runtime incident recording and recovery verification."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping

from .file_lock import exclusive_file_lock

INCIDENT_DIRECTORY = "runtime-incidents"
INCIDENT_LOCK = "runtime-incidents.lock"
_EVENT_TYPES = {
    "detected": "life.runtime.incident.detected",
    "recovered": "life.runtime.incident.recovered",
    "escalated": "life.runtime.incident.escalated",
}


def _identity(detector: str, invariant: str, subject_kind: str, subject_id: str) -> str:
    payload = "\0".join((detector, invariant, subject_kind, subject_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return {"unserializable": type(value).__name__}
    return value


class RuntimeIncidentStore:
    """Persist monitor findings without depending on Manager availability."""

    def __init__(self, root: Path | str, *, clock: Callable[[], float] = time.time):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / INCIDENT_DIRECTORY
        self.lock_path = self.root / INCIDENT_LOCK
        self.clock = clock

    def detect(
        self,
        *,
        detector: str,
        invariant: str,
        subject_kind: str,
        subject_id: str,
        severity: str,
        observed: Mapping[str, Any],
    ) -> dict[str, Any]:
        incident_id = _identity(detector, invariant, subject_kind, subject_id)
        now = self.clock()
        with self._locked():
            previous = self._read(incident_id)
            occurrence_count = int(previous.get("occurrence_count") or 0)
            if not previous or previous.get("status") == "recovered":
                occurrence_count += 1
            record = {
                **previous,
                "version": 1,
                "incident_id": incident_id,
                "detector": str(detector),
                "invariant": str(invariant),
                "subject_kind": str(subject_kind),
                "subject_id": str(subject_id),
                "severity": str(severity),
                "status": "detected",
                "observed": _json_value(dict(observed)),
                "occurrence_count": max(1, occurrence_count),
                "detection_count": int(previous.get("detection_count") or 0) + 1,
                "recovery_attempts": int(previous.get("recovery_attempts") or 0),
                "first_detected_at": float(previous.get("first_detected_at") or now),
                "last_detected_at": now,
                "updated_at": now,
            }
            self._transition(record, "detected", {"observed": record["observed"]})
            self._write(record)
            return dict(record)

    def begin_recovery(
        self,
        incident_id: str,
        *,
        action: str,
        expected_postcondition: str,
    ) -> dict[str, Any]:
        with self._locked():
            record = self._required(incident_id)
            record.update({
                "status": "recovery_applied",
                "recovery_action": str(action),
                "expected_postcondition": str(expected_postcondition),
                "recovery_attempts": int(record.get("recovery_attempts") or 0) + 1,
                "recovery_applied_at": self.clock(),
                "updated_at": self.clock(),
            })
            self._transition(
                record,
                "recovery_applied",
                {
                    "action": str(action),
                    "expected_postcondition": str(expected_postcondition),
                },
            )
            self._write(record)
            return dict(record)

    def verify_recovery(
        self,
        incident_id: str,
        *,
        recovered: bool,
        evidence: Mapping[str, Any],
        repeat_escalation_threshold: int = 3,
    ) -> dict[str, Any]:
        with self._locked():
            record = self._required(incident_id)
            repeat_count = int(record.get("occurrence_count") or 0)
            repeated = (
                recovered
                and repeat_count >= max(1, int(repeat_escalation_threshold))
            )
            status = "recovered" if recovered and not repeated else "escalated"
            record.update({
                "status": status,
                "verification": _json_value(dict(evidence)),
                "recovery_verified": bool(recovered),
                "verified_at": self.clock(),
                "updated_at": self.clock(),
                "escalation_reason": (
                    "recovery postcondition failed"
                    if not recovered
                    else "incident repeated after prior recovery"
                    if repeated
                    else ""
                ),
                "manager_attention_required": status == "escalated",
            })
            self._transition(
                record,
                status,
                {
                    "recovered": bool(recovered),
                    "evidence": record["verification"],
                    "reason": record["escalation_reason"],
                },
            )
            if status == "escalated":
                self._queue_event(record, status)
            else:
                record["pending_event"] = None
            self._write(record)
            return dict(record)

    def record_unresolved(
        self,
        *,
        detector: str,
        invariant: str,
        subject_kind: str,
        subject_id: str,
        severity: str,
        observed: Mapping[str, Any],
        reason: str,
        escalation_after: int = 1,
    ) -> dict[str, Any]:
        record = self.detect(
            detector=detector,
            invariant=invariant,
            subject_kind=subject_kind,
            subject_id=subject_id,
            severity=severity,
            observed=observed,
        )
        with self._locked():
            record = self._required(record["incident_id"])
            failures = int(record.get("unresolved_count") or 0) + 1
            escalated = failures >= max(1, int(escalation_after))
            record.update({
                "status": "escalated" if escalated else "detected",
                "unresolved_count": failures,
                "escalation_reason": str(reason),
                "manager_attention_required": escalated,
                "updated_at": self.clock(),
            })
            self._transition(
                record,
                record["status"],
                {"reason": str(reason), "unresolved_count": failures},
            )
            if escalated:
                self._queue_event(record, "escalated")
            self._write(record)
            return dict(record)

    def recover_if_present(
        self,
        *,
        detector: str,
        invariant: str,
        subject_kind: str,
        subject_id: str,
        action: str,
        expected_postcondition: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        incident_id = _identity(detector, invariant, subject_kind, subject_id)
        with self._locked():
            record = self._read(incident_id)
            if not record or record.get("status") == "recovered":
                return None
        self.begin_recovery(
            incident_id,
            action=action,
            expected_postcondition=expected_postcondition,
        )
        return self.verify_recovery(
            incident_id,
            recovered=True,
            evidence=evidence,
        )

    def pending_events(self) -> list[dict[str, Any]]:
        with self._locked():
            records = []
            for path in sorted(self.directory.glob("*.json")):
                record = self._read_path(path)
                event = record.get("pending_event")
                if isinstance(event, dict):
                    records.append(dict(event))
            return records

    def acknowledge_event(self, incident_id: str, event_revision: int) -> bool:
        with self._locked():
            record = self._read(incident_id)
            event = record.get("pending_event")
            if (
                not isinstance(event, dict)
                or int(event.get("event_revision") or 0) != int(event_revision)
            ):
                return False
            record["last_notified_event_revision"] = int(event_revision)
            record["pending_event"] = None
            record["updated_at"] = self.clock()
            self._write(record)
            return True

    def read(self, incident_id: str) -> dict[str, Any]:
        with self._locked():
            return dict(self._read(incident_id))

    def _queue_event(self, record: dict[str, Any], status: str) -> None:
        revision = int(record.get("event_revision") or 0) + 1
        record["event_revision"] = revision
        record["pending_event"] = {
            "type": _EVENT_TYPES[status],
            "event_id": f"runtime-incident-{record['incident_id']}-{revision}",
            "event_revision": revision,
            "incident_id": record["incident_id"],
            "detector": record["detector"],
            "invariant": record["invariant"],
            "subject_kind": record["subject_kind"],
            "subject_id": record["subject_id"],
            "severity": record["severity"],
            "status": status,
            "occurrence_count": record["occurrence_count"],
            "recovery_attempts": record.get("recovery_attempts", 0),
            "recovery_verified": record.get("recovery_verified", False),
            "recovery_action": record.get("recovery_action", ""),
            "expected_postcondition": record.get("expected_postcondition", ""),
            "verification": record.get("verification", {}),
            "reason": record.get("escalation_reason", ""),
            "manager_attention_required": status == "escalated",
            "ts": self.clock(),
        }

    def _transition(
        self,
        record: dict[str, Any],
        status: str,
        detail: Mapping[str, Any],
    ) -> None:
        history = list(record.get("history") or [])
        history.append({
            "status": status,
            "ts": self.clock(),
            "detail": _json_value(dict(detail)),
        })
        record["history"] = history[-32:]

    def _required(self, incident_id: str) -> dict[str, Any]:
        record = self._read(incident_id)
        if not record:
            raise KeyError(f"runtime incident not found: {incident_id}")
        return record

    def _read(self, incident_id: str) -> dict[str, Any]:
        return self._read_path(self.directory / f"{incident_id}.json")

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, record: Mapping[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record['incident_id']}.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            with exclusive_file_lock(
                handle,
                lock_name=f"runtime incident store {self.lock_path}",
            ):
                yield


def drain_runtime_incident_events(
    root: Path | str,
    emit: Callable[[dict[str, Any]], Any],
) -> int:
    """Publish pending incident events, acknowledging only accepted delivery."""

    store = RuntimeIncidentStore(root)
    delivered = 0
    for event in store.pending_events():
        if emit(dict(event)) is False:
            continue
        if store.acknowledge_event(
            str(event["incident_id"]),
            int(event["event_revision"]),
        ):
            delivered += 1
    return delivered


__all__ = [
    "RuntimeIncidentStore",
    "drain_runtime_incident_events",
]
