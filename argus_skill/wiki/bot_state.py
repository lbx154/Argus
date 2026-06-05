"""Persistent state for the wiki-collector cooldown.

Lives at `.autors/<project>/wiki/data/bot_state.json`. Tiny JSON file:
no migrations, no schema enforcement beyond dataclass field names.
"""
from __future__ import annotations

import json
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
    data = json.loads(path.read_text(encoding="utf-8"))
    lc = data.get("last_collected_at")
    return BotState(
        last_collected_at=(
            datetime.fromisoformat(lc).astimezone(timezone.utc) if lc else None
        ),
        last_query_seed=data.get("last_query_seed"),
        consecutive_failures=int(data.get("consecutive_failures", 0)),
        notes=data.get("notes", ""),
    )


def save_bot_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(state)
    if state.last_collected_at is not None:
        data["last_collected_at"] = state.last_collected_at.astimezone(
            timezone.utc
        ).isoformat()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def cooldown_elapsed(
    *,
    last_collected_at: datetime | None,
    now: datetime,
    hours: float,
) -> bool:
    if last_collected_at is None:
        return True
    return (now - last_collected_at).total_seconds() >= hours * 3600
