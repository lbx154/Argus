"""Transport and map index behavior at file, rotation, and byte-budget edges."""
from __future__ import annotations

import json
import sqlite3

from argus.apps.cli._follow import _merge_recent_event_rows
from argus.core import jsonl_reader
from argus.webapi import map_history


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
