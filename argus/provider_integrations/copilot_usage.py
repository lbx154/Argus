"""Copilot-specific per-call usage from the CLI's local session store."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agent_cli.copilot_home import argus_copilot_home

# Copilot records cost in nano-AI units. 1e9 nano-AIU = 1 AI credit and
# 1 AI credit = $0.01, therefore one USD is 1e11 nano-AIU.
NANO_AIU_PER_USD = 100_000_000_000


@dataclass(frozen=True)
class CopilotUsageCursor:
    db_path: Path
    max_id: int
    db_signature: tuple[int, int] | None
    wal_signature: tuple[int, int] | None
    fallback: CopilotUsageCursor | None = None


@dataclass(frozen=True)
class CopilotModelUsage:
    row_id: int
    session_id: str
    turn_index: int | None
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    total_nano_aiu: int | None
    request_multiplier: float | None
    created_at: str

    def to_usage_jsonable(self) -> dict[str, Any]:
        return {
            "usage_event_id": self.row_id,
            "session_id": self.session_id,
            "model": self.model,
            "turn_index": self.turn_index,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_tokens,
            "total_nano_aiu": self.total_nano_aiu,
            "cost_usd": (
                None
                if self.total_nano_aiu is None
                else self.total_nano_aiu / NANO_AIU_PER_USD
            ),
            "request_multiplier": self.request_multiplier,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CopilotCallUsage:
    rows: tuple[CopilotModelUsage, ...]

    @property
    def model(self) -> str:
        models = sorted({row.model for row in self.rows if row.model})
        return models[0] if len(models) == 1 else "mixed"

    @property
    def input_tokens(self) -> int | None:
        return _sum_optional(row.input_tokens for row in self.rows)

    @property
    def output_tokens(self) -> int | None:
        return _sum_optional(row.output_tokens for row in self.rows)

    @property
    def cache_read_tokens(self) -> int | None:
        return _sum_optional(row.cache_read_tokens for row in self.rows)

    @property
    def cache_write_tokens(self) -> int | None:
        return _sum_optional(row.cache_write_tokens for row in self.rows)

    @property
    def reasoning_tokens(self) -> int | None:
        return _sum_optional(row.reasoning_tokens for row in self.rows)

    @property
    def total_nano_aiu(self) -> int | None:
        if any(row.total_nano_aiu is None for row in self.rows):
            return None
        return _sum_optional(row.total_nano_aiu for row in self.rows)

    @property
    def cost_usd(self) -> float | None:
        total = self.total_nano_aiu
        return None if total is None else total / NANO_AIU_PER_USD

    @property
    def model_usage(self) -> tuple[dict[str, Any], ...]:
        """Standard per-model rows suitable for durable usage records."""
        return tuple(row.to_usage_jsonable() for row in self.rows)


def copilot_usage_db_candidates() -> list[Path]:
    from ..trial.client import profile_path, trial_enabled

    if trial_enabled():
        return [profile_path().parent / "copilot-trial-home" / "session-store.db"]
    candidates: list[Path] = []
    configured = os.environ.get("COPILOT_HOME", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser() / "session-store.db")
    candidates.extend(
        [
            argus_copilot_home() / "session-store.db",
            Path.home() / ".copilot" / "session-store.db",
        ]
    )
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def capture_copilot_usage_cursor() -> CopilotUsageCursor | None:
    # Child execution relocates Copilot using a copy of os.environ. Looking at
    # the parent environment and picking the first existing DB can instead
    # capture the operator's unrelated personal store. Use the intended child
    # home even before its DB is created; accounting itself remains read-only.
    from ..trial.client import profile_path, trial_enabled

    if trial_enabled():
        return _capture_cursor(profile_path().parent / "copilot-trial-home" / "session-store.db")
    configured = os.environ.get("COPILOT_HOME", "").strip()
    home = Path(configured).expanduser() if configured else argus_copilot_home()
    path = home / "session-store.db"
    # If child-home preparation fails, the CLI falls back to its personal home.
    # Capture that baseline separately and only read new rows for this session.
    personal = Path.home() / ".copilot" / "session-store.db"
    fallback = _capture_cursor(personal) if not configured and path != personal else None
    return _capture_cursor(path, fallback=fallback)


def _capture_cursor(
    path: Path, *, fallback: CopilotUsageCursor | None = None
) -> CopilotUsageCursor:
    return CopilotUsageCursor(
        db_path=path,
        max_id=(_max_usage_id(path) or 0) if path.is_file() else 0,
        db_signature=_signature(path),
        wal_signature=_signature(path.with_name(path.name + "-wal")),
        fallback=fallback,
    )


def copilot_store_supports_token_billing(path: Path | None) -> bool:
    """Expect token telemetry unless a readable store proves it is legacy."""
    if path is None:
        return False
    if not path.is_file():
        return True
    try:
        with _connect(path) as conn:
            columns = conn.execute("PRAGMA table_info(assistant_usage_events)").fetchall()
        return not columns or any(row[1] == "total_nano_aiu" for row in columns)
    except (OSError, sqlite3.Error):
        # Busy/corrupt stores do not establish a legacy billing contract.
        return True


def copilot_usage_store_signature() -> list[dict[str, Any]]:
    """Read-only invalidation key, including uncheckpointed SQLite writes."""
    return [
        {
            "path": str(path),
            "db": list(_signature(path) or ()),
            "wal": list(_signature(path.with_name(path.name + "-wal")) or ()),
        }
        for path in copilot_usage_db_candidates()
    ]


def read_copilot_usage_since(
    cursor: CopilotUsageCursor | None,
    *,
    session_id: str | None,
    timeout: float = 0.75,
) -> CopilotCallUsage | None:
    if cursor is None or not session_id:
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    stable_reads = 0
    last_ids: tuple[int, ...] = ()
    while True:
        rows = _usage_rows(cursor.db_path, min_id=cursor.max_id, session_id=session_id)
        if not rows and cursor.fallback is not None:
            rows = _usage_rows(
                cursor.fallback.db_path,
                min_id=cursor.fallback.max_id,
                session_id=session_id,
            )
        ids = tuple(row.row_id for row in rows)
        # Billing can arrive by updating existing rows, without changing IDs.
        if rows and ids == last_ids and all(row.total_nano_aiu is not None for row in rows):
            stable_reads += 1
            if stable_reads >= 1:
                return CopilotCallUsage(tuple(rows))
        elif rows:
            stable_reads = 0
            last_ids = ids
        if time.monotonic() >= deadline:
            return CopilotCallUsage(tuple(rows)) if rows else None
        time.sleep(0.05)


def find_copilot_usage_near(
    *,
    completed_at: float,
    session_id: str | None = None,
    started_at: float | None = None,
) -> tuple[Path, CopilotCallUsage] | None:
    # A time window alone cannot distinguish this call from unrelated spend.
    if not session_id:
        return None
    start = (started_at if started_at is not None else completed_at - 5.0) - 1.0
    end = completed_at + 1.0
    for path in copilot_usage_db_candidates():
        if not path.is_file():
            continue
        rows = _usage_rows(
            path,
            min_id=0,
            session_id=session_id,
            created_from=_iso(start),
            created_to=_iso(end),
        )
        if rows:
            return path, CopilotCallUsage(tuple(rows))
    return None


def _usage_rows(
    path: Path,
    *,
    min_id: int,
    session_id: str | None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> list[CopilotModelUsage]:
    where = ["id > ?"]
    params: list[Any] = [int(min_id)]
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if created_from:
        where.append("julianday(created_at) >= julianday(?)")
        params.append(created_from)
    if created_to:
        where.append("julianday(created_at) <= julianday(?)")
        params.append(created_to)
    sql = f"""
        SELECT id, session_id, turn_index, model,
               input_tokens, output_tokens, cache_read_tokens,
               cache_write_tokens, reasoning_tokens, total_nano_aiu,
               request_multiplier, created_at
          FROM assistant_usage_events
         WHERE {' AND '.join(where)}
         ORDER BY id
    """
    try:
        with _connect(path) as conn:
            raw_rows = conn.execute(sql, params).fetchall()
    except (OSError, sqlite3.Error):
        return []
    return [
        CopilotModelUsage(
            row_id=int(row[0]),
            session_id=str(row[1]),
            turn_index=_optional_int(row[2]),
            model=str(row[3] or ""),
            input_tokens=_optional_int(row[4]),
            output_tokens=_optional_int(row[5]),
            cache_read_tokens=_optional_int(row[6]),
            cache_write_tokens=_optional_int(row[7]),
            reasoning_tokens=_optional_int(row[8]),
            total_nano_aiu=_optional_int(row[9]),
            request_multiplier=_optional_float(row[10]),
            created_at=str(row[11] or ""),
        )
        for row in raw_rows
    ]


def _max_usage_id(path: Path) -> int | None:
    try:
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM assistant_usage_events"
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    return int(row[0] or 0) if row else 0


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _sum_optional(values: Any) -> int | None:
    seen = False
    total = 0
    for value in values:
        if value is None:
            continue
        seen = True
        total += int(value)
    return total if seen else None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "CopilotCallUsage",
    "CopilotModelUsage",
    "CopilotUsageCursor",
    "NANO_AIU_PER_USD",
    "capture_copilot_usage_cursor",
    "copilot_store_supports_token_billing",
    "copilot_usage_db_candidates",
    "copilot_usage_store_signature",
    "find_copilot_usage_near",
    "read_copilot_usage_since",
]
