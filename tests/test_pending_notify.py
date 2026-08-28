"""A paused mission has to reach the operator, not wait to be discovered.

A blocked verdict carrying an operator question parks the run and writes
`pending_question` to disk. The portal shows it — to whoever is watching the
portal. Nobody watches a long-running daemon, so the run sits there while the
operator assumes it is still working.

These tests pin two things: the question actually goes out, and nothing about
sending it can disturb the mission that just paused.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.supervisor.pending_notify import (
    notify_pending_question,
    pending_question_message,
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
        "argus_skill.life.supervisor.pending_notify._send_telegram",
        lambda message: sent.append(message) or True,
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify._send_feishu",
        lambda _message: False,
    )
    return sent


# -- the message ------------------------------------------------------------

def test_the_message_carries_what_a_decision_needs() -> None:
    text = pending_question_message(
        project="s-research1",
        title="survey the literature",
        question="which dataset should the baseline use?",
        options=[{"label": "CIFAR"}, {"label": "ImageNet"}],
    )

    assert "Your decision is needed" in text
    assert "Project: s-research1" in text
    assert "Task: survey the literature" in text
    assert "s-research1" in text
    assert "survey the literature" in text
    assert "which dataset" in text
    # Options matter on a phone: reading them beats opening the portal.
    assert "CIFAR / ImageNet" in text


def test_options_are_optional() -> None:
    text = pending_question_message(project="p", title="t", question="q?")

    assert "q?" in text
    assert "Options:" not in text


def test_malformed_options_do_not_break_the_message() -> None:
    text = pending_question_message(
        project="p", title="t", question="q?", options=["not a dict", {}, {"label": ""}]
    )

    assert "Options:" not in text


def test_chinese_question_uses_a_chinese_wrapper() -> None:
    text = pending_question_message(
        project="研究项目",
        title="选择数据集",
        question="基线应该使用哪个数据集？",
        options=[{"label": "数据集 A"}, {"label": "数据集 B"}],
    )

    assert "需要你决定" in text
    assert "项目：研究项目" in text
    assert "任务：选择数据集" in text
    assert "可选：数据集 A / 数据集 B" in text


# -- sending ----------------------------------------------------------------

def test_the_question_is_sent(project: Path, channel) -> None:
    assert notify_pending_question(project, _item()) is True
    assert channel and "which dataset" in channel[0]


def test_the_same_question_is_not_sent_twice(project: Path, channel) -> None:
    notify_pending_question(project, _item())
    notify_pending_question(project, _item())

    assert len(channel) == 1


def test_the_dedup_survives_a_restart(project: Path, channel) -> None:
    notify_pending_question(project, _item())
    # A fresh process reads the ledger from disk.
    assert notify_pending_question(project, _item()) is False


def test_a_new_question_on_the_same_item_is_sent(project: Path, channel) -> None:
    # Pausing again with a different question is new information.
    notify_pending_question(project, _item())
    notify_pending_question(project, _item(pending_question="which seed count?"))

    assert len(channel) == 2


def test_nothing_is_sent_without_a_question(project: Path, channel) -> None:
    assert notify_pending_question(project, _item(pending_question="   ")) is False
    assert channel == []


def test_no_configured_channel_is_not_an_error(project: Path) -> None:
    # Every channel is opt-in; a project with none simply gets no message.
    assert notify_pending_question(project, _item()) is False


# -- persistent waiting-status dedup ---------------------------------------


def test_wait_status_is_reported_once_per_question_set(project: Path) -> None:
    items = [_item()]

    assert should_report_pending_wait(project, items, now=100.0) is True
    assert should_report_pending_wait(project, items, now=101.0) is False
    # A new supervisor process reads the same durable state.
    assert should_report_pending_wait(project, items, now=102.0) is False


def test_wait_status_reports_changed_question_and_slow_heartbeat(project: Path) -> None:
    assert should_report_pending_wait(project, [_item()], now=100.0) is True
    assert should_report_pending_wait(
        project,
        [_item(pending_question="which seed count?")],
        now=101.0,
    ) is True
    assert should_report_pending_wait(
        project,
        [_item(pending_question="which seed count?")],
        heartbeat_seconds=60.0,
        now=162.0,
    ) is True


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
        "argus_skill.life.supervisor.pending_notify._send_telegram", explode
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify._send_feishu", lambda _m: False
    )

    # The mission already paused correctly; a notification problem must not
    # change that.
    assert notify_pending_question(project, _item()) is False


def test_one_channel_failing_does_not_stop_the_other(project: Path, monkeypatch) -> None:
    delivered: list[str] = []

    def explode(_message):
        raise RuntimeError("down")

    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify._send_telegram", explode
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify._send_feishu",
        lambda message: delivered.append(message) or True,
    )

    assert notify_pending_question(project, _item()) is True
    assert delivered


def test_a_garbage_item_is_survivable(project: Path, channel) -> None:
    assert notify_pending_question(project, object()) is False


def test_an_unwritable_ledger_still_sends(tmp_path: Path, channel, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify._record_sent",
        lambda *_a: (_ for _ in ()).throw(OSError("read-only")),
    )

    # Losing the ledger costs a duplicate message, never a missed one: the
    # send still happens and the failure is swallowed.
    assert notify_pending_question(tmp_path, _item()) is False
    assert channel and "which dataset" in channel[0]


# -- the wiring -------------------------------------------------------------

def test_the_settlement_path_notifies_when_it_parks_a_mission() -> None:
    from argus_skill.life.supervisor import _mission_execution_settlement as mod

    source = inspect.getsource(mod)
    park_at = source.index("pending_question=operator_question")
    notify_at = source.index("notify_pending_question(")

    # Must fire after the question reaches the backlog, or a crash between the
    # two would announce a decision that was never recorded.
    assert park_at < notify_at
