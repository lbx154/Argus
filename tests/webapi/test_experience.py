import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.webapi.server import create_app

AUTH = {"Authorization": "Bearer test-token"}


def project(root, sid="s-experience"):
    life = root / "projects" / sid
    workspace = root / "workspaces" / sid
    life.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (workspace / "report.md").write_text("# Verified\nOriginal measured conclusion.", encoding="utf-8")
    (workspace / ".env").write_text("PRIVATE=not-searchable", encoding="utf-8")
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))
    append_turn(life, "operator", "比较两种方法的证据", message_id="question-1")
    append_turn(life, "argus", "See report.md. 需要更多验证。",
                metadata={"delivery": {"summary": "[Report](report.md)", "targets": [{"path": "report.md", "label": "Report", "source": "solo_output"}]}})
    return life, workspace


def test_draft_is_shared_and_stale_writes_cannot_overwrite_other_devices(tmp_path):
    project(tmp_path)
    path = "/api/projects/s-experience/browser-draft"
    with TestClient(create_app(global_root=tmp_path, auth_token="test-token")) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=AUTH).json() == {"value": {}, "revision": 0}
        payload = {"value": {"text": "Device A draft"}, "revision": 0}
        saved = client.post(path, headers=AUTH, json=payload)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.post(path, headers=AUTH, json={"value": {"text": "Device B draft"}, "revision": 0}).status_code == 409
        assert client.get(path, headers=AUTH).json()["value"]["text"] == "Device A draft"
        assert client.post(path, headers=AUTH, json={"value": {"text": "x" * 32001}, "revision": 1}).status_code == 422
        assert client.post(path, headers=AUTH, json={"value": {"uploaded": [{"attachment_id": "invalid"}]}, "revision": 1}).status_code == 422
        assert client.get("/api/projects/missing/browser-draft", headers=AUTH).status_code == 404


def test_editions_preserve_source_and_handle_concurrent_saves(tmp_path):
    life, workspace = project(tmp_path)
    path = "/api/projects/s-experience/editions/artifact"
    original = (workspace / "report.md").read_text()
    with TestClient(create_app(global_root=tmp_path, auth_token="test-token")) as client:
        assert client.get(path, headers=AUTH, params={"source": "report.md"}).json() == {"original": original, "versions": []}
        body = {"source": "report.md", "base_version": 0, "content": "# Unreviewed\nUser edit."}
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: client.post(path, headers=AUTH, json=body).status_code, range(2)))
        assert sorted(statuses) == [200, 409]
        versions = client.get(path, headers=AUTH, params={"source": "report.md"}).json()["versions"]
        assert [item["version"] for item in versions] == [0, 1]
        assert versions[0]["content"] == original
        assert (workspace / "report.md").read_text() == original
        assert json.loads((life / "transcript.jsonl").read_text().splitlines()[-1])["text"] == "See report.md. 需要更多验证。"
        assert client.get(path, headers=AUTH, params={"source": ".env"}).status_code == 404
        assert client.post(path, headers=AUTH, json={**body, "source": "../outside.md"}).status_code == 404


def test_searches_old_messages_results_and_editions_but_not_unregistered_files(tmp_path):
    life, _ = project(tmp_path)
    for index in range(130):
        append_turn(life, "operator", f"Later unrelated message {index}")
    with TestClient(create_app(global_root=tmp_path, auth_token="test-token")) as client:
        assert client.get("/api/search?q=证据").status_code == 401
        result = client.get("/api/search?q=证据", headers=AUTH)
        assert result.status_code == 200
        assert result.json()["results"][0]["line"] == 1
        assert result.json()["results"][0]["role"] == "operator"
        assert client.get("/api/search?q=measured", headers=AUTH).json()["results"][0]["path"] == "report.md"
        client.post("/api/projects/s-experience/editions/artifact", headers=AUTH, json={
            "source": "report.md", "base_version": 0, "content": "Revised unique-edition-finding",
        }).raise_for_status()
        edition = client.get("/api/search?q=unique-edition-finding", headers=AUTH).json()["results"][0]
        assert edition["kind"] == "edition" and edition["version"] == 1
        assert not client.get("/api/search?q=not-searchable", headers=AUTH).json()["results"]
        limited = client.get("/api/search?q=Later&limit=1", headers=AUTH).json()
        assert len(limited["results"]) == 1 and limited["partial"]


def test_organization_merges_independent_project_fields(tmp_path):
    project(tmp_path)
    project(tmp_path, "s-second")
    with TestClient(create_app(global_root=tmp_path, auth_token="test-token")) as client:
        path = "/api/browser-state/organization"
        for sid, field in [("s-experience", "pinned"), ("s-second", "archived")]:
            assert client.post(path, headers=AUTH, json={"sid": sid, "field": field, "value": True}).status_code == 200
        values = client.get(path, headers=AUTH).json()["value"]
        assert values["s-experience"]["pinned"] and values["s-second"]["archived"]
        assert client.post(path, headers=AUTH, json={"sid": "missing", "field": "pinned", "value": True}).status_code == 404


def test_browser_session_uses_httponly_cookie_and_rejects_cross_origin_mutations(tmp_path):
    project(tmp_path)
    with TestClient(create_app(global_root=tmp_path, auth_token="test-token")) as client:
        assert client.post("/api/browser-session").status_code == 401
        signed_in = client.post("/api/browser-session", headers=AUTH)
        assert signed_in.status_code == 200
        cookie = signed_in.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=2592000" in cookie
        assert client.get("/api/projects").status_code == 200
        path = "/api/projects/s-experience/browser-draft"
        body = {"value": {"text": "Authenticated cookie draft"}, "revision": 0}
        assert client.post(path, headers={"Origin": "https://evil.test"}, json=body).status_code == 401
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, headers={"Origin": "http://testserver"}, json=body).status_code == 200
        assert client.delete("/api/browser-session", headers={"Origin": "http://testserver"}).status_code == 200
        assert client.get("/api/projects").status_code == 401
