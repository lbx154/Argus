from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import server
from argus.webapi.project_state import compact_backlog_item
from argus.webapi.routes.artifacts import _bundle_filename

HEADERS = {"Authorization": "Bearer test-token"}


def _delivered_project(tmp_path: Path, sid: str) -> tuple[Path, Path]:
    life, workspace = tmp_path / "projects" / sid, tmp_path / "workspace"
    life.mkdir(parents=True)
    (workspace / "study").mkdir(parents=True)
    (workspace / "study" / "README.md").write_text("# Report\n[data](table.csv)", encoding="utf-8")
    (workspace / "study" / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (workspace / ".env").write_text("private", encoding="utf-8")
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))
    append_turn(life, "argus", "Report is ready", metadata={"delivery": {
        "summary": "[report](study/README.md)",
        "targets": [{"path": "study/README.md", "label": "report", "source": "solo_output"}],
    }})
    return life, workspace


def test_results_bundle_zips_only_allowlisted_files_with_the_summary(tmp_path):
    sid = "s-bundle"
    _delivered_project(tmp_path, sid)
    with TestClient(server.create_app(global_root=tmp_path, auth_token="test-token")) as client:
        response = client.post(f"/api/projects/{sid}/artifacts/bundle", headers=HEADERS, json={
            "paths": ["study/README.md", "study/table.csv", ".env", "../outside.txt"],
            "name": "视频去噪 / 第一轮", "summary": "# Result\nIt works.",
        })
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        assert {"study/README.md", "study/table.csv", "README-Argus.md"} <= names
        assert ".env" not in names
        notes = archive.read("README-Argus.md").decode()
        assert "It works." in notes and ".env" in notes
        empty = client.post(f"/api/projects/{sid}/artifacts/bundle", headers=HEADERS, json={"paths": [".env"]})
        assert empty.status_code == 404
        assert client.post(f"/api/projects/{sid}/artifacts/bundle", json={"paths": ["study/README.md"]}).status_code == 401
    assert _bundle_filename("a/b:c") == "a b c.zip"
    assert _bundle_filename("") == "argus-results.zip"


def _workspace_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    workspace = tmp_path / "project"
    (workspace / "results").mkdir(parents=True)
    (workspace / "results" / "model.bin").write_bytes(b"\x00\x01weights")
    (workspace / ".env").write_text("TOKEN=x", encoding="utf-8")
    (workspace / "server.pem").write_text("key", encoding="utf-8")
    monkeypatch.setenv("ARGUS_V2_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARGUS_V2_WORKSPACE_PROFILES", json.dumps([{"label": "Fixture", "path": str(workspace)}]))
    client = TestClient(server.create_app(global_root=tmp_path / "argus-state"))
    sid = "s-fixture01"
    profile = next(row for row in client.get("/api/v2/workspaces", params={"sid": sid}).json()["profiles"] if row["label"] == "Fixture")
    return client, {"sid": sid, "workspace_id": profile["id"]}


def test_any_workspace_file_downloads_except_credentials(tmp_path, monkeypatch):
    client, context = _workspace_client(tmp_path, monkeypatch)
    response = client.get("/api/v2/workspace/download", params={**context, "path": "results/model.bin"})
    assert response.status_code == 200
    assert response.content == b"\x00\x01weights"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''model.bin"
    for blocked in [".env", "server.pem"]:
        assert client.get("/api/v2/workspace/download", params={**context, "path": blocked}).status_code == 403
    assert client.get("/api/v2/workspace/download", params={**context, "path": "../outside"}).status_code == 400


def test_snapshot_reports_disk_space_and_compact_tasks_carry_time_and_reason(tmp_path):
    sid = "s-storage"
    life, _ = _delivered_project(tmp_path, sid)
    item = LifeMemory.open(life).backlog.add(BacklogItem.new(title="Train", objective="Train"))
    with TestClient(server.create_app(global_root=tmp_path, auth_token="test-token")) as client:
        snapshot = client.get(f"/api/projects/{sid}/snapshot?compact=true", headers=HEADERS).json()
    storage = snapshot["storage"]
    assert storage["total_bytes"] > 0 and 0 <= storage["used_fraction"] <= 1
    row = compact_backlog_item(item)
    assert row["ts"] == item.ts and row["last_error"] == ""
    # The status line tells an Argus-records step from the person's work by it.
    owned = BacklogItem.new(title="Repair", objective="Repair", owns_paths=[".argus/maintenance/repair"])
    assert compact_backlog_item(owned)["owns_paths"] == [".argus/maintenance/repair"]

