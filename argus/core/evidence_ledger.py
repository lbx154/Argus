"""Append-only evidence records with explicit, traceable corrections."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from .file_lock import exclusive_file_lock

_SCHEMA_VERSION = 1


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_ledger"}


def _legacy_record_id(row: dict[str, Any]) -> str:
    return str(
        row.get("record_id")
        or row.get("run_id")
        or row.get("id")
        or ""
    ).strip()


def _record_id(row: dict[str, Any]) -> str:
    metadata = row.get("_ledger")
    if isinstance(metadata, dict):
        value = str(metadata.get("record_id") or "").strip()
        if value:
            return value
    return _legacy_record_id(row)


def _record_sha256(row: dict[str, Any]) -> str:
    metadata = row.get("_ledger")
    if isinstance(metadata, dict):
        value = str(metadata.get("payload_sha256") or "").strip()
        if value:
            return value
    return _payload_sha256(_row_payload(row))


class EvidenceLedger:
    """A JSONL ledger where later facts annotate rather than replace history."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _rows_unlocked(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        if text and not text.endswith("\n"):
            raise ValueError(f"evidence ledger has a torn final row: {self.path}")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"evidence ledger has invalid JSON on line {line_number}: "
                    f"{self.path}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"evidence ledger line {line_number} is not an object: {self.path}"
                )
            rows.append(row)
        return rows

    def _append_unlocked(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _locked_handle(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        return self.lock_path.open("a+", encoding="utf-8")

    @staticmethod
    def _existing_by_id(
        rows: Iterable[dict[str, Any]],
        record_id: str,
    ) -> dict[str, Any] | None:
        return next((row for row in rows if _record_id(row) == record_id), None)

    def append_record(
        self,
        *,
        record_id: str,
        payload: dict[str, Any],
        record_type: str = "evidence",
        created_at: float | None = None,
        preserve_existing: bool = False,
    ) -> dict[str, Any]:
        """Append one immutable record, idempotently for identical content."""
        normalized_id = str(record_id or "").strip()
        normalized_type = str(record_type or "").strip()
        if not normalized_id:
            raise ValueError("record_id must be non-empty")
        if not normalized_type:
            raise ValueError("record_type must be non-empty")
        clean_payload = dict(payload)
        clean_payload.pop("_ledger", None)
        digest = _payload_sha256(clean_payload)
        with self._locked_handle() as handle:
            with exclusive_file_lock(
                handle,
                lock_name=f"evidence ledger lock {self.lock_path}",
            ):
                rows = self._rows_unlocked()
                existing = self._existing_by_id(rows, normalized_id)
                if existing is not None:
                    if _record_sha256(existing) != digest and not preserve_existing:
                        raise ValueError(
                            f"record_id {normalized_id!r} already names different evidence"
                        )
                    return existing
                row = {
                    **clean_payload,
                    "_ledger": {
                        "schema_version": _SCHEMA_VERSION,
                        "record_type": normalized_type,
                        "record_id": normalized_id,
                        "created_at": (
                            time.time() if created_at is None else float(created_at)
                        ),
                        "payload_sha256": digest,
                    },
                }
                self._append_unlocked(row)
                return row

    def append_correction(
        self,
        *,
        correction_id: str,
        target_record_id: str,
        relation: str,
        reason: str,
        evidence_refs: Iterable[str] = (),
        payload: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        """Append a correction bound to the exact original record digest."""
        normalized_id = str(correction_id or "").strip()
        normalized_target = str(target_record_id or "").strip()
        normalized_relation = str(relation or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_id:
            raise ValueError("correction_id must be non-empty")
        if not normalized_target:
            raise ValueError("target_record_id must be non-empty")
        if not normalized_relation:
            raise ValueError("relation must be non-empty")
        if not normalized_reason:
            raise ValueError("reason must be non-empty")
        refs = list(dict.fromkeys(str(ref).strip() for ref in evidence_refs if str(ref).strip()))
        clean_payload = dict(payload or {})
        clean_payload.pop("_ledger", None)

        with self._locked_handle() as handle:
            with exclusive_file_lock(
                handle,
                lock_name=f"evidence ledger lock {self.lock_path}",
            ):
                rows = self._rows_unlocked()
                target = self._existing_by_id(rows, normalized_target)
                if target is None:
                    raise KeyError(
                        f"cannot correct missing evidence record {normalized_target!r}"
                    )
                target_sha256 = _record_sha256(target)
                correction_payload = {
                    **clean_payload,
                    "correction_id": normalized_id,
                    "target_record_id": normalized_target,
                    "target_record_sha256": target_sha256,
                    "relation": normalized_relation,
                    "reason": normalized_reason,
                    "evidence_refs": refs,
                }
                digest = _payload_sha256(correction_payload)
                existing = self._existing_by_id(rows, normalized_id)
                if existing is not None:
                    if _record_sha256(existing) != digest:
                        raise ValueError(
                            f"correction_id {normalized_id!r} already names "
                            "different evidence"
                        )
                    return existing
                row = {
                    **correction_payload,
                    "_ledger": {
                        "schema_version": _SCHEMA_VERSION,
                        "record_type": "correction",
                        "record_id": normalized_id,
                        "created_at": (
                            time.time() if created_at is None else float(created_at)
                        ),
                        "payload_sha256": digest,
                        "target_record_id": normalized_target,
                        "target_record_sha256": target_sha256,
                    },
                }
                self._append_unlocked(row)
                return row

    def history(self, record_id: str) -> list[dict[str, Any]]:
        """Return the original record followed by corrections in ledger order."""
        normalized_id = str(record_id or "").strip()
        rows = self._rows_unlocked()
        return [
            row
            for row in rows
            if _record_id(row) == normalized_id
            or (
                isinstance(row.get("_ledger"), dict)
                and str(row["_ledger"].get("target_record_id") or "") == normalized_id
            )
        ]

    def get(self, record_id: str) -> dict[str, Any] | None:
        """Return one immutable record by id, including legacy rows."""
        return self._existing_by_id(
            self._rows_unlocked(),
            str(record_id or "").strip(),
        )


__all__ = ["EvidenceLedger"]
