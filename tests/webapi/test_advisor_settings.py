"""Advisor settings are project-scoped and cannot choose server executables."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from argus.advisor.config import load_advisor_config, save_advisor_config
from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.server import create_app


@pytest.fixture
def client(tmp_path):
    for sid in ("one", "two"):
        life = tmp_path / "projects" / sid
        life.mkdir(parents=True)
        write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    with TestClient(create_app(global_root=tmp_path, auth_token="advisor-test")) as value:
        value.headers["Authorization"] = "Bearer advisor-test"
        yield value, tmp_path


def test_settings_persist_for_one_project_without_selecting_the_main_model(client, monkeypatch):
    web, root = client
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "cheap-main")
    route = "/api/projects/one/advisor/config"
    initial = web.get(route)
    assert initial.status_code == 200
    assert initial.json()["config"]["enabled"] is False
    assert not (root / "projects/one/advisor").exists()
    updated = web.post(route, json={"enabled": True, "backend": "pi", "model": "provider/advisor", "effort": "high"})
    assert updated.status_code == 200
    assert updated.json()["config"]["model"] == "provider/advisor"
    assert load_advisor_config(root / "projects/one", env={}).model == "provider/advisor"
    assert web.get("/api/projects/two/advisor/config").json()["config"]["enabled"] is False
    assert web.get("/api/projects/one/advisor/consultations").json() == {"consultations": []}
    assert not (root / "projects/one/advisor/receipts.sqlite3").exists()


def test_web_cannot_read_or_override_local_runner_path_and_requires_auth(client):
    web, root = client
    route = "/api/projects/one/advisor/config"
    save_advisor_config(root / "projects/one", {"runner_bin": "/private/runtime/runner"})
    response = web.get(route)
    assert "/private/runtime" not in response.text
    assert "runner_bin" not in response.text
    assert web.post(route, json={"runner_bin": "/tmp/untrusted"}).status_code == 422
    web.headers.pop("Authorization")
    assert web.get(route).status_code == 401
    assert web.post(route, json={"enabled": False}).status_code == 401


def test_model_choices_do_not_expose_provider_credentials(client, monkeypatch):
    web, root = client
    directory = root / "pi"
    directory.mkdir()
    (directory / "models.json").write_text(json.dumps({"providers": {"argus": {
        "apiKey": "private-provider-token", "baseUrl": "https://private.example/api",
        "models": [{"id": "execution"}, {"id": "advisor"}],
    }}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(directory))
    response = web.get("/api/projects/one/advisor/config")
    assert response.json()["model_options"] == [
        {"backend": "pi", "model": "argus/execution"},
        {"backend": "pi", "model": "argus/advisor"},
    ]
    assert "private-provider-token" not in response.text
    assert "private.example" not in response.text


def test_invalid_configuration_does_not_partially_write_and_override_is_visible(client, monkeypatch):
    web, root = client
    route = "/api/projects/one/advisor/config"
    assert web.post(route, json={"enabled": True}).status_code == 400
    assert load_advisor_config(root / "projects/one", env={}).enabled is False
    assert web.post(route, json={"enabled": "true"}).status_code == 422
    assert web.post(route, json={"max_calls_per_turn": 0}).status_code == 422
    assert web.get("/api/projects/absent/advisor/config").status_code == 404
    monkeypatch.setenv("ARGUS_SKILL_ADVISOR_ENABLED", "1")
    monkeypatch.setenv("ARGUS_SKILL_ADVISOR_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_ADVISOR_MODEL", "provider/server-advisor")
    response = web.post(route, json={"enabled": False}).json()
    assert response["saved"]["enabled"] is False
    assert response["config"]["enabled"] is True
    assert response["config"]["model"] == "provider/server-advisor"
    assert "enabled" in response["overridden_fields"]
