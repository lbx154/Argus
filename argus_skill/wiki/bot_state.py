"""Persistent state for the wiki-collector cooldown.

Lives at `.autors/<project>/wiki/data/bot_state.json`. Tiny JSON file:
no migrations, no schema enforcement beyond dataclass field names.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BotState:
    last_collected_at: datetime | None = None
    last_attempted_at: datetime | None = None
    last_query_seed: str | None = None
    consecutive_failures: int = 0
    notes: str = ""


def load_bot_state(path: Path) -> BotState:
    if not path.exists():
        return BotState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("bot_state must be a JSON object")
        lc = data.get("last_collected_at")
        la = data.get("last_attempted_at")
        return BotState(
            last_collected_at=(
                datetime.fromisoformat(lc).astimezone(timezone.utc) if lc else None
            ),
            last_attempted_at=(
                datetime.fromisoformat(la).astimezone(timezone.utc) if la else None
            ),
            last_query_seed=data.get("last_query_seed"),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            notes=data.get("notes", ""),
        )
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        quarantined = _quarantine_corrupt_file(path)
        return BotState(notes=f"recovered from corrupt state: {quarantined.name}")


def save_bot_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(state)
    if state.last_collected_at is not None:
        data["last_collected_at"] = state.last_collected_at.astimezone(
            timezone.utc
        ).isoformat()
    if state.last_attempted_at is not None:
        data["last_attempted_at"] = state.last_attempted_at.astimezone(
            timezone.utc
        ).isoformat()
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _quarantine_corrupt_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid.uuid4().hex[:8]}")
    try:
        path.rename(target)
    except OSError:
        return path
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def cooldown_elapsed(
    *,
    last_collected_at: datetime | None,
    now: datetime,
    hours: float,
) -> bool:
    if last_collected_at is None:
        return True
    return (now - last_collected_at).total_seconds() >= hours * 3600


def collect_backoff_hours(state: BotState) -> float:
    """Return the collector cooldown window for this state.

    Successful collects use the normal 12h window. Failed attempts retry
    sooner with exponential backoff capped at 12h.
    """
    failure_mode = bool(state.consecutive_failures > 0) or (
        state.last_attempted_at is not None
        and (
            state.last_collected_at is None
            or state.last_attempted_at > state.last_collected_at
        )
    )
    if not failure_mode:
        return 12.0
    failures = max(1, int(state.consecutive_failures or 1))
    return min(12.0, 0.5 * (2 ** failures))


def collect_cooldown_elapsed(*, state: BotState, now: datetime) -> bool:
    reference = (
        state.last_attempted_at
        if (
            state.last_attempted_at is not None
            and (
                state.consecutive_failures > 0
                or state.last_collected_at is None
                or state.last_attempted_at > state.last_collected_at
            )
        )
        else state.last_collected_at
    )
    return cooldown_elapsed(
        last_collected_at=reference,
        now=now,
        hours=collect_backoff_hours(state),
    )
