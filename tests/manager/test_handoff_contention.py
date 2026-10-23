from __future__ import annotations

import contextvars
import json
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import LifeMemory
from argus.manager import front_door
from argus.manager._session_ops import (
    ManagerLockCancelled,
    ManagerPipelineWaitTimeout,
    manager_pipeline_lock,
    manager_pipeline_yield_requested,
)


@contextmanager
def held_pipeline(root, *, release_on_request=False):
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with manager_pipeline_lock(root):
            entered.set()
            deadline = time.monotonic() + 10
            while not release.wait(0.01):
                if release_on_request and manager_pipeline_yield_requested(root):
                    break
                if time.monotonic() >= deadline:
                    break

    owner = threading.Thread(target=lambda: contextvars.Context().run(hold))
    owner.start()
    assert entered.wait(2)
    try:
        yield owner
    finally:
        release.set()
        owner.join(2)
        assert not owner.is_alive()






@pytest.mark.parametrize("cancel", [False, True])
def test_pipeline_wait_is_bounded_without_breaking_the_peer_lock(tmp_path, cancel):
    with held_pipeline(tmp_path) as owner:
        started = time.monotonic()
        cancelled = (lambda: time.monotonic() - started > 0.05) if cancel else None
        expected = ManagerLockCancelled if cancel else ManagerPipelineWaitTimeout
        with pytest.raises(expected):
            with manager_pipeline_lock(tmp_path, timeout=2 if cancel else 0.05, cancelled=cancelled):
                pytest.fail("contended lock entered")
        assert time.monotonic() - started < 1
        assert owner.is_alive()
    assert (tmp_path / ".manager_pipeline.lock").exists()
    with manager_pipeline_lock(tmp_path, timeout=0):
        pass


def test_delegated_gate_timeout_does_not_release_the_parent_file_lock(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    with manager_pipeline_lock(tmp_path):
        def nested():
            with manager_pipeline_lock(tmp_path):
                entered.set()
                release.wait(3)
        owner = threading.Thread(target=contextvars.copy_context().run, args=(nested,))
        owner.start()
        assert entered.wait(1)
        try:
            with pytest.raises(ManagerPipelineWaitTimeout):
                with manager_pipeline_lock(tmp_path, timeout=0.05):
                    pytest.fail("delegated gate entered")
        finally:
            release.set()
            owner.join(1)
    with manager_pipeline_lock(tmp_path, timeout=0):
        pass


def prepared_handoff(root, *, on_wait=None):
    memory = LifeMemory.open(root)
    commits = []

    class NativeManager:
        project_root = root
        pipeline_lock = staticmethod(lambda **kwargs: manager_pipeline_lock(root, **kwargs))

        def decide_vertical(self, *args, **kwargs):
            return SimpleNamespace(execution_task="updated publication requirement")

        def commit_vertical_decision(self, *args, **kwargs):
            commits.append(kwargs)
            return SimpleNamespace(execution_task="updated publication requirement", vertical="math",
                                   workflow_mode="staged", stages=["solve", "review"], kind="research")

    prepared = front_door.prepare_manager_execution_task(
        memory, "new requirement", {}, root_task_id="change-1",
        ensure_runner=lambda *args: SimpleNamespace(manager=NativeManager()),
    )
    prepared.on_wait = on_wait
    return memory, prepared, commits


def test_foreground_commit_requests_yield_before_waiting(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="original requirement")
    phases = []
    memory, prepared, commits = prepared_handoff(tmp_path, on_wait=lambda: phases.append("waiting"))
    with held_pipeline(tmp_path, release_on_request=True):
        result = front_door.manager_continuous_handoff(
            memory, "new requirement", {}, root_task_id="change-1", prepared_handoff=prepared,
        )
    assert phases == ["waiting"]
    assert commits and result == "updated publication requirement"
    assert read_continuous_state(tmp_path).objective == result
    assert not (tmp_path / ".manager_pipeline_yield.json").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_handoff_keeps_objective_and_emits_terminal_event(tmp_path, monkeypatch, cancel):
    write_continuous_config(tmp_path, enabled=True, objective="original requirement")
    before = (tmp_path / "continuous.json").read_bytes()
    memory, prepared, commits = prepared_handoff(tmp_path)
    monkeypatch.setattr(front_door, "_handoff_wait_seconds", lambda: 2 if cancel else 0.05)
    with held_pipeline(tmp_path):
        started = time.monotonic()
        check = (lambda: time.monotonic() - started > 0.05) if cancel else None
        with pytest.raises(front_door.ManagerHandoffError):
            front_door.manager_continuous_handoff(
                memory, "new requirement", {}, root_task_id="change-1", prepared_handoff=prepared,
                cancelled=check,
            )
    assert not commits
    assert (tmp_path / "continuous.json").read_bytes() == before
    assert not (tmp_path / ".manager_pipeline_yield.json").exists()
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    terminal = [row for row in events if row["type"] == "life.manager.intent.failed"]
    assert len(terminal) == 1
    assert terminal[0]["phase"] == ("cancelled" if cancel else "handoff_wait")


def test_bounded_handoff_uses_the_same_safe_boundary_protocol(tmp_path):
    memory, prepared, commits = prepared_handoff(tmp_path)
    persisted = []

    def persist(body, _division):
        assert manager_pipeline_yield_requested(tmp_path)
        persisted.append(body)
        return body

    with held_pipeline(tmp_path, release_on_request=True):
        result = front_door.manager_bounded_handoff(
            memory, "new requirement", {}, persist, root_task_id="change-1",
            prepared_handoff=prepared,
        )
    assert result == "updated publication requirement" and commits
    assert persisted == [result]
    assert not (tmp_path / ".manager_pipeline_yield.json").exists()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.5", "3601"])
def test_operator_wait_budget_is_finite_and_validated(monkeypatch, value):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_HANDOFF_WAIT_SECONDS", value)
    with pytest.raises(ValueError):
        front_door._handoff_wait_seconds()


def test_cancel_is_rechecked_inside_the_acquired_lock(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="original requirement")
    memory, prepared, commits = prepared_handoff(tmp_path)
    cancelled = threading.Event()

    @contextmanager
    def cancel_at_acquisition(**kwargs):
        with manager_pipeline_lock(tmp_path, **kwargs):
            cancelled.set()
            yield

    prepared.manager.pipeline_lock = cancel_at_acquisition
    with pytest.raises(front_door.ManagerHandoffError):
        front_door.manager_continuous_handoff(
            memory, "new requirement", {}, root_task_id="change-1", prepared_handoff=prepared,
            cancelled=cancelled.is_set,
        )
    assert not commits
    assert read_continuous_state(tmp_path).objective == "original requirement"
