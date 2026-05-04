"""Tests for the friendly Telegram event formatter + verbose toggle.

These tests are pure (no network) — we only call ``format_event_message``
and ``TelegramNotifier.set_verbose`` directly.
"""
from __future__ import annotations

from argus_skill.telegram.notifier import (
    _USER_FACING_EVENTS,
    _VERBOSE_EVENTS,
    TelegramConfig,
    TelegramNotifier,
    format_event_message,
)


def _cfg() -> TelegramConfig:
    return TelegramConfig(bot_token="dummy", chat_id="0")


def test_format_known_event_uses_icon_and_drops_brackets() -> None:
    msg = format_event_message({"type": "task.completed", "text": "all good"})
    assert msg.startswith("✅ ")
    assert "[task.completed]" not in msg
    assert "all good" in msg


def test_format_unknown_event_keeps_legacy_bracketed_form() -> None:
    msg = format_event_message({"type": "round.start", "text": "round 1"})
    # Internal/dev events still get the bracketed form so verbose-mode
    # users can grep them.
    assert msg == "[round.start] round 1"


def test_format_no_text_emits_just_icon_for_known_kind() -> None:
    msg = format_event_message({"type": "daemon.started", "text": ""})
    assert msg == "🟢"


def test_format_task_completed_allows_long_payload() -> None:
    long_answer = "x" * 1800
    msg = format_event_message({"type": "task.completed", "text": long_answer})
    # Capped at 1500 chars, so plus icon + space + ellipsis ≈ 1503ish.
    assert len(msg) <= 1505
    assert msg.endswith("…")
    assert msg.startswith("✅ ")


def test_format_short_event_caps_at_300_for_non_completion() -> None:
    long_text = "x" * 500
    msg = format_event_message({"type": "task.started", "text": long_text})
    assert len(msg) <= 305
    assert msg.endswith("…")
    assert msg.startswith("🏃 ")


def test_default_config_subscribes_to_user_facing_only() -> None:
    cfg = _cfg()
    assert cfg.notify_event_types == _USER_FACING_EVENTS
    # Internal events are NOT in the default subscription set.
    assert "round.start" not in cfg.notify_event_types
    assert "match.info" not in cfg.notify_event_types


def test_set_verbose_true_expands_subscription() -> None:
    notifier = TelegramNotifier(_cfg())
    assert "round.start" not in notifier.config.notify_event_types
    notifier.set_verbose(True)
    assert notifier.config.verbose is True
    assert notifier.config.notify_event_types == _VERBOSE_EVENTS
    assert "round.start" in notifier.config.notify_event_types


def test_set_verbose_false_restores_minimal() -> None:
    notifier = TelegramNotifier(_cfg())
    notifier.set_verbose(True)
    notifier.set_verbose(False)
    assert notifier.config.verbose is False
    assert notifier.config.notify_event_types == _USER_FACING_EVENTS
