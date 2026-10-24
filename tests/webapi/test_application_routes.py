import sys
from types import ModuleType

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from argus.webapi.server import create_app


def test_application_routes_share_the_selected_root_and_authorization(tmp_path, monkeypatch):
    module = ModuleType("test_application_routes_extension")

    def register(app, ctx):
        assert ctx.roots == [tmp_path.resolve()]
        ctx.api_meta["capabilities"].append("fixture.application.v1")

        @app.post("/api/application-fixture", dependencies=[Depends(ctx.require_auth)])
        def action():
            return {"accepted": True}

    module.register = register
    monkeypatch.setitem(sys.modules, module.__name__, module)
    name = f"{module.__name__}:register"
    monkeypatch.setenv("ARGUS_APPLICATION_ROUTES", name)
    token = "test-application-only"
    client = TestClient(create_app(global_root=tmp_path, auth_token=token))
    assert client.post("/api/application-fixture").status_code == 401
    headers = {"Authorization": "Bearer " + token}
    assert client.post("/api/application-fixture", headers=headers).json() == {"accepted": True}
    meta = client.get("/api/meta", headers=headers).json()
    assert meta["runtime"]["application_routes"] == name
    assert "fixture.application.v1" in meta["capabilities"]


def test_misconfigured_application_routes_fail_explicitly(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_APPLICATION_ROUTES", "not-a-registrar")
    with pytest.raises(ValueError, match="module:registrar"):
        create_app(global_root=tmp_path)
