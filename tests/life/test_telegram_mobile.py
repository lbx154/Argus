"""Telegram's phone-facing behaviour: splitting, the command menu, and taps.

The command bodies are covered by ``test_telegram_bot.py``; this file covers
what makes the bridge usable from a phone.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.life import telegram_bot
from argus.life.chat.router import COMMAND_MENU


@pytest.fixture
def api(monkeypatch):
    """Capture outbound API calls instead of hitting Telegram."""
    calls: list[tuple[str, dict]] = []

    def _call(_token, method, payload=None, *, timeout=35):
        calls.append((method, payload or {}))
        return {"ok": True, "result": []}

    monkeypatch.setattr(telegram_bot, "_api_call", _call)
    return calls


def _sent_texts(calls) -> list[str]:
    return [payload["text"] for method, payload in calls if method == "sendMessage"]


# -- splitting --------------------------------------------------------------

def test_long_reply_is_sent_as_several_messages(api) -> None:
    body = "\n".join(f"line {i} " + "x" * 150 for i in range(200))

    telegram_bot._send_message("tok", "1", body)

    texts = _sent_texts(api)
    assert len(texts) > 1
    assert all(len(text) <= telegram_bot.TELEGRAM_LIMIT for text in texts)
    # The bridge used to truncate at 4090 and append "…"; the tail must survive.
    assert "line 199" in texts[-1]
    assert not any(text.endswith("…") for text in texts)


def test_short_reply_is_a_single_message(api) -> None:
    telegram_bot._send_message("tok", "1", "ok")

    assert _sent_texts(api) == ["ok"]


def test_every_chunk_carries_parse_mode(api) -> None:
    telegram_bot._send_message("tok", "1", "y" * 9000)

    modes = {payload["parse_mode"] for _m, payload in api if _m == "sendMessage"}
    assert modes == {"HTML"}


# -- command menu -----------------------------------------------------------

def test_command_menu_is_published(api) -> None:
    assert telegram_bot.publish_command_menu("tok") is True

    method, payload = api[0]
    assert method == "setMyCommands"
    names = [entry["command"] for entry in payload["commands"]]
    assert names == [name for name, _desc in COMMAND_MENU]


def test_command_menu_entries_satisfy_telegram_limits() -> None:
    for name, description in COMMAND_MENU:
        assert name == name.lower()
        assert 1 <= len(name) <= 32
        assert name.replace("_", "").isalnum()
        assert 1 <= len(description) <= 256


def test_menu_failure_is_reported_but_not_fatal(monkeypatch) -> None:
    monkeypatch.setattr(telegram_bot, "_api_call", lambda *a, **k: None)

    assert telegram_bot.publish_command_menu("tok") is False


# -- inline keyboard --------------------------------------------------------

def test_status_reply_carries_quick_actions(api) -> None:
    transport = telegram_bot.TelegramTransport(token="tok", chat_id="1")

    transport.send("📊 <b>argus 状态</b>\n🟢 守护进程运行中")

    _method, payload = api[-1]
    buttons = [
        button
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert {button["callback_data"] for button in buttons} == {
        "/status", "/backlog", "/journal", "/help",
    }


def test_ordinary_reply_has_no_keyboard(api) -> None:
    telegram_bot.TelegramTransport(token="tok", chat_id="1").send("✅ 任务已添加")

    assert "reply_markup" not in api[-1][1]


def test_keyboard_rides_only_on_the_final_chunk(api) -> None:
    transport = telegram_bot.TelegramTransport(token="tok", chat_id="1")

    transport.send("📊 " + "\n".join("row " + "z" * 150 for _ in range(200)))

    sends = [payload for method, payload in api if method == "sendMessage"]
    assert len(sends) > 1
    assert all("reply_markup" not in payload for payload in sends[:-1])
    assert "reply_markup" in sends[-1]


# -- callbacks --------------------------------------------------------------

def _poller(tmp_path) -> telegram_bot.TelegramPoller:
    return telegram_bot.TelegramPoller(
        life_dir=tmp_path, token="tok", chat_id="1",
    )


def test_button_tap_runs_the_matching_command(tmp_path, api) -> None:
    poller = _poller(tmp_path)
    dispatched: list[str] = []
    router = SimpleNamespace(dispatch=dispatched.append)

    poller._handle_callback(
        {
            "id": "cb1",
            "data": "/backlog",
            "from": {"id": "9"},
            "message": {"chat": {"id": "1"}},
        },
        router,
    )

    assert dispatched == ["/backlog"]
    assert api[0][0] == "answerCallbackQuery"


def test_tap_from_another_chat_is_ignored(tmp_path, api) -> None:
    poller = _poller(tmp_path)
    dispatched: list[str] = []

    poller._handle_callback(
        {
            "id": "cb1",
            "data": "/backlog",
            "message": {"chat": {"id": "999"}},
        },
        SimpleNamespace(dispatch=dispatched.append),
    )

    assert dispatched == []


def test_tap_from_another_user_is_ignored(tmp_path, api) -> None:
    poller = telegram_bot.TelegramPoller(
        life_dir=tmp_path, token="tok", chat_id="1", user_id="42",
    )
    dispatched: list[str] = []

    poller._handle_callback(
        {
            "id": "cb1",
            "data": "/backlog",
            "from": {"id": "7"},
            "message": {"chat": {"id": "1"}, "from": {"id": "42"}},
        },
        SimpleNamespace(dispatch=dispatched.append),
    )

    assert dispatched == []


def test_non_command_callback_payload_is_ignored(tmp_path, api) -> None:
    poller = _poller(tmp_path)
    dispatched: list[str] = []

    poller._handle_callback(
        {
            "id": "cb1",
            "data": "rm -rf /",
            "message": {"chat": {"id": "1"}},
        },
        SimpleNamespace(dispatch=dispatched.append),
    )

    assert dispatched == []


def test_callback_is_always_acknowledged(tmp_path, api) -> None:
    """An unanswered query leaves the client spinning until it times out."""
    poller = _poller(tmp_path)

    poller._handle_callback(
        {"id": "cb1", "data": "/status", "message": {"chat": {"id": "999"}}},
        SimpleNamespace(dispatch=lambda _text: None),
    )

    assert api[0][0] == "answerCallbackQuery"


def test_poller_subscribes_to_callback_updates(tmp_path, monkeypatch) -> None:
    seen: list[dict] = []
    stopped = {"value": False}

    def _call(_token, method, payload=None, *, timeout=35):
        if method == "getUpdates":
            seen.append(payload or {})
            stopped["value"] = True
        return {"ok": True, "result": []}

    monkeypatch.setattr(telegram_bot, "_api_call", _call)
    monkeypatch.setattr(telegram_bot, "_read_offset", lambda _d: 0)
    poller = _poller(tmp_path)
    poller._stop = SimpleNamespace(
        is_set=lambda: stopped["value"], wait=lambda timeout: None,
    )

    poller._poll_loop()

    assert seen[0]["allowed_updates"] == ["message", "callback_query"]
