"""Transport and map index behavior at file, rotation, and byte-budget edges."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import portalocker

from argus.apps.cli._follow import _merge_recent_event_rows
from argus.core import jsonl_reader
from argus.webapi import map_history
from argus.webapi.server import tail_events


def _append(path, *numbers):
    with path.open("a", encoding="utf-8") as stream:
        stream.write("".join(json.dumps({"seq": number, "type": "event"}) + "\n" for number in numbers))


def _rotate(path):
    previous = path.with_name(path.name + ".1")
    if previous.exists():
        number = 2
        while path.with_name(path.name + f".{number}").exists():
            number += 1
        previous.rename(path.with_name(path.name + f".{number}"))
    path.rename(previous)
    path.touch()


def test_web_tail_drains_unread_bytes_through_multiple_rotations(tmp_path):
    path = tmp_path / "events.jsonl"
    _append(path, 0)

    async def read():
        stream = tail_events(tmp_path, replay_limit=1, poll_interval=0.001)
        try:
            assert (await anext(stream))["seq"] == 0
            for number in range(1, 5):
                _append(path, number)
                _rotate(path)
            _append(path, 5)
            return [(await asyncio.wait_for(anext(stream), timeout=1))["seq"] for _ in range(5)]
        finally:
            await stream.aclose()

    assert asyncio.run(read()) == [1, 2, 3, 4, 5]


def test_rotation_between_bounded_reads_does_not_reset_unconsumed_offset(tmp_path):
    path = tmp_path / "events.jsonl"
    _append(path, 0)
    reader = jsonl_reader.JsonlTail(path)
    try:
        reader.start(replay_limit=0, merge_rows=_merge_recent_event_rows)
        _append(path, 1, 2, 3)
        assert [row["seq"] for row in reader.read_batch(byte_limit=1).rows] == [1]
        _rotate(path)
        _append(path, 4)
        seen = []
        for _ in range(6):
            seen.extend(row["seq"] for row in reader.read_batch(byte_limit=1).rows)
        assert seen == [2, 3, 4]
    finally:
        reader.close()


def test_tail_replay_fills_from_multiple_retained_generations(tmp_path):
    path = tmp_path / "events.jsonl"
    for number in range(1, 5):
        _append(path, number)
        if number < 4:
            _rotate(path)
    reader = jsonl_reader.JsonlTail(path)
    try:
        assert [row["seq"] for row in reader.start(replay_limit=4, merge_rows=_merge_recent_event_rows)] == [1, 2, 3, 4]
    finally:
        reader.close()


def test_initial_replay_has_a_file_open_bound_even_for_empty_archives(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    for number in range(2, 102):
        path.with_name(f"events.jsonl.{number}").touch()
    _append(path, 1)
    original_open = jsonl_reader.open_jsonl_generation
    opened = []

    def counted_open(candidate, *args, **kwargs):
        if candidate.name.startswith("events.jsonl"):
            opened.append(candidate)
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(jsonl_reader, "open_jsonl_generation", counted_open)
    reader = jsonl_reader.JsonlTail(path)
    try:
        assert reader.start(replay_limit=40, merge_rows=_merge_recent_event_rows) == [{"seq": 1, "type": "event"}]
        assert len(opened) <= jsonl_reader.REPLAY_GENERATIONS + 1
    finally:
        reader.close()


def test_first_events_rotated_before_first_poll_are_still_new(tmp_path):
    path = tmp_path / "events.jsonl"
    reader = jsonl_reader.JsonlTail(path)
    try:
        assert reader.start(replay_limit=0, merge_rows=_merge_recent_event_rows) == []
        for number in range(1, 4):
            _append(path, number)
            if number < 3:
                _rotate(path)
        seen = []
        for _ in range(5):
            seen.extend(row["seq"] for row in reader.read_batch().rows)
        assert seen == [1, 2, 3]
    finally:
        reader.close()


def test_stable_tail_poll_does_not_open_logs_or_enumerate_generations(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    _append(path, 0)
    reader = jsonl_reader.JsonlTail(path)
    reader.start(replay_limit=0, merge_rows=_merge_recent_event_rows)
    reader.read_batch()
    original_open = jsonl_reader.open_jsonl_generation

    def checked_open(candidate, *args, **kwargs):
        assert not candidate.name.startswith("events.jsonl")
        return original_open(candidate, *args, **kwargs)

    def unexpected_paths(*args):
        raise AssertionError("stable poll enumerated event history")

    monkeypatch.setattr(jsonl_reader, "open_jsonl_generation", checked_open)
    monkeypatch.setattr(jsonl_reader, "retained_jsonl_paths", unexpected_paths)
    try:
        for _ in range(5):
            assert reader.read_batch().rows == []
    finally:
        reader.close()


def test_web_tail_oversized_partial_cannot_swallow_a_later_valid_event(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_reader, "MAX_JSONL_RECORD_BYTES", 128)
    path = tmp_path / "events.jsonl"
    _append(path, 0)
    reader = jsonl_reader.JsonlTail(path)
    try:
        reader.start(replay_limit=0, merge_rows=_merge_recent_event_rows)
        with path.open("ab") as stream:
            stream.write(b'x' * 4096)
        for _ in range(80):
            batch = reader.read_batch(byte_limit=64)
            assert batch.bytes_read <= 64 + 128 + 1
            if batch.waiting_for_line:
                break
        assert reader.discarding
        with path.open("ab") as stream:
            stream.write(b'\n{"seq":1}\n')
        assert reader.read_batch(byte_limit=64).rows == [{"seq": 1}]
    finally:
        reader.close()


def test_stream_close_releases_its_open_generation(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    _append(path, 0)
    original = jsonl_reader.JsonlTail
    readers = []

    def tracked_reader(event_path):
        reader = original(event_path)
        readers.append(reader)
        return reader

    monkeypatch.setattr(jsonl_reader, "JsonlTail", tracked_reader)

    async def read():
        stream = tail_events(tmp_path, replay_limit=1)
        assert (await anext(stream))["seq"] == 0
        assert readers[0].stream is not None
        await stream.aclose()

    asyncio.run(read())
    assert readers[0].stream is None


def test_in_place_truncation_restarts_at_the_new_file_contents(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"seq": 0, "padding": "x" * 200}) + "\n")
    reader = jsonl_reader.JsonlTail(path)
    try:
        reader.start(replay_limit=0, merge_rows=_merge_recent_event_rows)
        path.write_text('{"seq":1}\n')
        assert reader.read_batch().rows == [{"seq": 1}]
    finally:
        reader.close()


def _map_value():
    return {"id": "test", "tasks": [{"id": "A", "title": "Task", "ts": 1, "status": "running"}]}


def _map_event(event_id):
    return {"type": "round.review.completed", "event_id": event_id, "item_id": "A", "ts": 2, "status": "continue"}


def test_history_long_partial_makes_persisted_progress_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_reader, "MAX_JSONL_RECORD_BYTES", 256)
    monkeypatch.setattr(map_history, "PAGE_BYTES", 64)
    life = tmp_path / "life"
    life.mkdir()
    path = life / "events.jsonl"
    path.write_bytes(b'x' * 4096)
    cursor = None
    progress = []
    for _ in range(80):
        page = map_history.history_page(tmp_path, life, _map_value(), cursor)
        cursor = page["history_cursor"]
        progress.append(page["history_progress"]["loaded_bytes"])
        if not page["history_loading"]:
            break
    assert progress == sorted(set(progress))
    assert progress[-1] == 4096
    assert page["history_progress"]["waiting_for_line"]
    assert page["history_progress"]["oversized_rows"] == 1
    with path.open("ab") as stream:
        stream.write(b'\n' + json.dumps(_map_event("good")).encode() + b'\n')
    for _ in range(4):
        page = map_history.history_page(tmp_path, life, _map_value(), cursor)
        cursor = page["history_cursor"]
        if page["events"]:
            break
    assert [event["id"] for event in page["events"]] == ["good"]
    assert page["history_progress"]["oversized_rows"] == 1
    with sqlite3.connect(map_history.history_path(tmp_path, life)) as db:
        state = json.loads(db.execute("SELECT value FROM metadata WHERE key='state'").fetchone()[0])
    assert state["files"][0]["discarding"] is False


def test_history_normalization_runs_after_releasing_event_lock(tmp_path, monkeypatch):
    life = tmp_path / "life"
    life.mkdir()
    path = life / "events.jsonl"
    path.write_text(json.dumps(_map_event("good")) + "\n")
    normalize = map_history.normalize_events
    observed = []

    def outside_lock(*args, **kwargs):
        with (life / "events.lock").open("a+b") as handle:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            observed.append(True)
            portalocker.unlock(handle)
        return normalize(*args, **kwargs)

    monkeypatch.setattr(map_history, "normalize_events", outside_lock)
    page = map_history.history_page(tmp_path, life, _map_value(), None)
    assert observed == [True]
    assert [event["id"] for event in page["events"]] == ["good"]


def test_drained_history_does_not_reopen_immutable_generations(tmp_path, monkeypatch):
    life = tmp_path / "life"
    life.mkdir()
    path = life / "events.jsonl"
    for index in range(5):
        path.write_text(json.dumps(_map_event(f"e{index}")) + "\n")
        _rotate(path)
    page = map_history.history_page(tmp_path, life, _map_value(), None)
    assert len(page["events"]) == 5
    original_open = Path.open

    def checked_open(candidate, *args, **kwargs):
        assert not candidate.name.startswith("events.jsonl"), "unchanged history reopened a log"
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    for _ in range(3):
        next_page = map_history.history_page(tmp_path, life, _map_value(), page["history_cursor"])
        assert next_page["events"] == []


def test_history_page_has_a_measured_read_bound_for_a_large_unterminated_row(tmp_path, monkeypatch):
    monkeypatch.setattr(map_history, "PAGE_BYTES", 64 * 1024)
    life = tmp_path / "life"
    life.mkdir()
    path = life / "events.jsonl"
    path.write_bytes(b'x' * (3 * jsonl_reader.MAX_JSONL_RECORD_BYTES))
    original_open = Path.open
    consumed = 0

    class CountedLog:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, amount=-1):
            nonlocal consumed
            assert amount >= 0
            value = self.stream.read(amount)
            consumed += len(value)
            return value

        def readline(self, amount=-1):
            nonlocal consumed
            assert 0 <= amount <= jsonl_reader.MAX_JSONL_RECORD_BYTES + 1
            value = self.stream.readline(amount)
            consumed += len(value)
            return value

    def counted_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return CountedLog(stream) if candidate == path else stream

    monkeypatch.setattr(Path, "open", counted_open)
    page = map_history.history_page(tmp_path, life, _map_value(), None)
    assert page["history_loading"]
    assert page["history_progress"]["oversized_rows"] == 1
    # The one-file page adds a 128-byte checkpoint anchor to the batch bound.
    assert consumed <= map_history.PAGE_BYTES + jsonl_reader.MAX_JSONL_RECORD_BYTES + 129
    assert consumed < path.stat().st_size


def test_rotation_after_bounded_read_does_not_reset_history_index(tmp_path, monkeypatch):
    life = tmp_path / "life"
    life.mkdir()
    path = life / "events.jsonl"
    path.write_text(json.dumps(_map_event("before")) + "\n")
    reading, attempted, rotated = threading.Event(), threading.Event(), threading.Event()
    failures = []

    def writer():
        try:
            assert reading.wait(2)
            attempted.set()
            with jsonl_reader.event_log_read_lock(path):
                _rotate(path)
                path.write_text(json.dumps(_map_event("after")) + "\n")
            rotated.set()
        except Exception as exc:
            failures.append(exc)

    read_batch = map_history.read_jsonl_batch
    normalize = map_history.normalize_events

    def observed_read(*args, **kwargs):
        reading.set()
        assert attempted.wait(2)
        return read_batch(*args, **kwargs)

    def observed_normalize(*args, **kwargs):
        assert rotated.wait(2), "normalization still held the event log lock"
        return normalize(*args, **kwargs)

    worker = threading.Thread(target=writer, daemon=True)
    worker.start()
    with monkeypatch.context() as patch:
        patch.setattr(map_history, "read_jsonl_batch", observed_read)
        patch.setattr(map_history, "normalize_events", observed_normalize)
        first = map_history.history_page(tmp_path, life, _map_value(), None)
    worker.join(2)
    assert not worker.is_alive() and not failures
    second = map_history.history_page(tmp_path, life, _map_value(), first["history_cursor"])
    assert [event["id"] for event in first["events"]] == ["before"]
    assert [event["id"] for event in second["events"]] == ["after"]
    assert not second["reset_history"]
