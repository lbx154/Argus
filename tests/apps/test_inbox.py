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


def test_stage_targeted_message_waits_without_blocking_generic_guidance(tmp_path) -> None:
    _inbox.queue_inbox_message(
        tmp_path,
        "profile only after baseline",
        source="test",
        stage="optimize",
    )
    _inbox.queue_inbox_message(tmp_path, "stop current run", source="test")

    assert _inbox.count_pending_inbox_messages(tmp_path) == 2
    assert _inbox.drain_inbox_messages(
        tmp_path,
        current_stage="baseline",
    ) == ["stop current run"]
    assert _inbox.count_pending_inbox_messages(tmp_path) == 1
    assert _inbox.drain_inbox_messages(
        tmp_path,
        current_stage="optimize",
    ) == ["profile only after baseline"]
    assert _inbox.count_pending_inbox_messages(tmp_path) == 0


def test_stage_targeted_event_renders_scope(tmp_path) -> None:
    _inbox.queue_inbox_message(
        tmp_path,
        "run profiler",
        source="test",
        stage="optimize",
    )
    event = __import__("json").loads(
        (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    rendered = _inbox.format_inbox_event(event)
    assert rendered is not None
    assert "stage=optimize" in rendered
