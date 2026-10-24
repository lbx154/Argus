"""A paused mission has to reach the operator, not wait to be discovered.

A blocked verdict carrying an operator question parks the run and writes
`pending_question` to disk. The portal shows it — to whoever is watching the
portal. Nobody watches a long-running daemon, so the run sits there while the
operator assumes it is still working.

These tests pin two things: the question actually goes out, and nothing about
sending it can disturb the mission that just paused.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life.supervisor.pending_notify import (
    ask_operator,
    notify_pending_question,
    should_report_pending_wait,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


def _item(**kw) -> SimpleNamespace:
    base = {
        "id": "item-1",
        "title": "survey the literature",
        "pending_question": "which dataset should the baseline use?",
        "operator_decision": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def channel(monkeypatch):
    """Capture what would have been sent, without touching a network."""
    sent: list[str] = []
    monkeypatch.setattr(
        "argus.life.supervisor.pending_notify._send_telegram",
        lambda message: sent.append(message) or True,
    )
    monkeypatch.setattr(
        "argus.life.supervisor.pending_notify._send_feishu",
        lambda _message: False,
    )
    return sent


# -- the message ------------------------------------------------------------


# -- sending ----------------------------------------------------------------


def test_the_dedup_survives_a_restart(project: Path, channel) -> None:
    notify_pending_question(project, _item())
    # A fresh process reads the ledger from disk.
    assert notify_pending_question(project, _item()) is False


# -- persistent waiting-status dedup ---------------------------------------


def test_wait_status_ignores_empty_question_set(project: Path) -> None:
    assert should_report_pending_wait(
        project,
        [_item(pending_question="")],
        now=100.0,
    ) is False


# -- the mission must not be affected --------------------------------------

def test_a_failing_channel_does_not_raise(project: Path, monkeypatch) -> None:
    def explode(_message):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(
        "argus.life.supervisor.pending_notify._send_telegram", explode
    )
    monkeypatch.setattr(
        "argus.life.supervisor.pending_notify._send_feishu", lambda _m: False
    )

    # The mission already paused correctly; a notification problem must not
    # change that.
    assert notify_pending_question(project, _item()) is False


def test_a_garbage_item_is_survivable(project: Path, channel) -> None:
    assert notify_pending_question(project, object()) is False


def test_an_unwritable_ledger_still_sends(tmp_path: Path, channel, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.life.supervisor.pending_notify._record_sent",
        lambda *_a: (_ for _ in ()).throw(OSError("read-only")),
    )

    # Losing the ledger costs a duplicate message, never a missed one: the
    # send still happens and the failure is swallowed.
    assert notify_pending_question(tmp_path, _item()) is False
    assert channel and "which dataset" in channel[0]


# -- the wiring -------------------------------------------------------------


def test_the_question_event_carries_the_options_a_role_listed(project, channel) -> None:
    events: list[dict] = []
    item = _item(pending_question="")
    ask_operator(
        project, item, "Which requirement wins?", reason="they conflict", layer="engineer",
        save=lambda **_fields: None, emit=events.append,
        options=("Keep the v1 API", "Ship the v2 schema"),
    )

    assert [option["label"] for option in item.operator_decision["options"]] == [
        "Keep the v1 API", "Ship the v2 schema",
    ]
    assert events[0]["options"] == ["Keep the v1 API", "Ship the v2 schema"]
    assert "1. Keep the v1 API\n2. Ship the v2 schema" in channel[0]
