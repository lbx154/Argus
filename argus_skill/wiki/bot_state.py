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
        return BotState(
            last_collected_at=(
                datetime.fromisoformat(lc).astimezone(timezone.utc) if lc else None
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
