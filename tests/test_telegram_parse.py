"""Tests for the slim Telegram command parser.

We don't need a real Telegram server — ``parse_command_text`` and
``parse_command_from_update`` are pure functions over dicts.
"""
from __future__ import annotations

from argus_skill.telegram.poller import (
    TelegramCommand,
    parse_command_from_update,
    parse_command_text,
)


def _msg(text: str, chat_id: str = "100") -> dict:
    return {"update_id": 42, "message": {"chat": {"id": chat_id}, "text": text}}


def test_parse_run_command() -> None:
    cmd = parse_command_text(text="/run write a hello.py", plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="run", text="write a hello.py")


def test_parse_run_without_text_returns_none() -> None:
    assert parse_command_text(text="/run", plain_text_as_inject=False) is None


def test_parse_inject_command() -> None:
    cmd = parse_command_text(text="/inject use 4-space indent",
                             plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="inject", text="use 4-space indent")


def test_parse_interrupt_alias_for_inject() -> None:
    cmd = parse_command_text(text="/interrupt stop the test", plain_text_as_inject=False)
    assert cmd is not None
    assert cmd.kind == "inject"
    assert cmd.text == "stop the test"


def test_parse_skip_command() -> None:
    cmd = parse_command_text(text="/skip", plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="skip", text="")


def test_parse_stop_aliases() -> None:
    for text in ("/stop", "/halt", "/daemon-stop", "/shutdown-daemon"):
        cmd = parse_command_text(text=text, plain_text_as_inject=False)
        assert cmd == TelegramCommand(kind="stop", text=""), text


def test_parse_status_and_help() -> None:
    assert parse_command_text(text="/status", plain_text_as_inject=False) == TelegramCommand(
        kind="status", text=""
    )
    assert parse_command_text(text="/stat", plain_text_as_inject=False) == TelegramCommand(
        kind="status", text=""
    )
    assert parse_command_text(text="/help", plain_text_as_inject=False) == TelegramCommand(
        kind="help", text=""
    )
    assert parse_command_text(text="/commands", plain_text_as_inject=False) == TelegramCommand(
        kind="help", text=""
    )


def test_unknown_slash_command_dropped() -> None:
    # /plan, /mode etc. were intentionally NOT ported — they should
    # return None, not match anything else.
    for text in ("/plan", "/mode", "/btw", "/criteria", "/show-status"):
        assert parse_command_text(text=text, plain_text_as_inject=False) is None, text


def test_plain_text_as_inject_when_enabled() -> None:
    cmd = parse_command_text(text="please use pytest", plain_text_as_inject=True)
    assert cmd == TelegramCommand(kind="inject", text="please use pytest")


def test_plain_text_dropped_when_disabled() -> None:
    assert parse_command_text(text="please use pytest", plain_text_as_inject=False) is None


def test_chinese_full_width_slash_normalized() -> None:
    cmd = parse_command_text(text="／run 中文任务", plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="run", text="中文任务")


def test_extract_command_from_update_filters_chat() -> None:
    update = _msg("/run hello", chat_id="100")
    cmd = parse_command_from_update(update=update,
                                    expected_chat_id="100",
                                    plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="run", text="hello")


def test_extract_command_from_update_drops_other_chat() -> None:
    update = _msg("/run hello", chat_id="999")
    cmd = parse_command_from_update(update=update,
                                    expected_chat_id="100",
                                    plain_text_as_inject=False)
    assert cmd is None


def test_extract_command_from_update_uses_caption_fallback() -> None:
    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": "100"},
            "caption": "/run from caption",
        },
    }
    cmd = parse_command_from_update(update=update,
                                    expected_chat_id="100",
                                    plain_text_as_inject=False)
    assert cmd == TelegramCommand(kind="run", text="from caption")
