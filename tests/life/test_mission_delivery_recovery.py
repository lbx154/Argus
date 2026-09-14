"""Crash, acknowledgement, and post-completion failures on real persisted state."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime_backends import _Outcome
from argus.core.event_catalog import EventType
from argus.core.jsonl_reader import MAX_JSONL_RECORD_BYTES
from argus.life import memory as memory_module
from argus.life.event_log import JsonlEventSink, event_log_paths
from argus.life.memory import Backlog, BacklogItem, LifeMemory
from argus.life.mission_delivery import drain_mission_deliveries, prepare_mission_delivery
from argus.life.mission_event_index import (
    MAX_INDEX_RECORD_BYTES,
    MissionEventIndex,
    mission_event_index,
)
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical


def _rows(root: Path):
    rows = []
    for path in event_log_paths(root / "events.jsonl"):
        if path.is_file():
            for line in path.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return [row for row in rows if row.get("type") == EventType.LIFE_MISSION_COMPLETED]


def _claimed(backlog):
    backlog.add(BacklogItem.new(title="checked artifact", objective="Produce the checked artifact."))
    item = backlog.claim_next()
    record = prepare_mission_delivery(
        item=item,
        event={"type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id,
               "status": "done", "success": True, "summary": "Artifact verified."},
        result={"item_id": item.id, "status": "done", "success": True,
                "summary": "Artifact verified."},
    )
    return item, record


@pytest.mark.parametrize("boundary", ["commit", "live", "outbox", "outbox_written", "clear"])
def test_terminal_and_delivery_recover_together_at_each_disk_boundary(tmp_path, monkeypatch, boundary):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    rewrite = memory_module._atomic_rewrite_jsonl

    def interrupted(path, rows):
        rows = list(rows)
        is_delivery = path.parent == backlog._mission_deliveries_path
        if boundary == "live" and path == backlog.path:
            raise OSError("crashed after archive fsync")
        if boundary == "outbox" and is_delivery:
            raise OSError("crashed before delivery materialization")
        if boundary == "clear" and path == backlog._commit_path and rows == [{"version": 1}]:
            raise OSError("crashed before commit acknowledgement")
        rewrite(path, rows)
        if boundary == "commit" and path == backlog._commit_path:
            raise OSError("crashed after commit fsync")
        if boundary == "outbox_written" and is_delivery:
            raise OSError("crashed after delivery fsync")

    with monkeypatch.context() as fault:
        fault.setattr(memory_module, "_atomic_rewrite_jsonl", interrupted)
        with pytest.raises(OSError):
            backlog.update(item.id, status="done", _mission_delivery=record)
    restarted = Backlog(backlog.path)
    pending = restarted.pending_mission_deliveries()
    assert [row["id"] for row in pending] == [record["id"]]
    assert restarted.claim_next() is None
    archived = restarted.history()
    assert len(archived) == 1 and archived[0].status == "done"
    assert archived[0].mission_result == record["result"]
    assert archived[0].outcome == {}  # Public outcome dimensions are not a nested outbox.
    assert restarted._mission_deliveries_path in restarted.storage_paths
    assert len(restarted.archive_path.read_text().splitlines()) == 1
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert drain_mission_deliveries(restarted, sink.handle_event)
    assert restarted.pending_mission_deliveries() == []
    assert len(_rows(tmp_path)) == 1


def test_process_exit_after_commit_recovers_without_reexecuting(tmp_path):
    script = r'''
import os, sys
from pathlib import Path
from argus.life import memory as module
from argus.life.memory import Backlog, BacklogItem
from argus.life.mission_delivery import prepare_mission_delivery
backlog = Backlog(Path(sys.argv[1]) / "backlog.jsonl")
backlog.add(BacklogItem.new(title="artifact", objective="produce it"))
item = backlog.claim_next()
record = prepare_mission_delivery(item=item,
    event={"type":"life.mission.completed", "item_id":item.id, "status":"done"},
    result={"item_id":item.id, "status":"done", "summary":"verified"})
rewrite = module._atomic_rewrite_jsonl
def crash(path, rows):
    rows = list(rows)
    rewrite(path, rows)
    if path == backlog._commit_path and rows[0].get("version") == 2:
        os._exit(73)
module._atomic_rewrite_jsonl = crash
backlog.update(item.id, status="done", _mission_delivery=record)
'''
    process = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}, timeout=10,
    )
    assert process.returncode == 73, process.stderr
    backlog = Backlog(tmp_path / "backlog.jsonl")
    assert backlog.claim_next() is None
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert drain_mission_deliveries(backlog, sink.handle_event)
    assert len(_rows(tmp_path)) == 1
    assert backlog.history()[0].mission_result["summary"] == "verified"


def _supervisor(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software")
    memory = LifeMemory.open(tmp_path / "life")
    observed = []
    sink = JsonlEventSink(SimpleNamespace(handle_event=lambda event: observed.append(event)), life_dir=memory.root)

    class Runner:
        calls = 0

        def execute(self, **_kwargs):
            self.calls += 1
            return _Outcome(True, "done", final_message="Artifact verified.")

    runner = Runner()
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
    )
    monkeypatch.setattr(supervisor, "_evolve_runtime_skills_after_mission", lambda **_kwargs: None)
    item = memory.backlog.add(BacklogItem.new(
        title="checked artifact", objective="Produce the checked artifact.", iterate=False,
        manager_decision={"routed": True, "vertical": "software"},
    ))
    return supervisor, runner, item, observed


def test_sink_failure_keeps_completion_pending_and_holds_new_work(tmp_path, monkeypatch):
    supervisor, runner, item, observed = _supervisor(tmp_path, monkeypatch)
    append = JsonlEventSink._append
    failing = True
    rejected = []

    def append_or_fail(sink, event):
        if failing and event.get("mission_delivery_id"):
            rejected.append(event["mission_delivery_id"])
            return False
        return append(sink, event)

    # Inject at the durable writer, beneath the supervision sink decorator.
    monkeypatch.setattr(JsonlEventSink, "_append", append_or_fail)
    result = supervisor.tick()
    assert result["success"] is True
    assert runner.calls == 1
    assert len(rejected) == 1
    stored = supervisor.memory.backlog.history()[0]
    assert stored.status == "done" and stored.mission_result == result
    assert len(supervisor.memory.backlog.pending_mission_deliveries()) == 1
    summary = supervisor.run()
    assert summary["stopped_by"] == "mission_delivery_pending"
    assert summary["missions_run"] == 0 and runner.calls == 1
    failing = False
    summary = supervisor.run()
    assert summary["stopped_by"] == "backlog_empty"
    assert summary["missions_run"] == 0 and runner.calls == 1
    assert len(_rows(supervisor.memory.root)) == 1
    assert len([row for row in observed if row.get("type") == EventType.LIFE_MISSION_COMPLETED]) == 1


def test_delivery_ack_failure_cannot_duplicate_history_or_notifications(tmp_path, monkeypatch):
    supervisor, runner, item, observed = _supervisor(tmp_path, monkeypatch)
    unlink = Path.unlink

    def fail_ack(path, *args, **kwargs):
        if path.parent == supervisor.memory.backlog._mission_deliveries_path and path.suffix == ".json":
            raise OSError("acknowledgement failed after event fsync")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "unlink", fail_ack)
        assert supervisor.tick()["success"] is True
    assert len(_rows(supervisor.memory.root)) == 1
    assert len(supervisor.memory.backlog.pending_mission_deliveries()) == 1
    mission_event_index.cache_clear()  # A fresh process must reach the same answer.
    assert supervisor.run()["missions_run"] == 0
    assert runner.calls == 1
    assert len(_rows(supervisor.memory.root)) == 1
    assert len(supervisor.memory.journal.tail_settlements(10)) == 1
    assert len([row for row in observed if row.get("type") == EventType.LIFE_MISSION_COMPLETED]) == 1
    assert supervisor.memory.backlog.pending_mission_deliveries() == []


def test_recovery_fills_ui_receipt_if_process_failed_after_completion_append(tmp_path, monkeypatch):
    from argus.core.transcript import read_turns

    supervisor, runner, item, observed = _supervisor(tmp_path, monkeypatch)
    with monkeypatch.context() as fault:
        fault.setattr(
            supervisor, "_publish_mission_completion_message",
            lambda _event: (_ for _ in ()).throw(OSError("crashed before UI receipt")),
        )
        assert supervisor.tick()["success"]
    assert len(_rows(supervisor.memory.root)) == 1
    assert not read_turns(supervisor.memory.root)
    assert len(supervisor.memory.backlog.pending_mission_deliveries()) == 1
    assert supervisor.run()["missions_run"] == 0
    receipts = [turn for turn in read_turns(supervisor.memory.root) if turn.get("mission_result")]
    assert len(receipts) == 1
    assert receipts[0]["message_id"] == f"mission-result-{_rows(supervisor.memory.root)[0]['mission_delivery_id']}"
    assert len(_rows(supervisor.memory.root)) == 1
    assert runner.calls == 1


def test_v2_commit_rejects_delivery_not_bound_to_its_result(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    with monkeypatch.context() as fault:
        fault.setattr(backlog, "_apply_commit", lambda _record: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError):
            backlog.update(item.id, status="done", _mission_delivery=record)
    committed = json.loads(backlog._commit_path.read_text())
    committed["mission_deliveries"][0]["result"]["summary"] = "not the committed result"
    backlog._commit_path.write_text(json.dumps(committed))
    with pytest.raises(RuntimeError, match="invalid backlog commit record"):
        Backlog(backlog.path).claim_next()


def test_index_ack_failure_recovers_by_offset_even_after_rotation(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    finish = MissionEventIndex.finish

    with monkeypatch.context() as fault:
        fault.setattr(MissionEventIndex, "finish", lambda *_args: (_ for _ in ()).throw(OSError("index ack lost")))
        assert not drain_mission_deliveries(backlog, sink.handle_event)
    assert len(_rows(tmp_path)) == 1
    sink._roll_bytes = 1
    assert sink.append({"type": "life.status", "text": "after completion"})
    assert (tmp_path / "events.jsonl.1").exists()
    mission_event_index.cache_clear()
    monkeypatch.setattr(MissionEventIndex, "finish", finish)
    monkeypatch.setattr(MissionEventIndex, "_rebuild", lambda _self: pytest.fail("retry scanned full history"))
    _require_bounded_receipt_reads(monkeypatch, tmp_path)
    assert drain_mission_deliveries(backlog, sink.handle_event)
    assert len(_rows(tmp_path)) == 1


def test_copied_backup_recovers_pending_receipt_without_duplicate_completion(tmp_path, monkeypatch):
    original = tmp_path / "original"
    backlog = Backlog(original / "backlog.jsonl")
    item, record = _claimed(backlog)
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=original)
    with monkeypatch.context() as fault:
        fault.setattr(MissionEventIndex, "finish", lambda *_args: (_ for _ in ()).throw(OSError("index ack lost")))
        assert not drain_mission_deliveries(backlog, sink.handle_event)
    assert len(_rows(original)) == 1
    restored = tmp_path / "restored"
    shutil.copytree(original, restored)
    assert (original / "events.jsonl").stat().st_ino != (restored / "events.jsonl").stat().st_ino
    mission_event_index.cache_clear()
    rebuild = MissionEventIndex._rebuild
    rebuilds = []

    def counted_rebuild(index):
        rebuilds.append(index.root)
        return rebuild(index)

    monkeypatch.setattr(MissionEventIndex, "_rebuild", counted_rebuild)
    recovered = Backlog(restored / "backlog.jsonl")
    restored_sink = JsonlEventSink(None, life_dir=restored)
    assert drain_mission_deliveries(recovered, restored_sink.handle_event)
    assert recovered.claim_next() is None
    assert recovered.pending_mission_deliveries() == []
    assert len(recovered.history()) == 1
    assert len(_rows(restored)) == 1
    assert mission_event_index(str(restored.resolve())).contains(record["id"])
    assert rebuilds == [restored]


@pytest.mark.parametrize("invalid", [
    {"mission_delivery_id": "invalid"},
    {"mission_delivery_id": "g" * 64},
    {"mission_delivery_id": 123},
    {"mission_delivery_id": ""},
    {"mission_delivery_id": None},
    {"type": "life.status"},
    {"event_id": "unrelated-event"},
    {"event_schema_version": -1},
])
def test_invalid_delivery_envelope_cannot_poison_later_completion(tmp_path, invalid):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    index = mission_event_index(str(tmp_path.resolve()))
    index.refresh()
    before = index.path.read_bytes()
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert not sink.append({**record["event"], **invalid})
    assert index.path.read_bytes() == before
    assert not (tmp_path / "events.jsonl").exists()
    backlog.update(item.id, status="done", _mission_delivery=record)
    assert drain_mission_deliveries(backlog, sink.handle_event)
    assert backlog.pending_mission_deliveries() == []
    assert [row["mission_delivery_id"] for row in _rows(tmp_path)] == [record["id"]]


@pytest.mark.parametrize("restoration", ["partial", "earlier", "copied_earlier", "same_size"])
def test_stale_written_receipt_cannot_ack_missing_completion(tmp_path, monkeypatch, restoration):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert sink.append({"type": "life.status", "text": "before completion"})
    event_path = tmp_path / "events.jsonl"
    previous = event_path.read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(backlog, "acknowledge_mission_delivery", lambda _key: (_ for _ in ()).throw(OSError("ack lost")))
        assert not drain_mission_deliveries(backlog, sink.handle_event)
    canonical = event_path.read_bytes()
    index = mission_event_index(str(tmp_path.resolve()))
    assert index.rows[record["id"]]["state"] == "written"
    if restoration == "partial":
        event_path.write_bytes(canonical[:-10])
    elif restoration == "earlier":
        event_path.write_bytes(previous)
    elif restoration == "copied_earlier":
        replacement = tmp_path / "earlier.events"
        replacement.write_bytes(previous)
        replacement.replace(event_path)
    else:
        event_path.write_bytes(canonical.replace(record["id"].encode(), b"c" * 64))
        assert event_path.stat().st_size == len(canonical)
    assert not [row for row in _rows(tmp_path) if row.get("mission_delivery_id") == record["id"]]
    rebuild = MissionEventIndex._rebuild
    rebuilds = []

    def counted_rebuild(current):
        rebuilds.append(current.root)
        return rebuild(current)

    monkeypatch.setattr(MissionEventIndex, "_rebuild", counted_rebuild)
    assert drain_mission_deliveries(backlog, sink.handle_event)
    assert backlog.pending_mission_deliveries() == []
    assert backlog.claim_next() is None
    assert len(backlog.history()) == 1
    assert len([row for row in _rows(tmp_path) if row.get("mission_delivery_id") == record["id"]]) == 1
    assert rebuilds == [tmp_path]


def test_written_receipt_checks_current_offset_without_enumerating_archives(tmp_path, monkeypatch):
    from argus.life import event_log

    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert drain_mission_deliveries(backlog, sink.handle_event)
    monkeypatch.setattr(event_log, "event_log_paths", lambda _path: pytest.fail("enumerated archive history"))
    _require_bounded_receipt_reads(monkeypatch, tmp_path)
    assert mission_event_index(str(tmp_path.resolve())).contains(record["id"])


def _require_bounded_receipt_reads(monkeypatch, root):
    open_file = Path.open

    class CheckedRead:
        def __init__(self, handle, limit):
            self.handle = handle
            self.limit = limit

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def readline(self, size=-1):
            assert 0 < size <= self.limit + 1
            return self.handle.readline(size)

    def checked_open(path, mode="r", *args, **kwargs):
        handle = open_file(path, mode, *args, **kwargs)
        if path.parent == root and mode in {"rb", "r+b"}:
            limit = MAX_INDEX_RECORD_BYTES if path.name == "mission-events.index.jsonl" else MAX_JSONL_RECORD_BYTES
            return CheckedRead(handle, limit)
        return handle

    monkeypatch.setattr(Path, "open", checked_open)


def test_receipt_rebuild_skips_oversized_rows_in_bounded_chunks(tmp_path, monkeypatch):
    fake_key, key = "a" * 64, "b" * 64

    def encoded(delivery_id):
        return (json.dumps({
            "type": EventType.LIFE_MISSION_COMPLETED,
            "mission_delivery_id": delivery_id, "event_id": f"mission-{delivery_id}",
        }) + "\n").encode()

    oversized = b"x" * (MAX_JSONL_RECORD_BYTES * 3 + 7) + encoded(fake_key)
    (tmp_path / "events.jsonl").write_bytes(oversized + encoded(key) + b"y" * (MAX_JSONL_RECORD_BYTES + 5))
    _require_bounded_receipt_reads(monkeypatch, tmp_path)
    index = MissionEventIndex(tmp_path)
    assert index.contains(key)
    assert not index.contains(fake_key)
    assert index.rows[key]["offset"] == len(oversized)


@pytest.mark.parametrize("terminated", [False, True])
def test_oversized_receipt_index_is_corruption_and_is_not_truncated(tmp_path, monkeypatch, terminated):
    index = MissionEventIndex(tmp_path)
    content = b"x" * (MAX_INDEX_RECORD_BYTES + 9) + (b"\n" if terminated else b"")
    index.path.write_bytes(content)
    _require_bounded_receipt_reads(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="oversized mission event receipt index row"):
        index.refresh()
    assert index.path.read_bytes() == content


def test_oversized_completion_stays_pending_without_unrecoverable_canonical_row(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    record["event"]["summary"] = "x" * MAX_JSONL_RECORD_BYTES
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    assert not drain_mission_deliveries(backlog, sink.handle_event)
    assert len(backlog.pending_mission_deliveries()) == 1
    assert _rows(tmp_path) == []


def test_partial_canonical_event_write_is_retried_as_one_complete_event(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item, record = _claimed(backlog)
    backlog.update(item.id, status="done", _mission_delivery=record)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    open_file = Path.open

    class InterruptedAppend:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, data):
            if b'"mission_delivery_id"' in data:
                self.handle.write(data[:len(data) // 2])
                self.handle.flush()
                os.fsync(self.handle.fileno())
                raise OSError("event append stopped halfway through the row")
            return self.handle.write(data)

    def faulting_open(path, mode="r", *args, **kwargs):
        handle = open_file(path, mode, *args, **kwargs)
        return InterruptedAppend(handle) if path == tmp_path / "events.jsonl" and mode == "a+b" else handle

    with monkeypatch.context() as fault:
        fault.setattr(Path, "open", faulting_open)
        assert not drain_mission_deliveries(backlog, sink.handle_event)
    assert (tmp_path / "events.jsonl").stat().st_size > 0
    assert _rows(tmp_path) == []
    mission_event_index.cache_clear()
    assert drain_mission_deliveries(backlog, sink.handle_event)
    assert len(_rows(tmp_path)) == 1
    assert backlog.pending_mission_deliveries() == []


def test_completion_and_return_receipt_are_durable_before_learning(tmp_path, monkeypatch):
    supervisor, runner, item, observed = _supervisor(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def learning(**_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("optional learning failed")

    monkeypatch.setattr(supervisor, "_evolve_runtime_skills_after_mission", learning)
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(supervisor.tick)
        try:
            assert entered.wait(timeout=5)
            assert not task.done()
            stored = supervisor.memory.backlog.history()[0]
            assert stored.status == "done" and stored.mission_result["success"] is True
            assert len(_rows(supervisor.memory.root)) == 1
            assert supervisor.memory.backlog.pending_mission_deliveries() == []
        finally:
            release.set()
        assert task.result(timeout=5)["success"] is True
    assert runner.calls == 1


def test_post_completion_learning_cost_remains_in_ledger_and_return_receipt(tmp_path, monkeypatch):
    from argus.core.usage import UsageLedger, UsageRecord

    supervisor, runner, item, observed = _supervisor(tmp_path, monkeypatch)
    ledger = UsageLedger(supervisor.memory.root, migrate_legacy=False)
    usage_id = f"{item.id}:attempt:1"

    def record_call(label, cost):
        ledger.append(UsageRecord(
            call_id=label, project_id=supervisor.memory.root.name, mission_id=usage_id,
            provider="memory", model="memory", run_label=label, started_at=1.0,
            completed_at=2.0, status="completed", input_tokens=0,
            cached_input_tokens=0, output_tokens=0, reasoning_output_tokens=0,
            premium_requests=0.0, pricing_status="priced", pricing_tier="test",
            cost_usd=cost, cost_basis="provider_reported",
        ))

    def execute(**_kwargs):
        runner.calls += 1
        record_call("execution", 0.01)
        return _Outcome(True, "done", final_message="Verified result.")

    def learning(**_kwargs):
        assert _rows(supervisor.memory.root)[0]["known_cost_usd"] == 0.01
        record_call("post-completion-learning", 0.02)

    runner._set_usage_context = lambda **_kwargs: None
    runner.execute = execute
    monkeypatch.setattr(supervisor, "_evolve_runtime_skills_after_mission", learning)
    result = supervisor.tick()
    assert result["known_cost_usd"] == pytest.approx(0.03)
    assert ledger.summary().known_cost_usd == pytest.approx(0.03)
    assert supervisor.memory.backlog.history()[0].mission_result["known_cost_usd"] == pytest.approx(0.03)
    assert len(_rows(supervisor.memory.root)) == 1
