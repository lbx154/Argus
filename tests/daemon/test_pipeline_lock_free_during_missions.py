"""A running mission never holds the Manager commit lock."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.manager import Manager
from argus.manager._session_ops import manager_pipeline_lock


def test_operator_handoff_commits_while_a_mission_runs(tmp_path):
    running, release = threading.Event(), threading.Event()

    class Supervisor:
        def run(self):
            running.set()
            release.wait(timeout=10)
            return {"stopped_by": "backlog_empty"}

    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path, backend="memory", poll_interval=0.01,
        continuous_open_ended=False,
    ))
    state = SimpleNamespace(
        runtime_root=tmp_path, cfg=worker.config,
        runner=SimpleNamespace(manager=Manager(tmp_path)), sup=Supervisor(),
    )
    thread = threading.Thread(target=worker._rf_main_loop, args=(state,), daemon=True)
    thread.start()
    try:
        assert running.wait(timeout=5)
        committed = threading.Event()

        def commit():
            with manager_pipeline_lock(tmp_path):
                committed.set()

        threading.Thread(target=commit, daemon=True).start()
        assert committed.wait(timeout=2), "a running mission blocked the operator handoff"
    finally:
        release.set()
        worker._stop.set()
        thread.join(timeout=5)
