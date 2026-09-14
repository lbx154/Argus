"""``/api/verticals``: auth, payload shape, 202/200/409/404, same-origin, hosted gate."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.verticals import _registry, store
from argus.webapi.protocol import API_CAPABILITIES
from argus.webapi.routes.verticals import register_vertical_routes
from argus.webapi.server import create_app
from tests.verticals import fake_release as fake


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS", raising=False)
    monkeypatch.delenv(store.PREINSTALL_ENV, raising=False)
    catalog = fake.build_release(tmp_path / "dist", [
        fake.spec("base_v"),
        fake.spec("child_v", requires=("base_v",), parents=("base_v",), purpose_zh="子垂直"),
    ])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    _registry.refresh_vertical_plugins()
    yield catalog
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


@pytest.fixture
def client():
    root = Path(os.environ["ARGUS_SKILL_HOME"])
    with TestClient(create_app(global_root=root, auth_token="store-test")) as web:
        web.headers["Authorization"] = "Bearer store-test"
        yield web, root


def _row(payload, name):
    return next(row for row in payload["verticals"] if row["name"] == name)


def test_capability_is_advertised() -> None:
    assert "verticals.store.v1" in API_CAPABILITIES


def test_listing_requires_auth_and_has_the_contract_shape(client) -> None:
    web, root = client
    response = web.get("/api/verticals")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"verticals", "catalog", "host"}
    assert set(payload["catalog"]) == {"source", "fetched_at", "release_tag", "error"}
    assert payload["catalog"]["release_tag"] == "vtest" and payload["catalog"]["error"] == ""
    assert payload["host"] == {"managed_by_host": False, "store_root": str(root / "verticals")}
    child = _row(payload, "child_v")
    assert child["kind"] == "available" and child["actions"] == ["install"] and child["purpose_zh"] == "子垂直"
    assert _row(payload, "research")["kind"] == "builtin"
    web.headers.pop("Authorization")
    assert web.get("/api/verticals").status_code == 401
    assert web.post("/api/verticals/child_v/manage/install").status_code == 401
    assert web.post("/api/verticals/catalog/refresh").status_code == 401
    assert web.get("/api/verticals/child_v/operation").status_code == 401


def test_install_is_a_202_job_then_toggling_is_200_and_uninstall_is_a_job(client) -> None:
    web, root = client
    response = web.post("/api/verticals/child_v/manage/install", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["name"] == "child_v" and body["action"] == "install"
    assert body["operation"]["status"] in {"running", "done"} and "identity" not in body["operation"]
    final = store.wait_for_operation("child_v", root)
    assert final["status"] == "done"
    polled = web.get("/api/verticals/child_v/operation")
    assert polled.status_code == 200 and polled.json()["status"] == "done"
    assert polled.json()["action"] == "install" and polled.json()["progress"] == 100

    listing = web.get("/api/verticals").json()
    assert _row(listing, "child_v")["kind"] == "installed"
    assert _row(listing, "base_v")["kind"] == "installed"  # pulled in through requires
    assert _row(listing, "child_v")["actions"] == ["disable", "uninstall"]

    disabled = web.post("/api/verticals/child_v/manage/disable")
    assert disabled.status_code == 200
    assert disabled.json() == {"name": "child_v", "action": "disable", "operation": None}
    assert _row(web.get("/api/verticals").json(), "child_v")["enabled"] is False
    assert web.post("/api/verticals/child_v/manage/enable").status_code == 200

    blocked = web.post("/api/verticals/base_v/manage/uninstall")
    assert blocked.status_code == 409 and "required by installed vertical" in blocked.json()["detail"]
    removed = web.post("/api/verticals/child_v/manage/uninstall", json={"force": True})
    assert removed.status_code == 202 and removed.json()["action"] == "uninstall"
    assert store.wait_for_operation("child_v", root)["status"] == "done"
    assert _row(web.get("/api/verticals").json(), "child_v")["kind"] == "available"


def test_unknown_names_and_actions_are_404_and_refusals_409(client) -> None:
    web, _ = client
    assert web.post("/api/verticals/ghost_v/manage/install").status_code == 404
    assert web.post("/api/verticals/child_v/manage/enable").status_code == 404  # not installed
    assert web.post("/api/verticals/child_v/manage/explode").status_code == 404
    assert web.get("/api/verticals/child_v/operation").status_code == 404
    assert web.post("/api/verticals/research/manage/install").status_code == 409
    bad_body = web.post("/api/verticals/child_v/manage/install", json={"force": "yes"})
    assert bad_body.status_code == 409
    assert web.post("/api/verticals/child_v/manage/install", json={"other": 1}).status_code == 409


def test_writes_are_same_origin_only(client) -> None:
    web, _ = client
    headers = {"Origin": "https://attacker.example", "Host": "testserver"}
    assert web.post("/api/verticals/child_v/manage/install", headers=headers).status_code == 403
    assert web.post("/api/verticals/catalog/refresh", headers=headers).status_code == 403
    same = {"Origin": "http://testserver", "Host": "testserver"}
    assert web.post("/api/verticals/catalog/refresh", headers=same).status_code == 200


def test_refresh_forces_a_fetch_and_reports_a_broken_catalog_in_the_payload(client, _isolated_store) -> None:
    web, _ = client
    catalog = _isolated_store
    assert web.get("/api/verticals").json()["catalog"]["error"] == ""
    catalog.write_text("{broken", encoding="utf-8")
    refreshed = web.post("/api/verticals/catalog/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert "not valid JSON" in body["catalog"]["error"]
    assert _row(body, "child_v")["kind"] == "available"  # the stale cache still lists it


def test_hosted_trial_refuses_installation_but_allows_toggling(monkeypatch) -> None:
    root = Path(os.environ["ARGUS_SKILL_HOME"])
    store.install("base_v", root, wait=True)
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    app = FastAPI()
    register_vertical_routes(app, SimpleNamespace(global_root=root, require_auth=lambda: None, token=None))
    web = TestClient(app)
    listing = web.get("/api/verticals").json()
    assert listing["host"]["managed_by_host"] is True
    assert _row(listing, "base_v")["actions"] == ["disable"]
    for action in ("install", "update", "uninstall"):
        assert web.post(f"/api/verticals/base_v/manage/{action}").status_code == 403
    assert web.post("/api/verticals/base_v/manage/disable").status_code == 200
    assert web.post("/api/verticals/base_v/manage/enable").status_code == 200


def test_startup_prepares_declared_verticals_on_a_thread(monkeypatch) -> None:
    root = Path(os.environ["ARGUS_SKILL_HOME"])
    monkeypatch.setenv(store.PREINSTALL_ENV, "child_v")
    with TestClient(create_app(global_root=root)) as web:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and set(store.installed(root)) != {"base_v", "child_v"}:
            time.sleep(0.1)  # the preinstall thread starts the job after startup returns
        assert set(store.installed(root)) == {"base_v", "child_v"}
        assert _row(web.get("/api/verticals").json(), "child_v")["kind"] == "installed"
