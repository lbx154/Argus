"""The Mission View computed from the event log, including interrupted writers and rotation."""
from __future__ import annotations

import json

import pytest

from argus.core import mission_view
from argus.core.mission_view import _replay
from argus.life.event_log import JsonlEventSink, event_log_paths


def _start(item_id="mission", **extra):
    return {"type": "life.mission.started", "item_id": item_id, "title": "Task", "objective": "Work", "ts": 1, **extra}


def _review(index=1, item_id="mission", **extra):
    return {"type": "round.review.completed", "item_id": item_id, "status": "continue", "reason": "Add a control", "round_index": index, "ts": index + 1, **extra}


def _snapshot(root):
    return mission_view.snapshot_mission_view(root, session={}, daemon={}, roles=[], backlog=[])


def _append_raw(path, events):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(row) + "\n" for row in events))


def test_view_spans_retained_generations(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink._roll_bytes = 1
    sink.append(_start())
    for index in range(5):
        sink.append(_review(index + 1))
    assert len(event_log_paths(tmp_path / "events.jsonl")) > 1
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 5
    assert _snapshot(tmp_path)["projection_sync"]["status"] == "current"


def test_the_view_is_never_written_and_rebuilds_to_the_same_result(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink._roll_bytes = 1
    sink.append(_start())
    sink.append(_review(1))
    first = mission_view.load_mission_view(tmp_path)
    sink.append(_review(2))
    incremental = mission_view.load_mission_view(tmp_path)
    assert first["review"]["rejected_attempts"] == 1
    assert incremental["review"]["rejected_attempts"] == 2
    assert not (tmp_path / "mission-view.json").exists()
    assert not (tmp_path / "mission-view.lock").exists()

    _replay.forget_cached_views()
    rebuilt = mission_view.load_mission_view(tmp_path)
    for view in (incremental, rebuilt):
        view.pop("updated_at")
        view.pop("_event_cursor")
    assert rebuilt == incremental


def test_a_cached_view_reads_only_appended_events(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    sink.append(_review(1))
    mission_view.load_mission_view(tmp_path)
    seen = []
    reduce = _replay.reduce_mission_view_event

    def counted(view, event):
        seen.append(event["type"])
        return reduce(view, event)

    monkeypatch.setattr(_replay, "reduce_mission_view_event", counted)
    assert mission_view.load_mission_view(tmp_path)["review"]["rejected_attempts"] == 1
    assert seen == []
    sink.append(_review(2))
    assert mission_view.load_mission_view(tmp_path)["review"]["rejected_attempts"] == 2
    assert seen == ["round.review.completed"]


def test_a_caller_cannot_change_the_cached_view(tmp_path):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    mission_view.load_mission_view(tmp_path)["mission"]["id"] = "edited"
    assert mission_view.load_mission_view(tmp_path)["mission"]["id"] == "mission"


def test_partial_last_line_waits_and_next_append_does_not_swallow_valid_event(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b'{"type":"round.review.completed"')
    assert _snapshot(tmp_path)["projection_sync"]["status"] == "waiting_for_line"
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 0
    sink.append(_review())
    view = _snapshot(tmp_path)
    assert view["projection_sync"]["status"] == "current"
    assert view["projection_sync"]["skipped_rows"] == 1
    assert view["review"]["rejected_attempts"] == 1


@pytest.mark.parametrize("partial", [False, True])
def test_oversized_rows_are_skipped_in_bounded_steps(tmp_path, monkeypatch, partial):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    path = tmp_path / "events.jsonl"
    large = json.dumps(_review(padding="x" * 4000)).encode()
    with path.open("ab") as handle:
        handle.write(large[:-1] if partial else large + b"\n")
    original = path.read_bytes()
    monkeypatch.setattr(_replay, "MAX_EVENT_BYTES", 512)
    monkeypatch.setattr(_replay, "REPLAY_BYTES", 600)
    passes = []
    reconcile = _replay.reconcile_unlocked
    monkeypatch.setattr(_replay, "reconcile_unlocked", lambda *a: passes.append(1) or reconcile(*a))
    view = _snapshot(tmp_path)
    assert len(passes) > 1  # bounded passes, run until the log is consumed
    assert view["projection_sync"]["status"] == ("waiting_for_line" if partial else "current")
    assert path.read_bytes() == original
    assert view["projection_sync"]["oversized_rows"] == 1
    assert view["review"]["rejected_attempts"] == 0
    sink.append(_review(2))
    final = _snapshot(tmp_path)
    assert final["projection_sync"]["status"] == "current"
    assert final["projection_sync"]["oversized_rows"] == 1
    assert final["projection_sync"]["skipped_rows"] == 1
    assert final["review"]["rejected_attempts"] == 1


def test_a_view_whose_history_disappeared_is_an_explicit_error(tmp_path):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    _snapshot(tmp_path)
    (tmp_path / "events.jsonl").unlink()
    with pytest.raises(_replay.MissionViewReplayError, match="missing event history"):
        _snapshot(tmp_path)
