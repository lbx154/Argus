"""When the operator last spoke, and what that means for the work.

A daemon that runs for days needs to know whether anyone is listening. The
operator's presence is read from the two places their words arrive: the
conversation transcript and the inbox of notes. Nothing here guesses at working
hours; it reports how long the silence has lasted and lets the roles draw the
practical conclusion, which is that a question asked into silence costs a night
of work, while a long run started into silence has results by morning.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# After this long without a word from the operator, roles are told to prefer
# work that carries itself to a result over work that waits for an answer.
UNATTENDED_AFTER_SECONDS = 3.0 * 3600.0


@dataclass(frozen=True)
class OperatorPresence:
    last_spoke_at: float | None
    now: float

    @property
    def silence_seconds(self) -> float | None:
        if self.last_spoke_at is None:
            return None
        return max(0.0, self.now - self.last_spoke_at)

    @property
    def unattended(self) -> bool:
        silence = self.silence_seconds
        return silence is None or silence >= UNATTENDED_AFTER_SECONDS

    def describe(self) -> str:
        """One line a role can read: local time and how long the operator has been away."""
        local = time.strftime("%Y-%m-%d %H:%M %A", time.localtime(self.now))
        silence = self.silence_seconds
        if silence is None:
            away = "the operator has not written during this campaign"
        elif silence < 3600:
            away = f"the operator last wrote {int(silence // 60)} minutes ago"
        else:
            away = f"the operator last wrote {silence / 3600:.1f} hours ago"
        return f"Local time is {local}; {away}."

    def guidance(self) -> str:
        """The practical conclusion, or an empty string when someone is around."""
        if not self.unattended:
            return ""
        return (
            "No one is likely to read a question soon. Prefer the work that has "
            "results ready by the time someone does: the long, claim-sized runs "
            "this machine can carry now, rather than a question or a small pilot "
            "that waits for an answer."
        )


def _last_operator_turn(life_dir: Path) -> float | None:
    try:
        from .transcript import read_turns

        turns = read_turns(life_dir, limit=200)
    except Exception:  # noqa: BLE001 - presence is advisory
        return None
    latest: float | None = None
    for turn in turns:
        if turn.get("role") != "operator":
            continue
        try:
            ts = float(turn.get("ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest


def _last_inbox_note(life_dir: Path) -> float | None:
    latest: float | None = None
    from ..apps._inbox import latest_durable_inbox_timestamp

    try:
        latest = latest_durable_inbox_timestamp(life_dir)
    except (OSError, RuntimeError):
        pass  # Presence is advisory; authority intake reports its own failures.
    for path in sorted(Path(life_dir).glob("inbox*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-50:]):
            line = line.strip()
            if not line:
                continue
            try:
                row: Any = json.loads(line)
                ts = float(row.get("ts") or 0.0)
            except (ValueError, TypeError, AttributeError):
                continue
            if ts and (latest is None or ts > latest):
                latest = ts
            break
    return latest


def operator_presence(life_dir: Path | str | None, *, now: float | None = None) -> OperatorPresence:
    """Read when the operator last spoke to this project."""
    moment = time.time() if now is None else float(now)
    if life_dir is None:
        return OperatorPresence(last_spoke_at=None, now=moment)
    root = Path(life_dir)
    candidates = [
        ts for ts in (_last_operator_turn(root), _last_inbox_note(root)) if ts
    ]
    return OperatorPresence(
        last_spoke_at=max(candidates) if candidates else None,
        now=moment,
    )


__all__ = ["OperatorPresence", "UNATTENDED_AFTER_SECONDS", "operator_presence"]
