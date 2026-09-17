"""Feishu bridge: inbound guards, event normalization, and the send path.

The long connection itself needs the optional ``lark-oapi`` SDK and a live
socket, so these tests exercise everything either side of it: the guard logic
that decides whether an event runs at all, the flattening of the SDK's event
object, and the outbound card path.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.life import feishu_bot
from argus.life.chat.dedup import EventDedup, sender_allowed
from argus.life.chat.router import CommandRouter, help_text


@pytest.fixture
def life_dir(tmp_path):
    path = tmp_path / "projects" / "s-feishu01"
    path.mkdir(parents=True)
    return path


def _poller(life_dir, **kwargs):
    poller = feishu_bot.FeishuPoller(
        life_dir=life_dir,
        app_id="cli_test",
        app_secret="secret",
        **kwargs,
    )
    # Run command work inline so assertions don't race a worker thread.
    poller._spawn = lambda work, name: work()
    return poller


def _event(**overrides):
    event = {
        "event_id": "evt-1",
        "chat_id": "oc_chat",
        "message_id": "om_msg",
        "text": "/status",
        "sender_id": "ou_alice",
    }
    event.update(overrides)
    return event


class _RecordingRouter:
    """Stands in for CommandRouter; records what got dispatched."""

    instances: list["_RecordingRouter"] = []

    def __init__(self, *, life_dir, transport):
        self.life_dir = life_dir
        self.transport = transport
        self.dispatched: list[str] = []
        _RecordingRouter.instances.append(self)

    def dispatch(self, text: str) -> None:
        self.dispatched.append(text)


@pytest.fixture
def router(monkeypatch):
    _RecordingRouter.instances = []
    monkeypatch.setattr(feishu_bot, "CommandRouter", _RecordingRouter)
    # Reactions hit the network; the guard tests don't care about them.
    monkeypatch.setattr(feishu_bot.FeishuTransport, "begin_progress", lambda *a, **k: "")
    monkeypatch.setattr(feishu_bot.FeishuTransport, "end_progress", lambda *a, **k: None)
    return _RecordingRouter


def _dispatched() -> list[str]:
    return [text for r in _RecordingRouter.instances for text in r.dispatched]


@pytest.mark.parametrize("channel_name", ["", "飞书", "Telegram"])
def test_help_card_describes_current_four_role_runtime(channel_name: str) -> None:
    text = help_text(channel_name)

    assert text.index("Manager · 控制") < text.index("Planner · 方向")
    assert text.index("Planner · 方向") < text.index("Engineer · 执行")
    assert text.index("Engineer · 执行") < text.index("Reviewer · 验证")
    assert "只走 Manager" in text
    assert "四层 Agent 架构" not in text
    assert "L3" not in text


def test_status_role_labels_match_the_four_role_runtime() -> None:
    assert CommandRouter._LAYER_LABELS == {
        "manager": "👔 Manager · 控制",
        "planner": "🧠 Planner · 方向",
        "engineer": "👷 Engineer · 执行",
        "reviewer": "👨‍🏫 Reviewer · 验证",
    }


def test_feishu_status_uses_current_role_label(life_dir) -> None:
    router = CommandRouter(
        life_dir=life_dir,
        transport=feishu_bot.FeishuTransport(
            app_id="cli_test",
            app_secret="secret",
            chat_id="oc_chat",
        ),
    )
    memory = SimpleNamespace(
        journal=SimpleNamespace(
            tail=lambda _count: [
                SimpleNamespace(extra={"agent_layer": "engineer"}),
            ]
        )
    )

    assert router._detect_active_layer(memory) == "👷 Engineer · 执行"


# -- inbound guards ---------------------------------------------------------

def test_message_runs_the_shared_command_router(life_dir, router) -> None:
    _poller(life_dir).handle_event(_event(text="/backlog"))

    assert _dispatched() == ["/backlog"]


def test_duplicate_event_is_processed_once(life_dir, router) -> None:
    poller = _poller(life_dir)

    poller.handle_event(_event(event_id="evt-dup"))
    poller.handle_event(_event(event_id="evt-dup"))

    assert _dispatched() == ["/status"]


def test_dedup_survives_a_restart(life_dir, router) -> None:
    _poller(life_dir).handle_event(_event(event_id="evt-persist"))
    # A fresh poller reads the ledger the first one wrote.
    _poller(life_dir).handle_event(_event(event_id="evt-persist"))

    assert _dispatched() == ["/status"]


def test_sender_outside_the_allowlist_is_ignored(life_dir, router) -> None:
    poller = _poller(life_dir, allowed_users="ou_bob")

    poller.handle_event(_event(sender_id="ou_alice"))

    assert _dispatched() == []


def test_allowlisted_sender_is_accepted(life_dir, router) -> None:
    poller = _poller(life_dir, allowed_users="ou_bob,ou_alice")

    poller.handle_event(_event(sender_id="ou_alice"))

    assert _dispatched() == ["/status"]


def test_empty_text_and_missing_chat_are_dropped(life_dir, router) -> None:
    poller = _poller(life_dir)

    poller.handle_event(_event(event_id="a", text="   "))
    poller.handle_event(_event(event_id="b", chat_id=""))

    assert _dispatched() == []


def test_reply_goes_back_to_the_originating_chat(life_dir, router) -> None:
    _poller(life_dir).handle_event(_event(chat_id="oc_other"))

    assert _RecordingRouter.instances[-1].transport.chat_id == "oc_other"


# -- allowlist semantics ----------------------------------------------------

def test_blank_allowlist_allows_everyone() -> None:
    assert sender_allowed("ou_anyone", "") is True
    assert sender_allowed("ou_anyone", None) is True


def test_wildcard_allows_everyone() -> None:
    assert sender_allowed("ou_anyone", "*") is True


def test_allowlist_ignores_surrounding_whitespace() -> None:
    assert sender_allowed("ou_a", " ou_a , ou_b ") is True


# -- dedup ledger -----------------------------------------------------------

def test_blank_event_id_is_never_deduped(tmp_path) -> None:
    dedup = EventDedup(tmp_path / "seen.json")

    assert dedup.seen("") is False
    assert dedup.seen("") is False


def test_stale_entries_are_pruned(tmp_path) -> None:
    import time

    path = tmp_path / "seen.json"
    # An id recorded two days ago is past any redelivery window.
    path.write_text(json.dumps({"ancient": int(time.time()) - 2 * 86400}))
    dedup = EventDedup(path)

    # Recording anything prunes the stale entry...
    assert dedup.seen("fresh") is False
    assert "ancient" not in json.loads(path.read_text())
    # ...and a recent one is still remembered.
    assert dedup.seen("fresh") is True


def test_corrupt_ledger_is_treated_as_empty(tmp_path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("{not json")

    assert EventDedup(path).seen("evt") is False


# -- SDK event flattening ---------------------------------------------------

def test_normalize_flattens_the_sdk_event() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="evt-9"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_type="text",
                content=json.dumps({"text": "/status"}),
                chat_id="oc_1",
                message_id="om_1",
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
        ),
    )

    assert feishu_bot.FeishuPoller._normalize(data) == {
        "event_id": "evt-9",
        "chat_id": "oc_1",
        "message_id": "om_1",
        "text": "/status",
        "sender_id": "ou_1",
    }


def test_normalize_ignores_non_text_messages() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="evt-img"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_type="image",
                content=json.dumps({"image_key": "img_x"}),
                chat_id="oc_1",
                message_id="om_1",
            ),
            sender=None,
        ),
    )

    assert feishu_bot.FeishuPoller._normalize(data)["text"] == ""


# -- outbound ---------------------------------------------------------------

def test_send_posts_a_lark_md_card(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(feishu_bot, "tenant_access_token", lambda *a: "tok")
    monkeypatch.setattr(
        feishu_bot,
        "_request",
        lambda method, url, **kw: calls.append({"method": method, "url": url, **kw}),
    )

    transport = feishu_bot.FeishuTransport(
        app_id="cli", app_secret="s", chat_id="oc_1",
    )
    transport.send("<b>状态</b>")

    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["receive_id"] == "oc_1"
    assert payload["msg_type"] == "interactive"
    card = json.loads(payload["content"])
    assert card["elements"][0]["text"]["tag"] == "lark_md"
    assert "**状态**" in card["elements"][0]["text"]["content"]


def test_long_send_is_split_into_several_cards(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(feishu_bot, "tenant_access_token", lambda *a: "tok")
    monkeypatch.setattr(
        feishu_bot, "_request", lambda method, url, **kw: calls.append(kw),
    )

    transport = feishu_bot.FeishuTransport(
        app_id="cli", app_secret="s", chat_id="oc_1",
    )
    transport.send("\n".join("row " + "q" * 150 for _ in range(200)))

    assert len(calls) > 1


def test_send_without_a_token_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(feishu_bot, "tenant_access_token", lambda *a: None)
    sent: list = []
    monkeypatch.setattr(feishu_bot, "_request", lambda *a, **k: sent.append(a))

    feishu_bot.FeishuTransport(app_id="", app_secret="", chat_id="oc").send("hi")

    assert sent == []


# -- configuration ----------------------------------------------------------

def test_bridge_is_off_unless_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENABLE_FEISHU", raising=False)
    assert feishu_bot.feishu_enabled() is False

    monkeypatch.setenv("ARGUS_SKILL_ENABLE_FEISHU", "1")
    assert feishu_bot.feishu_enabled() is True


def test_poller_needs_credentials_to_be_enabled(life_dir, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ENABLE_FEISHU", "1")

    assert feishu_bot.FeishuPoller(
        life_dir=life_dir, app_id="", app_secret="",
    ).enabled is False
    assert feishu_bot.FeishuPoller(
        life_dir=life_dir, app_id="cli", app_secret="s",
    ).enabled is True


def test_domain_switches_between_feishu_and_lark(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_FEISHU_DOMAIN", "lark")
    assert "larksuite" in feishu_bot._api_base()

    monkeypatch.setenv("ARGUS_SKILL_FEISHU_DOMAIN", "feishu")
    assert "feishu.cn" in feishu_bot._api_base()

    monkeypatch.setenv("ARGUS_SKILL_FEISHU_DOMAIN", "https://open.example.com/")
    assert feishu_bot._api_base() == "https://open.example.com"


def test_missing_sdk_leaves_the_daemon_running(life_dir, monkeypatch, caplog) -> None:
    """An enabled bridge without lark-oapi must degrade, not crash."""
    import builtins

    real_import = builtins.__import__

    def _no_lark(name, *args, **kwargs):
        if name == "lark_oapi":
            raise ImportError("no lark_oapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_lark)

    feishu_bot.FeishuPoller(life_dir=life_dir, app_id="c", app_secret="s")._run()

    assert "lark-oapi is not installed" in caplog.text
