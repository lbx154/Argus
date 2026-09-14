"""Settled observations survive delivery, shutdown and process boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_backends import _Outcome
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.skills.vertical_select import persist_vertical


def _supervisor(root: Path, *, stop_event=None, stop_after_execute=False, allow_execute=True):
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    persist_vertical(project, "software")
    evidence = project / "checked.txt"
    evidence.write_text("The offline fixture observed the bounded check passing.\n")
    memory = LifeMemory.open(root / "life")

    class Runner:
        def execute(self, **_kwargs):
            assert allow_execute, "a committed mission must never execute again"
            with (root / "executions.jsonl").open("a") as handle:
                handle.write(json.dumps({"executed": True}) + "\n")
            if stop_after_execute:
                stop_event.set()
            return _Outcome(True, "done", final_message="The bounded artifact check passed.",
                            final_review_status="done", final_review_source="reviewer",
                            final_review_reason="The saved artifact matches the bounded check.")

    supervisor = LifeSupervisor(memory=memory, runner=Runner(),
        sink=JsonlEventSink(SimpleNamespace(handle_event=lambda _event: None), life_dir=memory.root),
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project, stop_event=stop_event))
    supervisor._evolve_runtime_skills_after_mission = lambda **_kwargs: None
    return supervisor


def _enqueue(supervisor):
    return supervisor.memory.backlog.add(BacklogItem.new(
        title="bounded artifact check", objective="Check the saved artifact under one condition.",
        iterate=False, context_refs=[{"path": str(supervisor.config.project_worktree / "checked.txt")}],
        manager_decision={"routed": True, "vertical": "software"},
    ))


@pytest.mark.parametrize("boundary", ["delivery_failure", "stop_after_execute"])
def test_settled_observation_survives_delivery_failure_or_stop_and_restart(tmp_path, monkeypatch, boundary):
    stop = threading.Event()
    supervisor = _supervisor(tmp_path, stop_event=stop, stop_after_execute=boundary == "stop_after_execute")
    item = _enqueue(supervisor)
    append = JsonlEventSink._append

    def fail_delivery(sink, event):
        if event.get("mission_delivery_id"):
            return False
        return append(sink, event)

    with monkeypatch.context() as fault:
        if boundary == "delivery_failure":
            fault.setattr(JsonlEventSink, "_append", fail_delivery)
        assert supervisor.tick()["success"] is True
    settled = supervisor.memory.backlog.history()[0]
    assert settled.id == item.id and settled.status == "done"
    initial = supervisor.memory.failure_experiences.recent()
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert restarted.tick() is None
    experiences = restarted.memory.failure_experiences.recent()
    assert len(experiences) == 1
    assert len(initial) == 1
    assert experiences[0].mission_id == item.id and experiences[0].revision == 1
    assert experiences[0].research_narrative == "The bounded artifact check passed."
    assert "not a general causal rule" in experiences[0].claim_boundaries[0]
    assert str(supervisor.config.project_worktree / "checked.txt") in experiences[0].artifact_refs
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    assert len((tmp_path / "executions.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("boundary", ["commit", "capture"])
def test_process_exit_after_real_settlement_replays_learning_without_execution(tmp_path, boundary):
    script = r'''
import importlib.util, os, sys
from pathlib import Path
from argus_skill.life import memory as module
spec = importlib.util.spec_from_file_location("settlement_fixture", sys.argv[2])
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
supervisor = fixture._supervisor(Path(sys.argv[1]))
fixture._enqueue(supervisor)
rewrite = module._atomic_rewrite_jsonl
def crash(path, rows):
    rows = list(rows)
    rewrite(path, rows)
    if sys.argv[3] == "commit" and path == supervisor.memory.backlog._commit_path and rows[0].get("version") == 2:
        os._exit(73)
module._atomic_rewrite_jsonl = crash
if sys.argv[3] == "capture":
    from argus_skill.life.failure_experience_storage import ExperienceRepository
    save = ExperienceRepository.save
    def captured(repository, *args, **kwargs):
        save(repository, *args, **kwargs)
        os._exit(73)
    ExperienceRepository.save = captured
supervisor.tick()
raise AssertionError("the real completion commit boundary was not reached")
'''
    process = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), str(Path(__file__).resolve()), boundary],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True, text=True, timeout=15)
    assert process.returncode == 73, process.stderr
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert restarted.tick() is None
    (settled,) = restarted.memory.backlog.history()
    assert settled.status == "done" and settled.mission_result["success"] is True
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    experiences = restarted.memory.failure_experiences.recent()
    assert len(experiences) == 1
    assert experiences[0].mission_id == settled.id and experiences[0].revision == 1
    assert len((tmp_path / "executions.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("failure", ["unwritable", "corrupt"])
def test_learning_storage_failure_keeps_delivery_and_next_mission_independent(tmp_path, monkeypatch, failure):
    from argus_skill.life.failure_experience_storage import ExperienceRepository

    supervisor = _supervisor(tmp_path)
    first = _enqueue(supervisor)
    path = supervisor.memory.root / "failure_experiences.jsonl"
    original_execute = supervisor.runner.execute

    def execute(**kwargs):
        result = original_execute(**kwargs)
        if failure == "corrupt":
            path.write_text('{"record_type":"snapshot","schema_version":999}\n')
        return result

    supervisor.runner.execute = execute
    with monkeypatch.context() as fault:
        if failure == "unwritable":
            fault.setattr(ExperienceRepository, "save", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("store temporarily read-only")))
        assert supervisor.tick()["success"] is True
        second = _enqueue(supervisor)
        assert supervisor.tick()["success"] is True
        # Learning failures must not turn either completed result into a
        # mission-delivery pause, nor prevent a new mission from executing.
        assert {row.id for row in supervisor.memory.backlog.history()} == {first.id, second.id}
        if failure == "corrupt":
            assert path.read_text() == '{"record_type":"snapshot","schema_version":999}\n'
    if failure == "corrupt":
        path.rename(path.with_suffix(".corrupt-preserved"))
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert restarted.tick() is None
    assert {row.mission_id for row in restarted.memory.failure_experiences.recent()} == {first.id, second.id}
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    assert len((tmp_path / "executions.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("correction", ["revise", "retract", "merge", "capacity"])
def test_recovery_never_overwrites_later_canonical_lifecycle(tmp_path, monkeypatch, correction):
    from argus_skill.life.failure_experience import FailureExperience, FailureExperienceStore
    from argus_skill.life.failure_experience_storage import ExperienceRepository

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    save = ExperienceRepository.save

    def committed_then_failed(repository, *args, **kwargs):
        save(repository, *args, **kwargs)
        raise OSError("process failed after canonical fsync but before acknowledgement")

    with monkeypatch.context() as fault:
        fault.setattr(ExperienceRepository, "save", committed_then_failed)
        assert supervisor.tick()["success"] is True
    store = supervisor.memory.failure_experiences
    (captured,) = store.recent()
    (pending,) = supervisor.memory.backlog.pending_mission_deliveries()
    assert pending["publication_acknowledged"] is True
    if correction == "revise":
        store.revise(captured.id, expected_revision=1, evidence_refs=["review:correction"],
                     factual_outcome="The later controlled check corrected the original interpretation.")
    elif correction == "retract":
        store.retract(captured.id, expected_revision=1, evidence_refs=["review:withdrawal"], reason="Observation withdrawn.")
    elif correction == "merge":
        other = store.append(FailureExperience.new(mission_id="other", title="related observation", objective="related",
            status="failed", factual_outcome="a later related observation", source_refs=["mission:other/attempt:1"]))
        store.merge(captured.id, [other.id], expected_revisions={captured.id: 1, other.id: 1},
                    evidence_refs=["review:consolidation"], reason="Explicitly consolidated related observations.")
    else:
        store = FailureExperienceStore(store.path, max_active=1, max_history=0)
        store.append(FailureExperience.new(mission_id="newer", title="newer", objective="newer",
                                          status="failed", factual_outcome="newer retained observation"))
    canonical = store.path.read_bytes()
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert store.path.read_bytes() == canonical
    assert restarted.memory.backlog.pending_mission_deliveries() == []


def test_stop_capture_has_no_embedding_or_optional_model_work(tmp_path, monkeypatch):
    from argus_skill.life.http_embedding import HttpEmbeddingAdapter
    from argus_skill.life.recall_embedding import save_embedding_config

    stop = threading.Event()
    supervisor = _supervisor(tmp_path, stop_event=stop, stop_after_execute=True)
    _enqueue(supervisor)
    execute = supervisor.runner.execute
    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append("model_or_embedding")
        raise AssertionError("stopping capture must only persist canonical evidence")

    def enable_http_at_settlement(**kwargs):
        result = execute(**kwargs)
        save_embedding_config(supervisor.memory.root, {"enabled": True,
            "endpoint": "http://127.0.0.1:1/never-called", "model": "offline-prohibited", "dimensions": 2})
        return result

    supervisor.runner.execute = enable_http_at_settlement
    supervisor._evolve_runtime_skills_after_mission = forbidden
    monkeypatch.setattr(HttpEmbeddingAdapter, "embed", forbidden)
    assert supervisor.tick()["success"] is True
    assert len(supervisor.memory.failure_experiences.recent()) == 1
    assert attempts == []
    assert not (supervisor.memory.root / "embedding/usage.sqlite3").exists()


@pytest.mark.parametrize("iteration", ["requeued", "budget_exhausted", "measurement_blocked", "assessment_blocked"])
def test_real_unfinished_iteration_is_never_captured_as_success(tmp_path, monkeypatch, iteration):
    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    monkeypatch.setattr(supervisor, "_maybe_requeue_chartered_shortfall", lambda _state: {
        "status": iteration, "requeued": False, "stop_reason": "The original target remains unmet.",
    })
    assert supervisor.tick()["success"] is True  # The existing execution verdict is independent.
    (captured,) = supervisor.memory.failure_experiences.recent()
    assert captured.status == "iteration_" + iteration
    assert captured.factual_outcome == "The original target remains unmet."
    assert "successful runtime verdict" not in " ".join(captured.claim_boundaries)


@pytest.mark.parametrize("status,review,success", [
    ("failed", "blocked", False), ("research_incomplete", "research_incomplete", False),
    ("done", "continue", True),
])
def test_rejected_or_unfinished_verdict_is_not_successful_memory(tmp_path, status, review, success):
    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    supervisor.runner.execute = lambda **_kwargs: _Outcome(success, status, final_review_status=review,
        final_review_source="reviewer", stop_reason="The reviewed target remains unmet.")
    supervisor.tick()
    (captured,) = supervisor.memory.failure_experiences.recent()
    assert captured.status not in {"done", "success", "completed"}
    assert "successful runtime verdict" not in " ".join(captured.claim_boundaries)


@pytest.mark.parametrize("damage", ["nan", "missing_identity", "missing_capsule"])
def test_bad_learning_payload_does_not_block_other_completed_missions(tmp_path, monkeypatch, damage):
    from argus_skill.life.failure_experience import FailureExperienceStore

    supervisor = _supervisor(tmp_path)
    first = _enqueue(supervisor)
    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        assert supervisor.tick()["success"] is True
    (record,) = supervisor.memory.backlog.pending_mission_deliveries()
    path = supervisor.memory.backlog._mission_deliveries_path / f"{record['id']}.json"
    if damage == "nan":
        record["settled_experience"]["created_at"] = float("nan")
    elif damage == "missing_identity":
        record["settled_experience"].pop("id")
    else:
        record.pop("settled_experience")
    path.write_text(json.dumps(record) + "\n")
    second = _enqueue(supervisor)
    assert supervisor.tick()["success"] is True
    assert {row.id for row in supervisor.memory.backlog.history()} == {first.id, second.id}
    assert {row.mission_id for row in supervisor.memory.failure_experiences.recent()} == {second.id}


def test_learning_marker_failure_does_not_block_confirmed_delivery(tmp_path, monkeypatch):
    from argus_skill.life import memory as memory_module
    from argus_skill.life.failure_experience import FailureExperienceStore

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    rewrite = memory_module._atomic_rewrite_jsonl

    def fail_marker(path, rows):
        rows = list(rows)
        if path.parent == supervisor.memory.backlog._mission_deliveries_path and rows[0].get("publication_acknowledged"):
            raise OSError("learning progress marker temporarily unavailable")
        return rewrite(path, rows)

    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        fault.setattr(memory_module, "_atomic_rewrite_jsonl", fail_marker)
        assert supervisor.tick()["success"] is True
        _enqueue(supervisor)
        assert supervisor.tick()["success"] is True
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert len(restarted.memory.failure_experiences.recent()) == 2


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_prolonged_learning_failure_retains_a_bounded_window_without_blocking_work(tmp_path, monkeypatch, caplog, limit):
    from argus_skill.life import mission_delivery
    from argus_skill.life.failure_experience import FailureExperienceStore

    caplog.set_level("CRITICAL", logger="argus_skill.life.mission_delivery")
    supervisor = _supervisor(tmp_path)
    execute = supervisor.runner.execute
    if limit == "bytes":
        def long_handoff(**kwargs):
            result = execute(**kwargs)
            result.final_output = "A bounded synthetic delivery line.\n" * 5000
            return result
        supervisor.runner.execute = long_handoff
    count = 131 if limit == "count" else 40
    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("long-lived canonical store outage")))
        for _ in range(count):
            _enqueue(supervisor)
            assert supervisor.tick()["success"] is True
        pending = supervisor.memory.backlog.pending_mission_deliveries()
        assert pending and all(row["publication_acknowledged"] is True for row in pending)
        sizes = [path.stat().st_size for path in supervisor.memory.backlog._mission_deliveries_path.glob("*.json")]
        assert len(sizes) <= mission_delivery.MAX_PENDING_EXPERIENCES
        assert sum(sizes) <= mission_delivery.MAX_PENDING_EXPERIENCE_BYTES
        retention = json.loads((supervisor.memory.root / mission_delivery.EXPERIENCE_RETENTION).read_text())
        assert retention["retired_count"] == count - len(pending) > 0
        assert retention["reason"] == "pending_experience_capacity"
        assert len(supervisor.memory.backlog.history()) == count
    expected = {row["item_id"] for row in pending}
    restarted = _supervisor(tmp_path, allow_execute=False)
    for _ in range((len(pending) + mission_delivery.MAX_EXPERIENCE_RETRIES - 1) // mission_delivery.MAX_EXPERIENCE_RETRIES):
        assert restarted.tick() is None
    assert {row.mission_id for row in restarted.memory.failure_experiences.recent(max_entries=256)} == expected
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    assert len((tmp_path / "executions.jsonl").read_text().splitlines()) == count


def test_retention_watermark_survives_exit_before_delete_and_old_envelope_replay(tmp_path, monkeypatch):
    from argus_skill.life import mission_delivery
    from argus_skill.life.failure_experience import FailureExperienceStore

    class ProcessStopped(BaseException):
        pass

    supervisor = _supervisor(tmp_path)
    root = supervisor.memory.root
    original_ack = supervisor.memory.backlog.acknowledge_mission_delivery
    monkeypatch.setattr(mission_delivery, "MAX_PENDING_EXPERIENCES", 2)

    def stopped_after_watermark(identity, **kwargs):
        if (root / mission_delivery.EXPERIENCE_RETENTION).exists() and not kwargs.get("keep_for_experience"):
            raise ProcessStopped()
        return original_ack(identity, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        for _ in range(2):
            _enqueue(supervisor)
            assert supervisor.tick()["success"] is True
        old = supervisor.memory.backlog.pending_mission_deliveries()[0]
        fault.setattr(supervisor.memory.backlog, "acknowledge_mission_delivery", stopped_after_watermark)
        _enqueue(supervisor)
        with pytest.raises(ProcessStopped):
            supervisor.tick()
    assert len(supervisor.memory.backlog.pending_mission_deliveries()) == 3
    metadata = (root / mission_delivery.EXPERIENCE_RETENTION).read_bytes()
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert len(restarted.memory.failure_experiences.recent()) == 2
    assert old["item_id"] not in {row.mission_id for row in restarted.memory.failure_experiences.recent()}
    # Reintroduce the original unacknowledged envelope, as a stale commit replay
    # might do; the watermark must suppress learning without suppressing delivery.
    old["publication_acknowledged"] = False
    (restarted.memory.backlog._mission_deliveries_path / f"{old['id']}.json").write_text(json.dumps(old) + "\n")
    assert restarted.tick() is None
    assert len(restarted.memory.failure_experiences.recent()) == 2
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    assert (root / mission_delivery.EXPERIENCE_RETENTION).read_bytes() == metadata


def test_confirmed_learning_cleanup_failure_does_not_pause_new_work(tmp_path, monkeypatch):
    from argus_skill.life.failure_experience import FailureExperienceStore

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        assert supervisor.tick()["success"] is True
    (pending,) = supervisor.memory.backlog.pending_mission_deliveries()
    unlink = Path.unlink

    def failed_cleanup(path, *args, **kwargs):
        if path.name == f"{pending['id']}.json":
            raise OSError("pending learning unlink temporarily failed")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "unlink", failed_cleanup)
        _enqueue(supervisor)
        assert supervisor.tick()["success"] is True
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert len(restarted.memory.failure_experiences.recent()) == 2
    assert restarted.memory.backlog.pending_mission_deliveries() == []


def test_learning_lock_contention_never_waits_for_the_default_storage_timeout(tmp_path, monkeypatch):
    import time

    from argus_skill.life.failure_experience import FailureExperienceStore

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        assert supervisor.tick()["success"] is True
    restarted = _supervisor(tmp_path, allow_execute=False)
    with supervisor.memory.failure_experiences._repository.locked():
        started = time.monotonic()
        assert restarted.tick() is None
        assert time.monotonic() - started < 1.0
        assert len(restarted.memory.backlog.pending_mission_deliveries()) == 1
    assert restarted.tick() is None
    assert len(restarted.memory.failure_experiences.recent()) == 1


def test_dispatch_contention_distinguishes_learning_from_unpersisted_delivery(tmp_path, monkeypatch):
    import portalocker

    from argus_skill.life import mission_delivery
    from argus_skill.life.event_log import event_log_paths
    from argus_skill.life.failure_experience import FailureExperienceStore
    from argus_skill.life.mission_event_index import mission_event_index

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    with monkeypatch.context() as fault:
        fault.setattr(FailureExperienceStore, "record_settled", lambda *_args: (_ for _ in ()).throw(OSError("deferred")))
        assert supervisor.tick()["success"] is True
    lock = supervisor.memory.root / mission_delivery.DELIVERY_LOCK
    with lock.open("a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        try:
            assert supervisor._drain_mission_completions() is True
            # A copied earlier event snapshot must invalidate the marker's
            # event prerequisite even though learning was already queued.
            for path in event_log_paths(supervisor.memory.root / "events.jsonl"):
                if path.exists():
                    path.rename(path.with_suffix(path.suffix + ".preserved"))
            mission_event_index.cache_clear()
            assert supervisor._drain_mission_completions() is False
        finally:
            portalocker.unlock(handle)
    assert supervisor._drain_mission_completions() is True
    assert supervisor.memory.backlog.pending_mission_deliveries() == []


def test_valid_multilingual_capsule_uses_utf8_size_and_does_not_block_commit(tmp_path):
    from argus_skill.life.failure_experience import experience_from_settled_mission
    from argus_skill.life.mission_delivery import drain_mission_deliveries, prepare_mission_delivery

    supervisor = _supervisor(tmp_path)
    _enqueue(supervisor)
    item = supervisor.memory.backlog.claim_next()
    capsule = experience_from_settled_mission(mission_id=item.id, title=item.title, objective=item.objective,
        status="done", success=True, factual_outcome="The bounded check passed.",
        created_at=item.started_ts, attempt_id=f"{item.id}:attempt:{item.attempt}",
        non_goals=[str(index) + "界" * 999 for index in range(24)]).to_jsonable()
    assert len(json.dumps(capsule).encode()) > 128 * 1024
    assert len(json.dumps(capsule, ensure_ascii=False).encode()) < 128 * 1024
    event = {"type": "life.mission.completed", "item_id": item.id, "status": "done", "success": True}
    record = prepare_mission_delivery(item=item, event=event, result={"item_id": item.id, "status": "done", "success": True},
                                     settled_experience=capsule)
    supervisor.memory.backlog.update(item.id, status="done", _mission_delivery=record)
    assert drain_mission_deliveries(supervisor.memory.backlog, supervisor._emit)
    assert len(supervisor.memory.failure_experiences.recent()) == 1


def test_retention_numeric_overflow_does_not_block_delivery_or_new_work(tmp_path, monkeypatch):
    from argus_skill.life.mission_delivery import EXPERIENCE_RETENTION

    supervisor = _supervisor(tmp_path)
    first = _enqueue(supervisor)
    append = JsonlEventSink._append
    with monkeypatch.context() as fault:
        fault.setattr(JsonlEventSink, "_append", lambda sink, event:
            False if event.get("mission_delivery_id") else append(sink, event))
        assert supervisor.tick()["success"] is True
    assert len(supervisor.memory.backlog.pending_mission_deliveries()) == 1
    retention = supervisor.memory.root / EXPERIENCE_RETENTION
    retention.write_text(json.dumps({"version": 1, "retired_through": [10**400, "0" * 64],
        "retired_count": 0, "reason": "pending_experience_capacity"}) + "\n")
    corrupt = retention.read_bytes()
    assert len(corrupt) < 4096  # Valid bounded JSON; conversion to float overflows.
    second = _enqueue(supervisor)
    result = supervisor.tick()
    assert result["success"] is True and result["item_id"] == second.id
    assert len(supervisor.memory.journal.tail_settlements(5)) == 2
    assert {row.id for row in supervisor.memory.backlog.history()} == {first.id, second.id}
    pending = supervisor.memory.backlog.pending_mission_deliveries()
    assert len(pending) == 2 and all(row["publication_acknowledged"] is True for row in pending)
    assert retention.read_bytes() == corrupt  # Never invent a replacement retirement history.
    retention.rename(retention.with_suffix(".corrupt-preserved"))
    restarted = _supervisor(tmp_path, allow_execute=False)
    assert restarted.tick() is None
    assert {row.mission_id for row in restarted.memory.failure_experiences.recent()} == {first.id, second.id}
    assert restarted.memory.backlog.pending_mission_deliveries() == []
    assert len((tmp_path / "executions.jsonl").read_text().splitlines()) == 2
