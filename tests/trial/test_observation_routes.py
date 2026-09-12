"""Raw process routes reuse the existing administrator permission boundary."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus_skill.trial.training_routes import register_training_routes
from tests.trial.test_training_data import training as training


def portal(training):
    data, _, _ = training
    identities = {
        "trial": {"role": "trial", "tenant": "tenant-one", "readonly": False},
        "reader": {"role": "admin", "tenant": "admin", "readonly": True},
        "operator": {"role": "admin", "tenant": "admin", "readonly": False},
    }
    app = FastAPI()
    register_training_routes(
        app, data.analytics, lambda request: identities.get(request.headers.get("x-role")),
        journal=data.journal, controls=data.controls,
    )
    return app


def test_observations_keep_admin_read_access_and_forward_pagination(training, monkeypatch):
    app = portal(training)
    received = []

    def observations(purpose, tenant, sid, task_id, *, cursor, limit):
        received.append((purpose, tenant, sid, task_id, cursor, limit))
        return {"tenant_id": tenant, "sid": sid, "episodes": [],
                "pagination": {"has_more": False, "next_cursor": None}}

    monkeypatch.setattr(app.state.training_data, "observations", observations, raising=False)
    with TestClient(app) as client:
        path = "/admin/api/training/observations/tenant-one/s-project"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"x-role": "trial"}).status_code == 403
        response = client.get(path, headers={"x-role": "reader"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert received[-1] == ("internal_training", "tenant-one", "s-project", None, None, 200)
        response = client.get(path, headers={"x-role": "operator"}, params={
            "task_id": "task-one", "cursor": "next-page", "limit": 25,
        })
        assert response.status_code == 200
        assert received[-1][-3:] == ("task-one", "next-page", 25)
        for params in ({"limit": 0}, {"limit": 501}, {"cursor": "x" * 1025}):
            assert client.get(path, headers={"x-role": "reader"}, params=params).status_code == 422


def test_observation_export_streams_without_review_fields_and_keeps_mutation_auth(training, monkeypatch):
    app = portal(training)
    received = []

    def export(purpose, projects):
        received.append((purpose, projects))
        return iter([b"PK", b"synthetic-stream"]), "observations.zip"

    monkeypatch.setattr(app.state.training_data, "export_observations", export, raising=False)
    with TestClient(app) as client:
        path = "/admin/api/training/export-observations"
        body = {"purpose": "internal_training", "projects": [
            {"tenant_id": "tenant-one", "sid": "s-project"},
        ]}
        assert client.post(path, json=body).status_code == 401
        for role in ("trial", "reader"):
            assert client.post(path, json=body, headers={
                "x-role": role, "origin": "http://testserver",
            }).status_code == 403
        assert client.post(path, json=body, headers={"x-role": "operator"}).status_code == 403
        headers = {"x-role": "operator", "origin": "http://testserver"}
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert 'filename="observations.zip"' in response.headers["content-disposition"]
        assert response.content == b"PKsynthetic-stream"
        assert received == [(body["purpose"], body["projects"])]
        assert client.post(path, json={**body, "review": {}}, headers=headers).status_code == 400


def test_routes_browse_and_export_actual_zero_tool_records_without_quality_review(training):
    import io
    import json
    import zipfile

    from tests.trial.test_observed_training import begin, send

    data, _, _ = training
    episode = begin(training, role="planner.cycle0")
    send(data, episode, "context", {"messages": [
        {"role": "system", "content": "Actual application planning instruction."},
        {"role": "user", "content": "Create a reproducible experiment plan."},
    ], "tools": []})
    send(data, episode, "agent_end", {"messages": [
        {"role": "assistant", "content": "Use fixed seeds and retain every measurement."},
    ]})
    send(data, episode, "settled", {})
    app = portal(training)
    with TestClient(app) as client:
        path = "/admin/api/training/observations/tenant-one/s-project"
        headers = {"x-role": "reader"}
        response = client.get(path, params={"limit": 1}, headers=headers)
        assert response.status_code == 200
        first = response.json()
        assert first["episodes"][0]["role"] == "planner"
        assert first["episodes"][0]["quality"]["state"] == "not_evaluated"
        assert first["episodes"][0]["events"][0]["payload"]["messages"][0]["role"] == "system"
        assert first["pagination"]["has_more"] is True
        later = client.get(path, params={"cursor": first["pagination"]["next_cursor"]}, headers=headers)
        assert later.status_code == 200
        assert later.json()["pagination"]["has_more"] is False
        assert {event["kind"] for row in later.json()["episodes"] for event in row["events"]} == {
            "agent_end", "settled",
        }
        response = client.post("/admin/api/training/export-observations", json={
            "purpose": "internal_training", "projects": [{"tenant_id": "tenant-one", "sid": "s-project"}],
        }, headers={"x-role": "operator", "origin": "http://testserver"})
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            rows = [json.loads(line) for line in archive.read("observations.jsonl").splitlines()]
        assert any(row.get("role") == "planner" for row in rows)
        assert "Actual application planning instruction." in json.dumps(rows)
        assert "Use fixed seeds and retain every measurement." in json.dumps(rows)
        assert "human_operator" not in json.dumps(rows)
