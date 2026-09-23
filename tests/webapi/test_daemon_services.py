"""Project/work-item HTTP services belong to their app, not server globals."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.daemon.state import DaemonStatus
from argus.life.memory import LifeMemory
from argus.webapi import (
    daemon_lifecycle,
    manager_dispatch,
    mission_items,
    project_crud,
    project_state,
    server,
)
from argus.webapi.daemon_services import DaemonServices
from argus.webapi.index_cache import CacheWaitTimeout


def _project(root: Path, sid: str = "s-service-probe") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), display_name="Service probe"))
    return life


def _status(path: Path, *, alive: bool = False, backend: str = "probe") -> DaemonStatus:
    return DaemonStatus(
        alive=alive, pid=123 if alive else None, started_at_iso=None,
        uptime_seconds=None, life_dir=path, backend=backend,
    )


def test_two_apps_keep_independent_status_delete_and_start_services(tmp_path, monkeypatch):
    sid = "s-service-probe"
    roots = [tmp_path / "first", tmp_path / "second"]
    lives = [_project(root, sid) for root in roots]
    reads, starts, clients = [[], []], [[], []], []
    for index, root in enumerate(roots):
        def read_status(path, *, selected=index):
            reads[selected].append(path)
            return _status(path, alive=selected == 0, backend=f"app-{selected}")

        def start(project_id, *, selected=index, **options):
            starts[selected].append((project_id, options))
            return {"rc": 0, "service": f"app-{selected}"}

        clients.append(TestClient(server.create_app(
            global_root=root,
            daemon_services=DaemonServices(read_status=read_status, start=start),
        )))

    def wrong_global(*_args, **_kwargs):
        pytest.fail("request consulted a mutable server service instead of its app dependency")

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', wrong_global)
    monkeypatch.setattr(daemon_lifecycle, 'start_project_daemon', wrong_global)
    # Interleave requests to expose process-global injection, including the
    # actual start command receipt and the real trash move.
    for index in (1, 0, 1):
        result = clients[index].get(f"/api/projects/{sid}/status")
        assert result.status_code == 200
        assert result.json()["daemon"]["backend"] == f"app-{index}"
    for index in (0, 1):
        result = clients[index].post(f"/api/projects/{sid}/daemon/start")
        assert result.status_code == 200
        assert result.json()["service"] == f"app-{index}"
        assert starts[index] == [(sid, {"global_root": roots[index], "resume_continuous": True})]
    assert clients[0].delete(f"/api/projects/{sid}").status_code == 409
    assert lives[0].is_dir()
    deleted = clients[1].delete(f"/api/projects/{sid}")
    assert deleted.status_code == 200
    assert not lives[1].exists()
    assert (roots[1] / deleted.json()["trash_path"]).is_dir()
    assert reads[0] == [lives[0], lives[0]]
    assert reads[1] == [lives[1], lives[1], lives[1]]


@pytest.mark.parametrize("autostart", [False, True])
def test_task_enqueue_passes_app_starter_and_persists_real_backlog(tmp_path, monkeypatch, autostart):
    sid = "s-service-probe"
    life = _project(tmp_path, sid)
    starts = []

    def start(project_id, **options):
        starts.append((project_id, options))
        return {"rc": 0, "service": "task-starter"}

    # Replace only the model-facing handoff; parsing, metadata, lifecycle lock,
    # backlog persistence and the HTTP command remain real.
    monkeypatch.setattr(
        manager_dispatch, "manager_bounded_handoff",
        lambda project_id, text, persist, **kwargs: persist(text, SimpleNamespace()),
    )
    client = TestClient(server.create_app(
        global_root=tmp_path, daemon_services=DaemonServices(read_status=_status, start=start),
    ))

    def wrong_global(*_args, **_kwargs):
        pytest.fail("task command bypassed its injected starter")

    monkeypatch.setattr(daemon_lifecycle, 'start_project_daemon', wrong_global)
    response = client.post(
        f"/api/projects/{sid}/tasks",
        json={"text": "Verify the local service boundary", "autostart_daemon": autostart},
    )
    assert response.status_code == 200
    item = LifeMemory.open(life).backlog.active()[0]
    assert item.id == response.json()["item"]["id"]
    assert item.objective == "Verify the local service boundary"
    if autostart:
        assert starts == [(sid, {
            "global_root": tmp_path, "resume_continuous": False, "reclaim_idle": True,
        })]
        assert response.json()["daemon"]["service"] == "task-starter"
    else:
        assert starts == []
        assert "daemon" not in response.json()


def test_direct_business_calls_use_concrete_defaults_without_server_lookup(tmp_path, monkeypatch):
    sid = "s-service-probe"
    life = _project(tmp_path, sid)

    def wrong_global(*_args, **_kwargs):
        pytest.fail("direct business call looked up the server module")

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', wrong_global)
    assert mission_items.get_status(sid, global_root=tmp_path)["daemon"]["alive"] is False
    result = project_crud.delete_project(sid, global_root=tmp_path)
    assert result["ok"] is True
    assert not life.exists()


def test_injected_status_failure_preserves_existing_business_error_behavior(tmp_path):
    sid = "s-service-probe"
    life = _project(tmp_path, sid)

    def unavailable(path):
        raise OSError("daemon state unavailable")

    status = mission_items.get_status(sid, global_root=tmp_path, read_status=unavailable)
    assert status["daemon"] == {"alive": False, "pid": None}
    with pytest.raises(OSError, match="daemon state unavailable"):
        project_crud.delete_project(sid, global_root=tmp_path, read_status=unavailable)
    assert life.is_dir()


def test_cache_wait_timeout_returns_retryable_http_response(tmp_path, monkeypatch):
    def timed_out(**kwargs):
        raise CacheWaitTimeout()

    monkeypatch.setattr(project_state, 'list_projects', timed_out)
    response = TestClient(server.create_app(global_root=tmp_path)).get("/api/projects")
    assert response.status_code == 503
    assert response.json() == {"detail": "Snapshot refresh timed out; retry shortly."}
    assert response.headers["Retry-After"] == "1"
