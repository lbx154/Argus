"""Persisted event-to-view recovery, including interrupted writers and rotation."""
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


def test_failed_projection_catches_up_across_retained_generations(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink._roll_bytes = 1
    sink.append(_start())
    with monkeypatch.context() as fault:
        fault.setattr(mission_view, "update_mission_view_event", lambda *a, **kw: None)
        for index in range(5):
            sink.append(_review(index + 1))
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 5
    assert _snapshot(tmp_path)["projection_sync"]["status"] == "current"


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
def test_oversized_rows_are_skipped_in_bounded_persisted_steps(tmp_path, monkeypatch, partial):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    path = tmp_path / "events.jsonl"
    large = json.dumps(_review(padding="x" * 4000)).encode()
    with path.open("ab") as handle:
        handle.write(large[:-1] if partial else large + b"\n")
    original = path.read_bytes()
    monkeypatch.setattr(_replay, "MAX_EVENT_BYTES", 512)
    monkeypatch.setattr(_replay, "REPLAY_BYTES", 600)
    statuses = []
    for _ in range(20):
        view = _snapshot(tmp_path)
        statuses.append(view["projection_sync"]["status"])
        if statuses[-1] in {"current", "waiting_for_line"}:
            break
    assert statuses[0] == "catching_up"
    assert statuses[-1] == ("waiting_for_line" if partial else "current")
    assert path.read_bytes() == original
    assert view["projection_sync"]["oversized_rows"] == 1
    assert view["review"]["rejected_attempts"] == 0
    sink.append(_review(2))
    final = _snapshot(tmp_path)
    assert final["projection_sync"]["status"] == "current"
    assert final["projection_sync"]["oversized_rows"] == 1
    assert final["projection_sync"]["skipped_rows"] == 1
    assert final["review"]["rejected_attempts"] == 1


@pytest.mark.parametrize("existing_log", [False, True])
@pytest.mark.parametrize("raw_tail", [False, True])
def test_reader_adopts_canonical_append_even_when_first_callback_fails(tmp_path, monkeypatch, existing_log, raw_tail):
    if existing_log:
        JsonlEventSink(None, life_dir=tmp_path).append(_start("earlier-log"))
    mission_view.update_mission_view_event(tmp_path, _start("projection-only"))
    assert _snapshot(tmp_path)["mission"]["id"] == "projection-only"
    with monkeypatch.context() as fault:
        def failed_callback(*args, **kwargs):
            raise OSError("first canonical projection failed")

        fault.setattr(mission_view, "update_mission_view_event", failed_callback)
        assert JsonlEventSink(None, life_dir=tmp_path).append(_start("durable-log", ts=5))
    if raw_tail:
        _append_raw(tmp_path / "events.jsonl", [{"type": "session.roll", "text": "legacy tail" * 50}])
    view = _snapshot(tmp_path)
    assert view["mission"]["id"] == "durable-log"
    assert view["projection_sync"]["status"] == "current"


def test_canonical_takeover_probe_checkpoints_before_marker_and_survives_rotation(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start("earlier"))
    mission_view.update_mission_view_event(tmp_path, _start("projection-only"))
    _append_raw(tmp_path / "events.jsonl", [{"type": "session.roll", "padding": "x" * 2000}])
    sink._roll_bytes = 1
    with monkeypatch.context() as fault:
        fault.setattr(mission_view, "update_mission_view_event", lambda *args, **kwargs: None)
        sink.append(_start("durable-log", ts=5))
    _append_raw(tmp_path / "events.jsonl", [{"type": "session.roll", "padding": "y" * 2000}])
    monkeypatch.setattr(_replay, "REPLAY_BYTES", 128)
    first = _snapshot(tmp_path)
    assert first["projection_sync"].get("probe_pending") is True
    for _ in range(70):
        view = _snapshot(tmp_path)
        if view["projection_sync"]["status"] == "current":
            break
    assert view["mission"]["id"] == "durable-log"
    assert view["projection_sync"]["status"] == "current"


@pytest.mark.parametrize("old_revision", [None, 0])
@pytest.mark.parametrize("replay_budget", [1, _replay.REPLAY_BYTES])
def test_old_checkpoint_replays_completed_objective_with_current_semantics(tmp_path, monkeypatch, old_revision, replay_budget):
    objective = "Compare both groups at the same batch size."
    wrapped = "[BOUNDED TASK CONTEXT] Prior goal\n[CURRENT OPERATOR MESSAGE]\n" + objective
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink._roll_bytes = 1
    assert sink.append(_start())
    assert sink.append({"type": "life.manager.intent.completed", "item_id": "mission", "ts": 2,
                        "objective": wrapped, "execution_task": objective,
                        "vertical": "research", "kind": "research", "stages": []})
    assert sink.append(_review(index=3))
    logs = {path: path.read_bytes() for path in event_log_paths(tmp_path / "events.jsonl")}
    view_file = tmp_path / "mission-view.json"
    old = json.loads(view_file.read_text())
    assert old["mission"]["objective"] == objective
    assert old["last_event_ts"] > 2
    old["mission"].update(title=wrapped, objective=wrapped)
    old["_event_cursor"].pop("projection_revision", None)
    if old_revision is not None:
        old["_event_cursor"]["projection_revision"] = old_revision
    view_file.write_text(json.dumps(old))

    monkeypatch.setattr(_replay, "REPLAY_BYTES", replay_budget)
    for attempt in range(6):
        view = mission_view.load_mission_view(tmp_path)
        if view["projection_sync"]["status"] == "current":
            break
    else:
        pytest.fail("Old checkpoint kept restarting instead of completing bounded replay")
    assert attempt > 0 if replay_budget == 1 else attempt == 0
    assert view["schema_version"] == old["schema_version"]
    assert view["mission"]["title"] == view["mission"]["objective"] == objective
    assert view["review"]["rejected_attempts"] == 1
    assert {path: path.read_bytes() for path in logs} == logs
    checkpoint = view_file.read_bytes()

    def no_replay(*_args, **_kwargs):
        pytest.fail("A current checkpoint must not replay unchanged events again")

    monkeypatch.setattr(_replay, "reduce_mission_view_event", no_replay)
    assert _snapshot(tmp_path)["mission"]["objective"] == objective
    assert view_file.read_bytes() == checkpoint


@pytest.mark.parametrize("field,value", [("version", True), ("source_name", ".."), ("source_name", "other.jsonl")])
def test_invalid_cursor_version_and_source_names_are_rejected(tmp_path, field, value):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    view_file = tmp_path / "mission-view.json"
    view = json.loads(view_file.read_text())
    cursor = view["_event_cursor"]
    if field == "source_name":
        cursor["source"]["name"] = value
    else:
        cursor[field] = value
    view_file.write_text(json.dumps(view))
    with pytest.raises(_replay.MissionViewReplayError, match="invalid"):
        _snapshot(tmp_path)


@pytest.mark.parametrize("legacy_revision", [False, True])
def test_invalid_cursor_and_missing_history_are_explicit_errors(tmp_path, legacy_revision):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    view_file = tmp_path / "mission-view.json"
    valid = json.loads(view_file.read_text())
    if legacy_revision:
        valid["_event_cursor"].pop("projection_revision", None)
    invalid = dict(valid)
    invalid["_event_cursor"] = {"version": 2}
    view_file.write_text(json.dumps(invalid))
    with pytest.raises(_replay.MissionViewReplayError, match="invalid"):
        _snapshot(tmp_path)
    view_file.write_text(json.dumps(valid))
    (tmp_path / "events.jsonl").unlink()
    with pytest.raises(_replay.MissionViewReplayError, match="missing event history"):
        _snapshot(tmp_path)
