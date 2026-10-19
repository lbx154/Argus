from __future__ import annotations

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.daemon.state import DaemonStatus, request_daemon_drain
from argus.webapi import server
from argus.webapi.project_state import daemon_dict


def _project(tmp_path, sid):
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return life


def test_pause_after_current_returns_at_once_and_reports_draining(tmp_path, monkeypatch):
    sid = "s-pause-after"
    _project(tmp_path, sid)
    calls = []

    def stop(life_dir, **kwargs):
        calls.append(kwargs)
        return 2  # the drain is recorded; the current task is still finishing

    monkeypatch.setattr(daemon_worker, "stop_daemon", stop)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        response = client.post(f"/api/projects/{sid}/daemon/stop", json={"after_current": True})
    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 0 and body["draining"] is True and body["command_status"] == "applied"
    assert calls == [{"timeout": 10.0, "drain": True, "force": False, "drain_timeout": 2.0}]


def test_plain_stop_keeps_its_previous_arguments(tmp_path, monkeypatch):
    sid = "s-plain-stop"
    _project(tmp_path, sid)
    calls = []
    monkeypatch.setattr(daemon_worker, "stop_daemon", lambda life_dir, **kwargs: calls.append(kwargs) or 0)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        assert client.post(f"/api/projects/{sid}/daemon/stop", json={"force": True}).json()["rc"] == 0
    assert calls == [{"timeout": 1.0, "drain": False, "force": True}]


def test_daemon_view_marks_a_pending_pause(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus.webapi import project_state

    life = _project(tmp_path, "s-draining")
    live = SimpleNamespace(alive=True, pid=4242, control_available=True, source="heartbeat",
                           heartbeat_age_seconds=1.0, phase="running", last_progress_at=None,
                           last_progress_event="", seconds_since_progress=None)
    monkeypatch.setattr(project_state, "web_daemon_liveness", lambda *_: live)
    status = DaemonStatus(alive=True, pid=4242, started_at_iso="x", uptime_seconds=1.0, life_dir=life)
    assert daemon_dict(status, life_dir=life)["draining"] is False
    request_daemon_drain(life, pid=4242)
    assert daemon_dict(status, life_dir=life)["draining"] is True
