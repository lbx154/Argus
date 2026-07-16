from __future__ import annotations

from argus_skill.apps import _inbox


def test_drain_does_not_deliver_message_when_offset_cannot_persist(
    tmp_path,
    monkeypatch,
) -> None:
    _inbox.queue_inbox_message(tmp_path, "change direction", source="test")
    monkeypatch.setattr(_inbox, "_write_offset", lambda _path, _offset: False)

    assert _inbox.drain_inbox_messages(tmp_path) == []
    assert _inbox.count_pending_inbox_messages(tmp_path) == 1


def test_drain_delivers_each_message_once_after_offset_recovers(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "change direction", source="test")

    assert _inbox.drain_inbox_messages(tmp_path) == ["change direction"]
    assert _inbox.drain_inbox_messages(tmp_path) == []
    assert _inbox.count_pending_inbox_messages(tmp_path) == 0
