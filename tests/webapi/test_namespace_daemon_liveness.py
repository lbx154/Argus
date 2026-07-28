from __future__ import annotations

import json
import time
from types import SimpleNamespace

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi.daemon_liveness import web_daemon_liveness
from argus_skill.webapi.project_state import list_projects


def _status(*, alive: bool, pid: int | None = None):
    return SimpleNamespace(
        alive=alive,
        pid=pid,
        health_state="active" if alive else "stopped",
        last_progress_at=90.0 if alive else None,
        last_progress_event="engineer.progress" if alive else "",
        seconds_since_progress=10.0 if alive else None,
    )


def _namespace_sidecars(tmp_path, *, last_event_at: float = 95.0) -> None:
    (tmp_path / "daemon.pid").write_text("2\n")
    (tmp_path / "daemon.status.json").write_text(json.dumps({"pid": 2}))
    (tmp_path / "daemon.health.json").write_text(
        json.dumps(
            {
                "pid": 2,
                "phase": "active",
                "last_event_at": last_event_at,
                "last_progress_at": 90.0,
                "last_progress_event": "engineer.progress",
            }
        )
    )


def test_host_pid_lock_remains_controllable(tmp_path) -> None:
    result = web_daemon_liveness(tmp_path, _status(alive=True, pid=41), now=100.0)

    assert result.alive is True
    assert result.control_available is True
    assert result.source == "pid_lock"
    assert result.pid == 41


def test_fresh_namespace_heartbeat_is_visible_but_not_controllable(tmp_path) -> None:
    _namespace_sidecars(tmp_path)

    result = web_daemon_liveness(tmp_path, _status(alive=False), now=100.0)

    assert result.alive is True
    assert result.control_available is False
    assert result.source == "namespace_heartbeat"
    assert result.pid == 2
    assert result.heartbeat_age_seconds == 5.0


def test_stale_namespace_heartbeat_is_not_alive(tmp_path, monkeypatch) -> None:
    _namespace_sidecars(tmp_path, last_event_at=1.0)
    monkeypatch.setenv("ARGUS_SKILL_WEB_NAMESPACE_HEARTBEAT_S", "10")

    result = web_daemon_liveness(tmp_path, _status(alive=False), now=100.0)

    assert result.alive is False
    assert result.control_available is False
    assert result.source == "none"


def test_project_index_surfaces_namespace_daemon_without_host_control(tmp_path) -> None:
    sid = "s-namespace1"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, created=1.0, last_active=2.0, cwd=str(life_dir)),
    )
    _namespace_sidecars(life_dir, last_event_at=time.time())

    project = next(
        row
        for row in list_projects(global_root=tmp_path, include_empty=True)
        if row["id"] == sid
    )

    assert project["daemon_alive"] is True
    assert project["daemon_control_available"] is False
    assert project["daemon_liveness_source"] == "namespace_heartbeat"
    assert project["daemon_pid"] == 2
