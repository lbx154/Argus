from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.core.models import RunnerOptions, RunnerResult
from argus.core.run_gateway import run_exec
from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import LifeMemory, MemoryBundle
from argus.manager import config_intent, front_door
from argus.manager._session_ops import manager_pipeline_lock
from argus.webapi import manager_bridge, manager_dispatch, manager_state, project_crud, server
from argus.webapi.daemon_services import DaemonServices


def _project(root):
    sid = "s-control-responsive"
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return sid, life


def test_evicting_a_manager_context_cannot_reuse_a_waiting_requests_identity():
    sid = "s-evicted-control"
    with manager_dispatch._manager_request_scope(sid) as cancelled:
        assert not cancelled()
        manager_state.release_manager_context(sid)
        assert cancelled()


def test_continuous_stop_does_not_wait_for_busy_manager_lock(tmp_path):
    sid, life = _project(tmp_path)
    write_continuous_config(life, enabled=True, objective="Existing goal")
    held, release = threading.Event(), threading.Event()

    def holder():
        with manager_state.manager_context_lock(sid):
            held.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=1) as pool, TestClient(server.create_app(global_root=tmp_path)) as client:
        future = pool.submit(holder)
        try:
            assert held.wait(1)
            started = time.monotonic()
            response = client.post(f"/api/projects/{sid}/continuous", json={"enabled": False})
            assert time.monotonic() - started < 0.75
            assert response.status_code == 200
            assert not future.done()
            state = read_continuous_state(life)
            assert not state.enabled and state.objective == "Existing goal"
        finally:
            release.set()
            future.result(timeout=2)


def test_http_stop_interrupts_manager_provider_and_prevents_late_dispatch(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    entered, cleanup = threading.Event(), threading.Event()
    reasons = []

    class SlowBackend:
        def run_exec(self, *, options, **kwargs):
            interrupt = options.external_interrupt_reason_provider
            assert callable(interrupt)
            entered.set()
            deadline = time.monotonic() + 3
            while not cleanup.is_set() and time.monotonic() < deadline:
                reason = interrupt()
                if reason:
                    reasons.append(reason)
                    return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason)
                cleanup.wait(0.01)
            raise AssertionError("Manager provider did not receive stop")

    def classify(*args, **kwargs):
        run_exec(SlowBackend(), prompt="classify", options=RunnerOptions(), run_label="manager-test")
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(daemon_worker, 'stop_daemon', lambda *args, **kwargs: 0)
    with ThreadPoolExecutor(max_workers=1) as pool, TestClient(server.create_app(global_root=tmp_path)) as client:
        future = pool.submit(manager_bridge.manager_message, sid, "Develop the next experiment", global_root=tmp_path)
        try:
            assert entered.wait(2)
            response = client.post(f"/api/projects/{sid}/daemon/stop", json={"force": True})
            assert response.status_code == 200 and response.json()["command_status"] == "applied"
            assert future.result(timeout=1)["kind"] == "cancelled"
            assert reasons == ["operator interrupted Manager request"]
            assert LifeMemory.open(life).backlog.all() == []
        finally:
            cleanup.set()


def test_new_objective_supersedes_an_older_waiting_handoff(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    held, release, prepared = threading.Event(), threading.Event(), threading.Event()
    commits = []

    class Manager:
        def classify_front_door(self, text, **kwargs):
            return None, None, "complex"

        def decide_vertical(self, text, **kwargs):
            if text == "Old goal":
                prepared.set()
            return SimpleNamespace(execution_task=text)

        def commit_vertical_decision(self, text, decision, **kwargs):
            commits.append(text)
            return SimpleNamespace(execution_task=decision.execution_task)

        def pipeline_lock(self, *, cancelled=None):
            return manager_pipeline_lock(life, cancelled=cancelled)

    ensure = lambda *args: SimpleNamespace(manager=Manager())
    monkeypatch.setattr(front_door, "_ensure_manager_runner", ensure)
    monkeypatch.setattr(config_intent, "_ensure_manager_runner", ensure)

    def holder():
        with manager_pipeline_lock(life):
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=3) as pool:
        holding = pool.submit(holder)
        old = new = None
        try:
            assert held.wait(1)
            old = pool.submit(project_crud.set_continuous, sid, enabled=True, objective="Old goal", global_root=tmp_path)
            assert prepared.wait(2)
            new = pool.submit(project_crud.set_continuous, sid, enabled=True, objective="New goal", global_root=tmp_path)
            with pytest.raises(front_door.ManagerHandoffSupersededError):
                old.result(timeout=1)
            assert commits == [] and not holding.done()
            release.set()
            assert new.result(timeout=2) is True
            assert read_continuous_state(life).objective == "New goal"
            assert commits == ["New goal"]
        finally:
            release.set()


