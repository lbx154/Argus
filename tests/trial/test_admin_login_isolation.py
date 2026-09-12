"""The data administrator and ordinary invitation workspace are independent sessions."""

import json
import time

import httpx
from fastapi.testclient import TestClient

from argus_skill.trial import web_portal as portal
from tests.trial.test_web_portal import ORIGIN, Chunks, client_for, login
from tests.trial.test_web_portal import provisioned as provisioned


def admin_login(client, config, *, readonly=False):
    response = client.post("/admin/login", headers={"Origin": ORIGIN}, json={
        "admin_login_token": config["admin_login_token"], "readonly": readonly,
    })
    assert response.status_code == 200
    assert response.json() == {"redirect": "/admin/data"}
    return response


def test_admin_login_page_has_working_nonce_and_never_discloses_configured_credentials(provisioned):
    config, _, _ = provisioned
    with client_for(provisioned) as client:
        for path in ("/admin", "/admin/data"):
            response = client.get(path)
            assert response.status_code == 303
            assert response.headers["location"] == "/admin/login"
        page = client.get("/admin/login")
        assert page.status_code == 200
        assert 'id="admin-login"' in page.text and 'type="password"' in page.text
        nonce = page.headers["content-security-policy"].split("'nonce-", 1)[1].split("'", 1)[0]
        assert f'<script nonce="{nonce}">' in page.text
        for value in (config["admin_login_token"], config["admin"]["token"]):
            assert value not in page.text
        assert client.post("/admin/login", json={"admin_login_token": config["admin_login_token"]}).status_code == 403
        assert client.post("/admin/login", headers={"Origin": ORIGIN}, json={
            "admin_login_token": "incorrect-key",
        }).status_code == 401
        response = admin_login(client, config)
        assert portal.COOKIE not in response.cookies
        assert "Path=/admin" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/admin/login").headers["location"] == "/admin/data"


def test_trial11_reuses_original_endpoint_and_sessions_do_not_overwrite_each_other(provisioned):
    config, vault, _ = provisioned
    original_backend = config.pop("admin")
    config["tenants"]["trial-11"] = original_backend
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b'{"ok":true}']), headers={"content-type": "application/json"})

    with client_for(provisioned, upstream) as client:
        trial = login(client, vault, "trial-11")
        trial_cookie = trial.cookies[portal.COOKIE]
        assert client.get("/invite/status").json()["key_id"] == "trial-11"
        assert client.get("/admin/status").status_code == 401
        response = admin_login(client, config)
        administrator_cookie = response.cookies[portal.ADMIN_COOKIE]
        assert client.cookies.get(portal.COOKIE) == trial_cookie
        assert client.get("/admin/status").json()["role"] == "admin"
        assert client.get("/invite/status").json()["role"] == "trial"
        assert client.get("/api/projects").status_code == 200
        assert calls[-1].url.host == "host.docker.internal"
        assert calls[-1].headers["authorization"] == "Bearer " + original_backend["token"]
        assert calls[-1].extensions["argus_backend"] == "trial-11"
        assert config["admin_login_token"] not in str(calls[-1].headers)
        login(client, vault, "trial-01")
        assert client.cookies.get(portal.ADMIN_COOKIE) == administrator_cookie
        assert client.get("/invite/status").json()["key_id"] == "trial-01"
        assert client.get("/admin/status").status_code == 200
        assert client.post("/invite/logout", headers={"Origin": ORIGIN}).status_code == 200
        assert client.get("/invite/status").status_code == 401
        assert client.get("/admin/status").status_code == 200
        assert client.get("/api/projects").status_code == 401
        login(client, vault, "trial-11")
        assert client.post("/admin/logout", headers={"Origin": ORIGIN}).status_code == 200
        assert client.get("/admin/status").status_code == 401
        assert client.get("/invite/status").json()["key_id"] == "trial-11"
        assert client.get("/api/projects").status_code == 200


def test_admin_cookie_and_old_admin_frontend_cookies_cannot_be_used_as_trial11(provisioned):
    config, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        response = admin_login(client, config)
        value = response.cookies[portal.ADMIN_COOKIE]
        assert vault.cipher.decrypt(value.encode()).startswith(portal.ADMIN_COOKIE_DOMAIN)
        assert client.get("/api/projects").status_code == 401
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, value)
        assert client.get("/invite/status").status_code == 401
        old_identity = {"tenant": "admin", "role": "admin", "readonly": False,
                        "exp": int(time.time()) + portal.SESSION_SECONDS,
                        "admin_binding": client.app.state.admin_binding}
        old_cookie = vault.cipher.encrypt(portal.COOKIE_DOMAIN + json.dumps(old_identity).encode()).decode()
        client.cookies.set(portal.COOKIE, old_cookie)
        assert client.get("/api/projects").status_code == 401
        assert client.get("/invite/status").status_code == 401
        assert not calls


def test_legacy_ten_tenant_config_runs_without_issuing_trial11(provisioned):
    config, _, store = provisioned
    config["tenants"].pop("trial-11")
    with store.transaction() as db:
        db.execute("DELETE FROM trial_keys WHERE key_id='trial-11'")
    settings = portal.Settings.load(config)
    assert set(settings.tenants) == portal.LEGACY_TENANT_IDS
    with client_for(provisioned) as client:
        assert client.get("/invite").status_code == 200
        assert client.app.state.store.key_limit == 10
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM trial_keys").fetchone()[0] == 10


def test_new_config_can_drop_admin_backend_or_keep_exact_trial11_migration_alias(provisioned):
    config, _, _ = provisioned
    config["tenants"]["trial-11"] = dict(config["admin"])
    settings = portal.Settings.load(config)
    assert settings.tenants["trial-11"] == settings.admin
    config.pop("admin")
    settings = portal.Settings.load(config)
    assert settings.admin is None
    assert len(settings.tenants) == 11


def test_admin_data_routes_use_only_admin_cookie_and_ignore_trial_consent_state(provisioned, tmp_path):
    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    analytics = Analytics(tmp_path / "research", {
        "trial-11": {"data_dir": tmp_path / "trial-11", "internal_test": True},
    }, config["state_dir"] + "/usage.sqlite3", tmp_path / "compute.sqlite3")
    app = portal.create_app(config, analytics=analytics, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Chunks([b"workspace"])),
    ))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        stale_trial = {"tenant": "trial-11", "role": "trial", "readonly": False,
                       "exp": int(time.time()) + portal.SESSION_SECONDS}
        client.cookies.set(portal.COOKIE, vault.cipher.encrypt(
            portal.COOKIE_DOMAIN + json.dumps(stale_trial).encode(),
        ).decode())
        assert client.get("/api/projects").status_code == 401
        assert client.get("/admin/login").status_code == 200
        admin_login(client, config)
        response = client.get("/admin/data")
        assert response.status_code == 200 and "数据工作台" in response.text
        assert client.get("/admin/data/app.js").status_code == 200
        assert client.get("/admin/status").json()["role"] == "admin"
        assert client.get("/api/projects").status_code == 401
