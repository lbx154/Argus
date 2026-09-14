from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi.server import create_app


def test_timeline_preview_through_real_app_is_authenticated_and_read_only(tmp_path):
    client = TestClient(create_app(global_root=tmp_path, auth_token="test-token"))
    route = "/api/research/timeline/estimate"
    assert client.post(route, json={}).status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.post(route, headers=headers, json={}).status_code == 422
    body = dict(
        selected_proposal_id="idea",
        proposals=[
            dict(
                id="idea",
                title="Paper",
                tasks=[
                    dict(
                        id="pilot",
                        title="Validation",
                        phase="validation",
                        duration_hours=[1, 2, 9],
                        basis="Related published runtime",
                    )
                ],
            )
        ],
    )
    response = client.post(route, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["finish_hours"]["expected"] == 3
    assert not list(tmp_path.rglob("backlog.jsonl"))
    assert not list(tmp_path.rglob("000001.json"))


def test_browser_example_save_reload_and_conflict(tmp_path):
    home, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(home, SessionMeta(id="demo", workdir=str(workspace)))
    client = TestClient(create_app(global_root=home, auth_token="token"))
    headers = {"Authorization": "Bearer token"}
    source = client.get("/api/research/timeline/example", headers=headers)
    assert source.status_code == 200
    route = "/api/projects/demo/research/timeline"
    assert client.get(route, headers=headers).json() == {"latest": None}
    body = {"input": source.json(), "expected_version": 0, "reason": "Initial plan"}
    assert client.post(route, json=body).status_code == 401
    saved = client.post(route, headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert (workspace / ".argus/timeline/000001.json").exists()
    assert client.get(route, headers=headers).json()["latest"]["input"] == source.json()
    assert client.post(route, headers=headers, json=body).status_code == 409
    body.update(expected_version=1, reason="More resources")
    body["input"]["resources"]["gpu"] = 4
    revised = client.post(route, headers=headers, json=body)
    assert revised.status_code == 200, revised.text
    assert revised.json()["report"]["revision"]["reason"] == "More resources"
    assert client.get("/api/projects/missing/research/timeline", headers=headers).status_code == 404


def test_browser_rejects_timeline_symlink_outside_project(tmp_path):
    home, workspace, elsewhere = tmp_path / "state", tmp_path / "project", tmp_path / "elsewhere"
    (workspace / ".argus").mkdir(parents=True)
    elsewhere.mkdir()
    (workspace / ".argus/timeline").symlink_to(elsewhere, target_is_directory=True)
    write_session_meta(home, SessionMeta(id="demo", workdir=str(workspace)))
    client = TestClient(create_app(global_root=home))
    assert client.get("/api/projects/demo/research/timeline").status_code == 409
    assert not list(elsewhere.iterdir())