def test_old_request_cannot_adopt_a_new_generation_after_slow_project_open(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    opened, release = threading.Event(), threading.Event()
    real_open = MemoryBundle.for_cwd
    commits = []

    def delayed_open(*args, **kwargs):
        if threading.current_thread().name.startswith("older"):
            opened.set()
            assert release.wait(3)
        return real_open(*args, **kwargs)

    def commit(mem, objective, state, *, cancelled=None, **kwargs):
        assert not cancelled()
        write_continuous_config(mem.project_root, enabled=True, objective=objective)
        commits.append(objective)
        return objective

    monkeypatch.setattr(MemoryBundle, "for_cwd", delayed_open)
    monkeypatch.setattr(config_intent, "_front_door_classify", lambda *args, **kwargs: (None, None, "complex"))
    monkeypatch.setattr(front_door, "manager_continuous_handoff", commit)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="older") as pool:
        old = pool.submit(project_crud.set_continuous, sid, enabled=True, objective="Old", global_root=tmp_path)
        try:
            assert opened.wait(1)
            assert project_crud.set_continuous(sid, enabled=True, objective="New", global_root=tmp_path)
            release.set()
            with pytest.raises(front_door.ManagerHandoffSupersededError):
                old.result(timeout=1)
            assert commits == ["New"]
            assert read_continuous_state(life).objective == "New"
        finally:
            release.set()


def test_stop_after_goal_commit_prevents_late_http_start(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    committed, release = threading.Event(), threading.Event()
    starts = []

    def commit(mem, objective, state, **kwargs):
        write_continuous_config(mem.project_root, enabled=True, objective=objective)
        committed.set()
        assert release.wait(3)
        return objective

    monkeypatch.setattr(config_intent, "_front_door_classify", lambda *args, **kwargs: (None, None, "complex"))
    monkeypatch.setattr(front_door, "manager_continuous_handoff", commit)
    services = DaemonServices(read_status=daemon_worker.read_daemon_status,
        start=lambda *args, **kwargs: starts.append(True) or {"rc": 0})
    app = server.create_app(global_root=tmp_path, daemon_services=services)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.post, f"/api/projects/{sid}/continuous",
            json={"enabled": True, "objective": "Goal"})
        try:
            assert committed.wait(1)
            assert client.post(f"/api/projects/{sid}/continuous", json={"enabled": False}).status_code == 200
            release.set()
            assert request.result(timeout=1).status_code == 409
            assert starts == []
            assert not read_continuous_state(life).enabled
            assert not manager_state._STATES[sid]["config"]["continuous"]
        finally:
            release.set()


def test_failed_replacement_preserves_cached_committed_goal(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    write_continuous_config(life, enabled=True, objective="Previous")
    state = manager_state._chat_state_for(sid)
    state.update(config={"continuous": True}, continuous_objective="Previous")

    def fail(*args, **kwargs):
        raise front_door.ManagerHandoffError("backend unavailable")

    monkeypatch.setattr(config_intent, "_front_door_classify", fail)
    with pytest.raises(front_door.ManagerHandoffError):
        project_crud.set_continuous(sid, enabled=True, objective="Replacement", global_root=tmp_path)
    assert read_continuous_state(life).objective == state["continuous_objective"] == "Previous"
    assert read_continuous_state(life).enabled and state["config"]["continuous"]


@pytest.mark.parametrize("preview", [manager_bridge.manager_plan, manager_bridge.manager_rewrite])
def test_stop_reaches_raw_backend_while_preview_owns_manager_lock(tmp_path, monkeypatch, preview):
    sid, _life = _project(tmp_path)
    entered, cleanup = threading.Event(), threading.Event()

    class Backend:
        def run_exec(self, *, options, **kwargs):
            interrupt = options.external_interrupt_reason_provider
            assert callable(interrupt)
            entered.set()
            for _ in range(200):
                if reason := interrupt():
                    return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason)
                if cleanup.wait(0.01):
                    break
            raise AssertionError("preview did not receive interruption")

    backend = Backend()
    runner = SimpleNamespace(planner_backend=backend, _backend=backend)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *args, **kwargs: runner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(preview, sid, "Investigate this objective", global_root=tmp_path)
        try:
            assert entered.wait(1)
            manager_state.interrupt_manager_turns(sid, clear_continuous=False)
            assert future.result(timeout=1)["error"]
        finally:
            cleanup.set()
