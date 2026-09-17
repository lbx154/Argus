"""The hosted portal lets a visitor refresh and toggle verticals, never install them."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.trial import web_portal as portal
from argus.verticals import _registry, store
from argus.webapi.routes.verticals import register_vertical_routes
from tests.verticals import fake_release as fake


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/api/verticals/catalog/refresh", True),
        ("/api/verticals/quant/manage/enable", True),
        ("/api/verticals/digital_circuit_benchmark/manage/disable", True),
        ("/api/verticals/quant/manage/install", False),
        ("/api/verticals/quant/manage/update", False),
        ("/api/verticals/quant/manage/uninstall", False),
        ("/api/verticals/Quant/manage/enable", False),
        ("/api/verticals/quant/manage/enable/", False),
        ("/api/verticals/../verticals/quant/manage/enable", False),
    ],
)
def test_portal_permits_only_refresh_and_toggling(path: str, allowed: bool) -> None:
    assert portal.permitted(path, "POST") is allowed
    assert portal.permitted("/api/verticals", "GET") is True
    assert portal.permitted("/api/verticals/quant/operation", "GET") is True
    assert portal.permitted("/api/verticals/quant/manage/enable", "DELETE") is False


def test_hosted_registrar_matches_the_portal_gate(tmp_path, monkeypatch) -> None:
    """What the portal forwards, the route accepts; what it drops, the route refuses too."""
    root = Path(os.environ["ARGUS_SKILL_HOME"])
    catalog = fake.build_release(tmp_path / "dist", [fake.spec("solo_v")])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS", raising=False)
    _registry.refresh_vertical_plugins()
    store.install("solo_v", root, wait=True)
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    app = FastAPI()
    register_vertical_routes(app, SimpleNamespace(global_root=root, require_auth=lambda: None, token=None))
    web = TestClient(app)
    try:
        for action in ("enable", "disable"):
            path = f"/api/verticals/solo_v/manage/{action}"
            assert portal.permitted(path, "POST") and web.post(path).status_code == 200
        assert portal.permitted("/api/verticals/catalog/refresh", "POST")
        assert web.post("/api/verticals/catalog/refresh").status_code == 200
        for action in ("install", "update", "uninstall"):
            path = f"/api/verticals/solo_v/manage/{action}"
            assert not portal.permitted(path, "POST")
            assert web.post(path).status_code == 403
        assert web.get("/api/verticals").json()["host"]["managed_by_host"] is True
    finally:
        _registry.refresh_vertical_plugins()
        store._purge_modules(["argus_verticals"])
