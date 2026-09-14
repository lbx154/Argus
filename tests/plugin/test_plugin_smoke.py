from __future__ import annotations

from pathlib import Path

from argus.plugin.service import ArgusPluginService


def test_real_service_lifecycle_without_model_backend(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    workdir = tmp_path / "work"
    workdir.mkdir()
    service = ArgusPluginService(global_root=state_root)

    created = service.create_project(str(workdir), name="Plugin smoke")
    assert created["ok"] is True
    project_id = created["project"]["sid"]
    assert created["project"]["daemon"]["alive"] is False

    projects = service.list_projects(str(workdir))
    assert projects["ok"] is True
    assert [row["id"] for row in projects["projects"]] == [project_id]

    status = service.status(project_id)
    assert status["ok"] is True
    assert status["status"]["daemon"]["alive"] is False
    assert status["status"]["backlog_pending"] == []

    artifacts = service.artifacts(project_id)
    assert artifacts == {"ok": True, "artifacts": []}

    stopped = service.stop(project_id)
    assert stopped["ok"] is True
    assert stopped["result"]["rc"] == 0
    assert stopped["result"]["already_stopped"] is True
