import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from argus_skill.core import plugin_manager as manager
from argus_skill.trial.plugins import configure_plugins
from argus_skill.webapi.routes.plugins import register_plugin_routes


@pytest.fixture
def hosted_plugin(tmp_path, monkeypatch):
    root = tmp_path / "account"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv("ARGUS_CRYSTALPILOT_WORKSPACE", "")
    monkeypatch.delenv("ARGUS_PLUGINS_PREINSTALL", raising=False)
    configure_plugins(root)
    workspace = root / "crystalpilot-runtime"
    calls = []

    class Plugin:
        def mount(self, host, ctx):
            app = FastAPI()

            @app.get("/folders")
            def folders(path: str = ""):
                calls.append(path)
                return {"path": path}

            @app.post("/projects/open")
            async def open_project(request: Request):
                body = await request.json()
                calls.append(body)
                return body

            @app.post("/projects/import-structure")
            async def upload(request: Request):
                async with request.form() as form:
                    content = await form["file"].read()
                    calls.append(content)
                    return {"project": form["project"], "content": content.decode()}

            host.mount("/api/plugins/crystalpilot", app)

    plugin = Plugin()
    monkeypatch.setattr(manager, "load_plugin", lambda *a, **kw: plugin)
    monkeypatch.setattr(manager, "compatibility", lambda *a, **kw: {"supported": True})
    monkeypatch.setattr(manager, "mutate", lambda *a, **kw: {"status": "running"})
    app = FastAPI()
    register_plugin_routes(app, SimpleNamespace(global_root=root, require_auth=lambda: None, token=None))
    # No lifespan: these focused route tests must not install real dependencies.
    return TestClient(app), workspace, calls


def test_hosted_defaults_prepare_private_workspace_without_overriding_operator_list(hosted_plugin, tmp_path):
    _, workspace, _ = hosted_plugin
    assert (workspace / "test-projects").is_dir()
    assert os.environ["ARGUS_PLUGINS_PREINSTALL"] == "crystalpilot"
    configure_plugins(tmp_path / "other-account")
    assert os.environ["ARGUS_CRYSTALPILOT_WORKSPACE"] != str(workspace)
    os.environ["ARGUS_PLUGINS_PREINSTALL"] = ""
    configure_plugins(tmp_path / "other-account")
    assert os.environ["ARGUS_PLUGINS_PREINSTALL"] == ""


def test_workbench_can_browse_open_and_upload_its_own_data(hosted_plugin):
    client, workspace, calls = hosted_plugin
    prefix = "/api/plugins/crystalpilot"
    assert client.get(prefix + "/folders").status_code == 200
    assert client.get(prefix + "/folders", params={"path": str(workspace)}).status_code == 200
    project = str(workspace / "test-projects" / "sample")
    body = {"path": project, "auto_approve": True}
    assert client.post(prefix + "/projects/open", json=body).json() == body
    response = client.post(prefix + "/projects/import-structure",
                           data={"project": project}, files={"file": ("sample.cif", b"data_test\n")})
    assert response.status_code == 200
    assert response.json() == {"project": project, "content": "data_test\n"}
    assert calls[-1] == b"data_test\n"


@pytest.mark.parametrize("escape", ["absolute", "traversal", "symlink"])
def test_hosted_folder_and_project_entry_cannot_escape_account(hosted_plugin, tmp_path, escape):
    client, workspace, calls = hosted_plugin
    outside = tmp_path / "other-account"
    outside.mkdir()
    path = str(outside)
    if escape == "traversal":
        path = str(workspace / ".." / ".." / "other-account")
    elif escape == "symlink":
        link = workspace / "linked-account"
        link.symlink_to(outside, target_is_directory=True)
        path = str(link / "sample")
    prefix = "/api/plugins/crystalpilot"
    assert client.get(prefix + "/folders", params={"path": path}).status_code == 403
    assert client.post(prefix + "/projects/open", json={"path": path}).status_code == 403
    assert client.post(prefix + "/projects/import-structure",
                       data={"project": path}, files={"file": ("sample.cif", b"data_test")}).status_code == 403
    assert calls == []


@pytest.mark.parametrize("body", [{}, [], {"path": 42}, {"path": "\x00"}, {"path": ""}])
def test_invalid_hosted_project_request_is_explicit(hosted_plugin, body):
    client, _, calls = hosted_plugin
    assert client.post("/api/plugins/crystalpilot/projects/open", json=body).status_code == 400
    assert calls == []


def test_hosted_management_keeps_curated_repair_but_not_installer_controls(hosted_plugin):
    client, _, _ = hosted_plugin
    row = client.get("/api/plugins").json()["plugins"][0]
    assert row["managed_by_host"]
    assert row["setup"]["actions"] == ["health", "repair", "shelx"]
    for action in ("install", "update", "disable", "uninstall", "configure", "enable"):
        assert client.post(f"/api/plugins/crystalpilot/manage/{action}").status_code == 403
    for action in ("health", "repair", "shelx"):
        assert client.post(f"/api/plugins/crystalpilot/manage/{action}").status_code == 200


def test_non_hosted_plugin_retains_local_folder_selection(hosted_plugin, monkeypatch, tmp_path):
    client, _, calls = hosted_plugin
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS")
    body = {"path": str(tmp_path / "local-data")}
    assert client.post("/api/plugins/crystalpilot/projects/open", json=body).json() == body
    assert calls == [body]
