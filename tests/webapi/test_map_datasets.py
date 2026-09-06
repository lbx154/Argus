import json

from fastapi.testclient import TestClient

from argus_skill.webapi.server import create_app


def test_map_samples_are_separate_from_runnable_projects(tmp_path, monkeypatch):
    folder = tmp_path / "fixtures"
    folder.mkdir()
    data = {"id": "example", "read_only": True, "tasks": [{"id": "imported-task"}], "events": []}
    (folder / "example.json").write_text(json.dumps(data))
    (folder / "index.json").write_text(json.dumps({"datasets": [{"id": "example"}]}))
    monkeypatch.setenv("ARGUS_MAP_DATASETS_DIR", str(folder))
    client = TestClient(create_app(global_root=tmp_path / "state", auth_token="test-token"))
    assert client.get("/api/map-datasets/example").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/api/map-datasets/example", headers=headers).json() == data
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(
            "/api/map-datasets/example", headers=headers
        ).status_code in (404, 405)
    assert client.post("/api/projects/example/daemon/start", headers=headers).status_code == 404
    assert client.get("/api/projects", headers=headers).json()["projects"] == []


def test_map_samples_reject_symlinks_and_invalid_contract(tmp_path, monkeypatch):
    folder = tmp_path / "fixtures"
    folder.mkdir()
    outside = tmp_path / "private.json"
    outside.write_text('{"secret":"not-a-fixture"}')
    (folder / "escape.json").symlink_to(outside)
    (folder / "invalid.json").write_text('{"id":"invalid","read_only":false}')
    monkeypatch.setenv("ARGUS_MAP_DATASETS_DIR", str(folder))
    client = TestClient(create_app(global_root=tmp_path / "state"))
    assert client.get("/api/map-datasets/escape").status_code == 404
    assert client.get("/api/map-datasets/invalid").status_code == 503
    assert client.get("/api/map-datasets/index").status_code == 404
    assert client.get("/api/map-datasets/unknown").status_code == 404


def test_map_dataset_feature_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_MAP_DATASETS_DIR", raising=False)
    client = TestClient(create_app(global_root=tmp_path))
    assert client.get("/api/map-datasets").json() == {"datasets": []}
    assert client.get("/api/map-datasets/example").status_code == 404
