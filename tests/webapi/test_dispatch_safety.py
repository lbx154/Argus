"""Authenticated containment only; this is not a billing-repair endpoint."""
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.server import create_app


def test_quiesce_requires_auth_epoch_and_rejects_uploaded_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    write_session_meta(tmp_path, SessionMeta(id="synthetic-project", created=1, last_active=1))
    project = tmp_path / "projects" / "synthetic-project"
    damaged = project / "usage.jsonl"
    damaged.write_bytes(b'{"call_id":"unknown')
    headers = {"Authorization": "Bearer synthetic-test-token"}
    body = {"expected_epoch": 0, "reason": "hold dispatch, preserve debt"}
    with TestClient(create_app(global_root=tmp_path, auth_token="synthetic-test-token")) as client:
        path = "/api/projects/synthetic-project/dispatch-safety"
        assert client.get(path).status_code == 401
        assert client.post(path+"/quiesce", json=body).status_code == 401
        assert client.post(path+"/quiesce", headers=headers, json={**body, "evidence": {"cost_usd": 0}}).status_code == 422
        assert client.get(path, headers=headers).json()["epoch"] == 0
        result = client.post(path+"/quiesce", headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["paused"] and not result.json()["accounting_settled"]
        assert client.post(path+"/quiesce", headers=headers, json=body).status_code == 409
        assert client.get("/api/projects/synthetic-project/cost-control", headers=headers).status_code == 503
    assert damaged.read_bytes() == b'{"call_id":"unknown'
