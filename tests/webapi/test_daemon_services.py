"""Project/work-item HTTP services belong to their app, not server globals."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import (
    mission_items,
    project_crud,
    project_state,
    server,
)
from argus.webapi.index_cache import CacheWaitTimeout


def _project(root: Path, sid: str = "s-service-probe") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), display_name="Service probe"))
    return life


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
