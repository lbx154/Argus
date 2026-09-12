"""The collaboration view keeps the existing administrator and purpose boundary."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_collaboration_data import episode
from test_training_data import chat, grant
from test_training_data import training as training

from argus_skill.trial.training_routes import register_training_routes


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


def test_collaboration_requests_reuse_validation_but_recheck_authorization(training, monkeypatch):
    data, _, _ = training
    grant(data)
    episode(training)
    app = FastAPI()
    register_training_routes(
        app, data.analytics, lambda request: {"role": "admin", "readonly": True},
        journal=data.journal, controls=data.controls,
    )
    capture = app.state.training_data.capture
    validate = capture._sample
    validations = []

    def counted(*args, **kwargs):
        validations.append(True)
        return validate(*args, **kwargs)

    monkeypatch.setattr(capture, "_sample", counted)
    with TestClient(app) as client:
        overview = "/admin/api/training/collaboration"
        detail = overview + "/tenant-one/s-project?task_id=task-real"
        assert client.get(overview).json()["counts"]["candidates"] == 1
        assert client.get(detail).json()["quality"]["candidates"] == 1
        assert len(validations) == 1
        grant(data, internal=False)
        assert client.get(overview).json()["tasks"] == []
        assert client.get(detail).status_code == 403
