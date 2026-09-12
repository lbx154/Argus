"""Mission-boundary fairness and cancellation, without concurrent state commits."""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_backends import _Outcome
from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.manager import Manager, dispatch, front_door
from argus_skill.manager._session_ops import (
    ManagerLockCancelled,
    clear_manager_pipeline_yield,
    manager_pipeline_boundary,
    manager_pipeline_lock,
    manager_pipeline_yield_requested,
    request_manager_pipeline_yield,
)
from argus_skill.skills.vertical_select import persist_vertical


def test_independent_yield_waiters_do_not_clear_each_other(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _: request_manager_pipeline_yield(tmp_path), range(8)))
    for token in tokens[:-1]:
        assert clear_manager_pipeline_yield(tmp_path, token)
        assert manager_pipeline_yield_requested(tmp_path)
    assert not clear_manager_pipeline_yield(tmp_path, "unrelated-request")
    assert clear_manager_pipeline_yield(tmp_path, tokens[-1])
    assert not manager_pipeline_yield_requested(tmp_path)


def test_legacy_single_waiter_survives_another_requests_completion(tmp_path):
    marker = tmp_path / ".manager_pipeline_yield.json"
    marker.write_text(json.dumps({"schema_version": 1, "token": "legacy", "pid": os.getpid()}))
    token = request_manager_pipeline_yield(tmp_path)
    assert clear_manager_pipeline_yield(tmp_path, token)
    assert manager_pipeline_yield_requested(tmp_path)
    assert clear_manager_pipeline_yield(tmp_path, "legacy")
    assert not marker.exists()


def test_yield_liveness_uses_the_portable_read_only_process_probe(tmp_path, monkeypatch):
    request_manager_pipeline_yield(tmp_path)
    inspected = []

    def probe(pid):
        inspected.append(pid)
        return True

    monkeypatch.setattr("argus_skill.manager._session_ops.is_pid_running", probe)
    assert manager_pipeline_yield_requested(tmp_path)
    assert inspected == [os.getpid()]


def test_yield_cleanup_failure_cannot_override_the_handoff_result(tmp_path, monkeypatch):
    token = request_manager_pipeline_yield(tmp_path)
    marker = tmp_path / ".manager_pipeline_yields" / f"{token}.json"
    original_unlink = type(marker).unlink

    def failed_cleanup(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("temporary marker cleanup failed")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(marker), "unlink", failed_cleanup)
    assert clear_manager_pipeline_yield(tmp_path, token) is True
    assert marker.is_file()
    assert json.loads(marker.read_text())["state"] == "released"
    assert not manager_pipeline_yield_requested(tmp_path)
    # Another process can make the same determination after the requester exits.
    assert json.loads(marker.read_text())["state"] == "released"
    monkeypatch.setattr(type(marker), "unlink", original_unlink)
    assert not manager_pipeline_yield_requested(tmp_path)
    assert not marker.exists()


def test_boundary_adapts_legacy_factory_without_retrying_internal_type_errors():
    calls = []

    @contextmanager
    def legacy_lock():
        calls.append("entered")
        raise TypeError("failure inside lock factory")
        yield  # pragma: no cover

    with pytest.raises(TypeError, match="failure inside lock factory"):
        with manager_pipeline_boundary(
            SimpleNamespace(pipeline_lock=legacy_lock), cancelled=lambda: False,
        ):
            pytest.fail("failed lock factory entered the boundary")
    assert calls == ["entered"]


def _prepared(memory, manager, body, *, commit):
    return SimpleNamespace(
        mem=memory, body=body, root_task_id=None, intent_id="test-intent",
        manager=manager, execution_task=body, commit=commit,
        decision=SimpleNamespace(workflow_mode="direct"),
        completed=lambda *_args, **_kwargs: None,
        failed=lambda _exc: None,
    )


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("failure", ["cancelled", "marker_io"])
def test_pre_boundary_failure_settles_the_prepared_intent(
    tmp_path, monkeypatch, continuous, failure,
):
    memory = LifeMemory.open(tmp_path / "life")
    mutations, failures = [], []
    prepared = _prepared(
        memory, Manager(memory.root), "requested goal",
        commit=lambda **_kwargs: mutations.append("commit"),
    )
    prepared.failed = failures.append

    if failure == "marker_io":
        def fail_write(*_args):
            raise OSError("marker publication failed")

        monkeypatch.setattr("argus_skill.manager._session_ops._write_pipeline_yield", fail_write)
    handoff = front_door.manager_continuous_handoff if continuous else front_door.manager_bounded_handoff
    with pytest.raises(front_door.ManagerHandoffError):
        handoff(
            memory, prepared.body, {},
            persist=lambda *_args: mutations.append("persist"),
            prepared_handoff=prepared, cancelled=lambda: failure == "cancelled",
        )
    assert len(failures) == 1
    assert isinstance(failures[0], ManagerLockCancelled if failure == "cancelled" else OSError)
    assert mutations == []
    assert not manager_pipeline_yield_requested(memory.root)


