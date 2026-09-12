"""Durable consultation receipts with indexed, bounded reads for Manager.

Completed request IDs are reusable without repeating provider calls. An
unfinished receipt is deliberately never automatically replayed: a process
could have died after provider spend but before recording its answer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MAX_RECEIPT_BYTES = 393216


def _connection(project_root: Path | str, *, create: bool = False) -> sqlite3.Connection | None:
    path = Path(project_root) / "advisor" / "receipts.sqlite3"
    if not create and not path.is_file():
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3)
    if create:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, updated_at REAL NOT NULL, payload TEXT NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS receipt_updated ON receipts(updated_at)")
    return connection


def _encoded(receipt: dict[str, Any]) -> str:
    raw = json.dumps(receipt, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ValueError("advisor receipt is oversized")
    return raw


def create_receipt(project_root: Path | str, receipt: dict[str, Any]) -> bool:
    connection = _connection(project_root, create=True)
    assert connection is not None
    try:
        with connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO receipts VALUES (?, ?, ?)",
                (receipt["consultation_id"], receipt["created_at"], _encoded(receipt)),
            ).rowcount
        return inserted == 1
    finally:
        connection.close()


def update_receipt(project_root: Path | str, receipt: dict[str, Any]) -> None:
    connection = _connection(project_root, create=True)
    assert connection is not None
    try:
        with connection:
            updated = connection.execute(
                "UPDATE receipts SET updated_at=?, payload=? WHERE id=?",
                (receipt.get("completed_at") or receipt["created_at"], _encoded(receipt), receipt["consultation_id"]),
            ).rowcount
            if updated != 1:
                raise ValueError("advisor receipt was not admitted")
    finally:
        connection.close()


def read_receipt(project_root: Path | str, consultation_id: str) -> dict[str, Any] | None:
    connection = _connection(project_root)
    if connection is None:
        return None
    try:
        row = connection.execute("SELECT payload FROM receipts WHERE id=?", (consultation_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        connection.close()


def recent_receipts(project_root: Path | str, *, after_ts: float = 0, limit: int = 20) -> list[dict[str, Any]]:
    """Newest receipt changes, returned oldest first; at most 100 rows read.

The cursor follows creation AND completion, so a pending request becoming
complete is visible to a Manager that already saw its initial receipt.
"""
    connection = _connection(project_root)
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT payload FROM receipts WHERE updated_at>? ORDER BY updated_at DESC LIMIT ?",
            (after_ts, max(1, min(100, int(limit)))),
        ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
    finally:
        connection.close()
