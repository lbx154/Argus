"""The collaboration view keeps the existing administrator and purpose boundary."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_training_data import chat, grant
from test_training_data import training as training

from argus_trial_server.training_routes import register_training_routes


def test_collaboration_routes_keep_admin_auth_and_readonly_access(training):
    data, _, _ = training
    grant(data)
    chat(training, task="task-one", text="Check the finite sum identity.")
    identities = {
        "tester": {"role": "trial", "tenant": "tenant-one", "readonly": False},
        "admin": {"role": "admin", "tenant": "admin", "readonly": True},
    }
    app = FastAPI()
    register_training_routes(
        app, data.analytics, lambda request: identities.get(request.headers.get("x-role")),
        journal=data.journal, controls=data.controls,
    )
    overview = "/admin/api/training/collaboration"
    detail = overview + "/tenant-one/s-project"
    with TestClient(app) as client:
        for path in (overview, detail + "?task_id=task-one"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"x-role": "tester"}).status_code == 403
            response = client.get(path, headers={"x-role": "admin"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        headers = {"x-role": "admin"}
        assert client.get(detail, params={"task_id": "task-other"}, headers=headers).status_code == 404
        assert client.get(detail, params={"task_id": "task-one", "purpose": "external_sharing"},
                          headers=headers).status_code == 403
        assert client.get(overview, params={"offset": -1}, headers=headers).status_code == 422
        assert client.get(overview, params={"query": "x" * 161}, headers=headers).status_code == 422
        assert client.get(overview, params={"tenant": "not-configured"}, headers=headers).status_code == 404
        assert client.get(detail, params={"task_id": "x" * 81}, headers=headers).status_code == 422
        assert client.get(overview, params={"purpose": "unknown"}, headers=headers).status_code == 400
        grant(data, internal=False)
        response = client.get(overview, headers=headers)
        assert response.status_code == 200
        assert response.json()["tasks"] == []
        assert client.get(detail, params={"task_id": "task-one"}, headers=headers).status_code == 403
