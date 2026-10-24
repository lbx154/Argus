"""Mission-boundary fairness and cancellation, without concurrent state commits."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import LifeMemory
from argus.manager import Manager, dispatch, front_door
from argus.manager._session_ops import (
    ManagerLockCancelled,
    clear_manager_pipeline_yield,
    manager_pipeline_lock,
    manager_pipeline_yield_requested,
    request_manager_pipeline_yield,
)


def test_independent_yield_waiters_do_not_clear_each_other(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _: request_manager_pipeline_yield(tmp_path), range(8)))
    for token in tokens[:-1]:
        assert clear_manager_pipeline_yield(tmp_path, token)
        assert manager_pipeline_yield_requested(tmp_path)
    assert not clear_manager_pipeline_yield(tmp_path, "unrelated-request")
    assert clear_manager_pipeline_yield(tmp_path, tokens[-1])
    assert not manager_pipeline_yield_requested(tmp_path)


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


def _prepared(memory, manager, body, *, commit):
    return SimpleNamespace(
        mem=memory, body=body, root_task_id=None, intent_id="test-intent",
        manager=manager, execution_task=body, commit=commit,
        decision=SimpleNamespace(workflow_mode="direct"),
        completed=lambda *_args, **_kwargs: None,
        failed=lambda _exc: None,
    )


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
