"""A stop request can abandon a contended mission-boundary lock."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import portalocker
import pytest

from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.manager import Manager
from argus.manager._session_ops import manager_pipeline_lock, request_manager_pipeline_yield


@pytest.mark.parametrize("held_lock", ["pipeline", "yield_metadata"])
def test_daemon_stops_while_another_caller_still_holds_pipeline_lock(tmp_path, held_lock):
    waiting = threading.Event()

    class WaitingManager(Manager):
        @contextmanager
        def pipeline_lock(self, *, cancelled=None):
            waiting.set()
            with super().pipeline_lock(cancelled=cancelled):
                yield

    calls = []

    class Supervisor:
        def run(self):
            calls.append(True)
            return {"stopped_by": "backlog_empty"}

    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path, backend="memory", poll_interval=0.01,
        continuous_open_ended=False,
    ))
    state = SimpleNamespace(
        runtime_root=tmp_path, cfg=worker.config,
        runner=SimpleNamespace(manager=WaitingManager(tmp_path)), sup=Supervisor(),
    )

    @contextmanager
    def hold_boundary():
        if held_lock == "pipeline":
            with manager_pipeline_lock(tmp_path):
                yield
        else:
            request_manager_pipeline_yield(tmp_path)
            # A shared metadata mutex must never be introduced on the stop path.
            with (tmp_path / ".manager_pipeline_yield.lock").open("a+b") as handle:
                portalocker.lock(handle, portalocker.LOCK_EX)
                try:
                    yield
                finally:
                    portalocker.unlock(handle)

    with hold_boundary():
        thread = threading.Thread(target=worker._rf_main_loop, args=(state,), daemon=True)
        thread.start()
        if held_lock == "pipeline":
            assert waiting.wait(timeout=5)
        else:
            assert worker._supervisor_execution_active.wait(timeout=5)
        started = time.monotonic()
        worker._stop.set()
        thread.join(timeout=1)
        elapsed = time.monotonic() - started
        stopped_before_release = not thread.is_alive()
    thread.join(timeout=5)
    assert stopped_before_release, "stop was blocked by another caller's pipeline lock"
    assert elapsed < 1.0
    assert calls == []
    assert not worker._supervisor_execution_active.is_set()
