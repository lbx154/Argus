from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from argus_skill.wiki.bot_state import (
    BotState,
    cooldown_elapsed,
    load_bot_state,
    save_bot_state,
)


def test_load_returns_default_when_file_missing(tmp_path: Path):
    state = load_bot_state(tmp_path / "bot_state.json")
    assert state.last_collected_at is None
    assert state.last_query_seed is None
    assert state.consecutive_failures == 0


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "bot_state.json"
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    save_bot_state(
        path,
        BotState(
            last_collected_at=now,
            last_query_seed="grpo,visual editing,reward hacking",
            consecutive_failures=2,
        ),
    )
    state = load_bot_state(path)
    assert state.last_collected_at == now
    assert state.last_query_seed == "grpo,visual editing,reward hacking"
    assert state.consecutive_failures == 2


def test_cooldown_elapsed_false_when_recent():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=6)
    assert cooldown_elapsed(last_collected_at=last, now=now, hours=12) is False


def test_cooldown_elapsed_true_when_old():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=24)
    assert cooldown_elapsed(last_collected_at=last, now=now, hours=12) is True


def test_cooldown_elapsed_true_when_never_collected():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert cooldown_elapsed(last_collected_at=None, now=now, hours=12) is True