def test_bounded_handoff_yields_after_current_mission_before_draining_backlog(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software")
    memory = LifeMemory.open(tmp_path / "life")
    manager = Manager(memory.root, execution_workdir=project)
    started, finish_current, ready_to_commit = threading.Event(), threading.Event(), threading.Event()
    executed = []

    class Runner:
        def execute(self, *, objective, **_kwargs):
            executed.append(objective)
            started.set()
            assert finish_current.wait(timeout=5)
            return _Outcome(True, "done")

    runner = Runner()
    runner.manager = manager
    supervisor = LifeSupervisor(
        memory=memory, runner=runner, sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(
            project_worktree=project, artifact_root=project, budget=LifeBudget(max_missions=3),
            manager_pipeline_yield_provider=lambda: manager_pipeline_yield_requested(memory.root),
        ),
    )
    monkeypatch.setattr(supervisor, "_evolve_runtime_skills_after_mission", lambda **_kwargs: None)
    first, second = [memory.backlog.add(BacklogItem.new(
        title=title, objective=title, iterate=False,
        manager_decision={"routed": True, "vertical": "software"},
    )) for title in ("current mission", "later mission")]
    committed = []

    def commit(**_kwargs):
        rows = {row.id: row.status for row in memory.backlog.all()}
        assert rows[first.id] == "done"
        assert rows[second.id] == "pending"
        committed.append(True)
        return SimpleNamespace(vertical="software")

    def drain():
        with manager.pipeline_lock():
            return supervisor.run()

    prepared = _prepared(memory, manager, "new bounded request", commit=commit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(drain)
        assert started.wait(timeout=5)
        handoff = executor.submit(
            front_door.manager_bounded_handoff, memory, prepared.body, {},
            lambda task, _division: task, prepared_handoff=prepared,
            prepare_persist=lambda _task: ready_to_commit.set(),
        )
        try:
            assert ready_to_commit.wait(timeout=5)
            assert manager_pipeline_yield_requested(memory.root)
            assert committed == []  # Never mutate Manager state during the mission.
        finally:
            finish_current.set()
        summary = running.result(timeout=5)
        assert handoff.result(timeout=5) == prepared.body
    assert summary["stopped_by"] == "manager_config_pending"
    assert summary["missions_run"] == 1
    assert executed == [first.objective]
    assert committed == [True]
    assert not manager_pipeline_yield_requested(memory.root)


@pytest.mark.parametrize("route", ["bounded", "continuous", "enqueue"])
def test_cancelled_handoff_does_not_wait_for_running_mission_or_commit(tmp_path, route):
    memory = LifeMemory.open(tmp_path / "life")
    manager = Manager(memory.root)
    write_continuous_config(memory.root, enabled=True, objective="original goal")
    expected = read_continuous_state(memory.root)
    cancelled, waiting = threading.Event(), threading.Event()

    @contextmanager
    def waiting_lock(*, cancelled=None):
        waiting.set()
        with manager_pipeline_lock(memory.root, cancelled=cancelled):
            yield

    manager.pipeline_lock = waiting_lock
    mutations = []
    prepared = _prepared(
        memory, manager, "replacement goal", commit=lambda **_kwargs: mutations.append("commit"),
    )

    def handoff():
        if route == "enqueue":
            return dispatch.enqueue_mission(
                memory, prepared.body, {},
                cancelled=cancelled.is_set, prepared_handoff=prepared,
            )
        fn = front_door.manager_continuous_handoff if route == "continuous" else front_door.manager_bounded_handoff
        kwargs = dict(
            prepared_handoff=prepared, cancelled=cancelled.is_set,
            prepare_persist=lambda _task: waiting.set(),
            persist=lambda *_args: mutations.append("persist"),
        )
        return fn(memory, prepared.body, {}, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with manager_pipeline_lock(memory.root):
            future = executor.submit(handoff)
            assert waiting.wait(timeout=5)
            cancelled.set()
            with pytest.raises(front_door.ManagerHandoffError, match="cancelled") as caught:
                future.result(timeout=1)
            assert isinstance(caught.value.__cause__, ManagerLockCancelled)
            assert mutations == []
            assert memory.backlog.all() == []
            assert read_continuous_state(memory.root) == expected
            assert not manager_pipeline_yield_requested(memory.root)
