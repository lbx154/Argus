"""Persisted event-to-view recovery, including interrupted writers and rotation."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
from pathlib import Path

import pytest

from argus_skill.core import mission_view
from argus_skill.core.mission_view import _replay, _view_state
from argus_skill.life.event_log import JsonlEventSink, event_log_paths


def _start(item_id="mission", **extra):
    return {"type": "life.mission.started", "item_id": item_id, "title": "Task", "objective": "Work", "ts": 1, **extra}


def _review(index=1, item_id="mission", **extra):
    return {"type": "round.review.completed", "item_id": item_id, "status": "continue", "reason": "Add a control", "round_index": index, "ts": index + 1, **extra}


def _snapshot(root):
    return mission_view.snapshot_mission_view(root, session={}, daemon={}, roles=[], backlog=[])


def _append_raw(path, events):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(row) + "\n" for row in events))


def test_failed_projection_is_repaired_by_reader_and_duplicate_callback_is_safe(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert sink.append(_start())
    checkpoint = (tmp_path / "mission-view.json").read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(mission_view, "update_mission_view_event", lambda *a, **kw: (_ for _ in ()).throw(OSError("projection unavailable")))
        assert sink.append(_review())
    assert (tmp_path / "mission-view.json").read_bytes() == checkpoint

    view = _snapshot(tmp_path)
    assert view["review"]["rejected_attempts"] == 1
    assert view["projection_sync"]["status"] == "current"
    assert "_event_cursor" not in view
    # A late callback for an already consumed row cannot increment it again.
    mission_view.update_mission_view_event(tmp_path, _review(), logged=True)
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1
    assert all(json.loads(line).get("event_id") for line in (tmp_path / "events.jsonl").read_text().splitlines())


def test_existing_explicit_event_identity_is_preserved(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start(id="legacy-explicit-id"))
    row = json.loads((tmp_path / "events.jsonl").read_text())
    assert row["event_id"] == "legacy-explicit-id"
    assert _snapshot(tmp_path)["projection_sync"]["last_event_id"] == "legacy-explicit-id"


@pytest.mark.parametrize("after_replace", [False, True])
def test_checkpoint_failure_never_replays_on_top_of_partial_reduction(tmp_path, monkeypatch, after_replace):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    _append_raw(tmp_path / "events.jsonl", [_review()])
    write = _replay._write_unlocked

    def fail_checkpoint(root, view):
        if after_replace:
            write(root, view)
        raise OSError("checkpoint interrupted")

    for _ in range(2):
        with monkeypatch.context() as fault:
            fault.setattr(_replay, "_write_unlocked", fail_checkpoint)
            with pytest.raises(OSError, match="checkpoint interrupted"):
                _snapshot(tmp_path)
            if after_replace:
                # Once the first atomic checkpoint succeeded, the next read is
                # already current and must not invoke the failing writer again.
                assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1
                break
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1


def test_reducer_failure_retries_from_previous_persisted_view(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    checkpoint = (tmp_path / "mission-view.json").read_bytes()
    _append_raw(tmp_path / "events.jsonl", [_review()])
    reduce = _replay.reduce_mission_view_event

    def interrupted(view, event):
        reduce(view, event)
        raise OSError("reducer interrupted after mutation")

    with monkeypatch.context() as fault:
        fault.setattr(_replay, "reduce_mission_view_event", interrupted)
        with pytest.raises(OSError, match="reducer interrupted"):
            _snapshot(tmp_path)
    assert (tmp_path / "mission-view.json").read_bytes() == checkpoint
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1


def _crash_after_log(root):
    mission_view.update_mission_view_event = lambda *args, **kwargs: os._exit(29)
    JsonlEventSink(None, life_dir=Path(root)).append(_review())


def _parallel_writer(root, index):
    # Exercise the same locks and callback path used by independent daemons.
    sink = JsonlEventSink(None, life_dir=Path(root))
    sink._roll_bytes = 1
    for offset in range(3):
        sink.append(_review(index * 3 + offset + 1))


def _reentrant_callback_writer(root):
    sink = JsonlEventSink(None, life_dir=Path(root))
    update = mission_view.update_mission_view_event
    nested = False

    def callback(*args, **kwargs):
        nonlocal nested
        if not nested:
            nested = True
            sink.append(_review(2))
        return update(*args, **kwargs)

    mission_view.update_mission_view_event = callback
    sink.append(_review(1))


def _join(process):
    process.join(timeout=15)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("event writer/recovery deadlocked")


def test_process_death_after_durable_append_is_recoverable(tmp_path):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    process = mp.get_context("spawn").Process(target=_crash_after_log, args=(str(tmp_path),))
    process.start()
    _join(process)
    assert process.exitcode == 29
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1


def test_projection_callback_can_reenter_writer_without_lock_cycle(tmp_path):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    process = mp.get_context("spawn").Process(target=_reentrant_callback_writer, args=(str(tmp_path),))
    process.start()
    _join(process)
    assert process.exitcode == 0
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 2


def test_multiple_processes_and_rotations_preserve_log_order_once(tmp_path):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    processes = [mp.get_context("spawn").Process(target=_parallel_writer, args=(str(tmp_path), index)) for index in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        _join(process)
        assert process.exitcode == 0
    assert len(event_log_paths(tmp_path / "events.jsonl")) == 10
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 9
    assert mission_view.load_mission_view(tmp_path)["review"]["rejected_attempts"] == 9


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


def test_legacy_log_rebuild_is_bounded_resumable_and_does_not_double_count(tmp_path, monkeypatch):
    _append_raw(tmp_path / "events.jsonl", [_start(), *[_review(i + 1) for i in range(12)]])
    old = _view_state.empty_mission_view()
    old["bootstrapped"] = True
    old["review"]["rejected_attempts"] = 99
    (tmp_path / "mission-view.json").write_text(json.dumps(old))
    monkeypatch.setattr(_replay, "REPLAY_BYTES", 250)
    statuses = []
    for _ in range(20):
        view = _snapshot(tmp_path)
        statuses.append(view["projection_sync"]["status"])
        if statuses[-1] == "current":
            break
    assert statuses[0] == "catching_up"
    assert statuses[-1] == "current"
    assert view["review"]["rejected_attempts"] == 12
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 12


def test_snapshot_fast_path_never_opens_or_enumerates_log_history(tmp_path, monkeypatch):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    sink.append(_review())
    original_open = Path.open
    opened_logs = []

    def counted_open(path, *args, **kwargs):
        if path.name.startswith("events.jsonl"):
            opened_logs.append(path)
        return original_open(path, *args, **kwargs)

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("current snapshot must not replay or enumerate history")

    monkeypatch.setattr(Path, "open", counted_open)
    monkeypatch.setattr(_replay, "_paths", unexpected_scan)
    from argus_skill.core.mission_view import _snapshot as snapshots
    monkeypatch.setattr(snapshots, "_refresh_review_projection", unexpected_scan)
    for _ in range(3):
        assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1
    assert opened_logs == []


def test_empty_snapshot_fast_path_does_not_enumerate_or_rewrite(tmp_path, monkeypatch):
    _snapshot(tmp_path)

    def unexpected_work(*args, **kwargs):
        raise AssertionError("empty current snapshot must not enumerate or write")

    monkeypatch.setattr(_replay, "_paths", unexpected_work)
    monkeypatch.setattr(_replay, "_write_unlocked", unexpected_work)
    assert _snapshot(tmp_path)["projection_sync"]["status"] == "current"


def test_snapshot_reads_only_increment_plus_small_identity_anchors(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    _append_raw(path, [_start(), *[{"type": "session.roll", "padding": "x" * 200} for _ in range(2000)]])
    _snapshot(tmp_path)
    _append_raw(path, [_review()])
    original_open = Path.open
    bytes_read = 0

    class CountedFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, *args):
            nonlocal bytes_read
            data = self.handle.read(*args)
            bytes_read += len(data)
            return data

        def readline(self, *args):
            nonlocal bytes_read
            data = self.handle.readline(*args)
            bytes_read += len(data)
            return data

    def counted_open(candidate, *args, **kwargs):
        handle = original_open(candidate, *args, **kwargs)
        return CountedFile(handle) if candidate.name.startswith("events.jsonl") else handle

    monkeypatch.setattr(Path, "open", counted_open)
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1
    assert 0 < bytes_read < 4096
    assert path.stat().st_size > 400_000


def test_backup_inode_change_resumes_matching_content_without_reset(tmp_path):
    source, restored = tmp_path / "source", tmp_path / "restored"
    sink = JsonlEventSink(None, life_dir=source)
    sink.append(_start())
    sink.append(_review())
    shutil.copytree(source, restored)
    assert (source / "events.jsonl").stat().st_ino != (restored / "events.jsonl").stat().st_ino
    _append_raw(restored / "events.jsonl", [_review(2)])
    view = _snapshot(restored)
    assert view["review"]["rejected_attempts"] == 2
    assert view["projection_sync"]["reset_reason"] == ""


def test_in_place_truncation_rebuilds_available_history_instead_of_stale_view(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start(padding="x" * 2000))
    sink.append(_review())
    path = tmp_path / "events.jsonl"
    inode = path.stat().st_ino
    path.write_text(json.dumps(_start("replacement")) + "\n" + json.dumps(_review(item_id="replacement")) + "\n")
    assert path.stat().st_ino == inode
    view = _snapshot(tmp_path)
    assert view["mission"]["id"] == "replacement"
    assert view["review"]["rejected_attempts"] == 1
    assert view["projection_sync"]["reset_reason"] == "log_truncated"


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


def test_invalid_audit_envelope_does_not_block_later_valid_projection(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    assert sink.append(_review(ts="not-a-timestamp")) is False
    assert sink.append(_review()) is True
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


def test_canonical_replay_keeps_prior_task_review_out_of_new_owner(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start("previous"))
    sink.append(_start("current", ts=2))
    sink.append(_review(item_id="previous"))
    view = _snapshot(tmp_path)
    assert view["mission"]["id"] == "current"
    assert view["review"]["rejected_attempts"] == 0
    # Missing item_id keeps the historical "current owner" interpretation;
    # run_id/mission_id are separate identities and never substitute for it.
    review = _review()
    del review["item_id"]
    review.update(run_id="previous", mission_id="previous")
    sink.append(review)
    assert _snapshot(tmp_path)["review"]["rejected_attempts"] == 1


def test_legacy_unhashable_review_owner_cannot_block_following_valid_event(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append(_start())
    # Old/raw logs do not necessarily carry normalized event_validation data.
    _append_raw(tmp_path / "events.jsonl", [_review(item_id=[]), _review(2)])
    view = _snapshot(tmp_path)
    assert view["projection_sync"]["status"] == "current"
    assert view["review"]["rejected_attempts"] == 1


def test_direct_projection_is_explicit_and_canonical_writer_adopts_it(tmp_path):
    direct = mission_view.update_mission_view_event(tmp_path, _start("unlogged"))
    assert direct["projection_sync"]["status"] == "unlogged"
    assert _snapshot(tmp_path)["mission"]["id"] == "unlogged"
    JsonlEventSink(None, life_dir=tmp_path).append(_start("logged"))
    view = _snapshot(tmp_path)
    assert view["mission"]["id"] == "logged"
    assert view["projection_sync"]["status"] == "current"


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


@pytest.mark.parametrize("bad_fields", [
    {"ts": "unparseable"}, {"ts": []}, {"round_index": []}, {"status": "invalid-status"},
])
def test_legacy_provided_field_validation_skips_poison_and_reaches_next_event(tmp_path, bad_fields):
    JsonlEventSink(None, life_dir=tmp_path).append(_start())
    _append_raw(tmp_path / "events.jsonl", [_review(**bad_fields), _review(2)])
    view = _snapshot(tmp_path)
    assert view["projection_sync"]["status"] == "current"
    assert view["projection_sync"]["skipped_rows"] == 1
    assert view["review"]["rejected_attempts"] == 1


def test_legacy_missing_payload_fields_remain_compatible(tmp_path):
    _append_raw(tmp_path / "events.jsonl", [
        _start(), {"type": "round.review.completed", "status": "continue", "ts": 2},
    ])
    view = _snapshot(tmp_path)
    assert view["projection_sync"]["skipped_rows"] == 0
    assert view["review"]["rejected_attempts"] == 1


def test_empty_legacy_event_file_keeps_existing_projection(tmp_path):
    old = _view_state.empty_mission_view()
    old["bootstrapped"] = True
    old["mission"].update(id="kept", title="Keep the old projection", summary="Saved result")
    (tmp_path / "mission-view.json").write_text(json.dumps(old))
    (tmp_path / "events.jsonl").touch()
    view = _snapshot(tmp_path)
    assert view["mission"]["id"] == "kept"
    assert view["mission"]["summary"] == "Saved result"
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
