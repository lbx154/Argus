from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from argus_skill.trial import web_portal as portal
from argus_skill.trial.secrets import Vault
from argus_skill.trial.store import Store
from argus_skill.trial.training_data import COMBINED_NOTICE_VERSION, NOTICE_VERSION

ORIGIN = "https://portal.test"


@pytest.fixture
def provisioned(tmp_path):
    state = tmp_path / "meter"
    state.mkdir()
    key = tmp_path / "master.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    vault = Vault(key, state / "github-token.enc")
    store = Store(state / "usage.sqlite3")
    for tenant in portal.TENANT_IDS:
        store.issue(tenant, vault.credential(tenant))
    config = {
        "state_dir": str(state), "key_file": str(key),
        "tenants": {
            tenant: {"url": f"http://{tenant}:8080", "token": f"internal-{tenant}-secret"}
            for tenant in sorted(portal.TENANT_IDS)
        },
        "admin": {"url": "http://host.docker.internal:8896", "token": "private-admin-token"},
        "admin_login_token": "separate-private-administrator-login",
    }
    return config, vault, store


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_proxy_observation_keeps_streaming_and_records_real_completion():
    from fastapi import FastAPI

    class Capture:
        def __init__(self):
            self.chunks = []
            self.finished = []

        def feed(self, data):
            self.chunks.append(data)

        def finish(self, *args):
            self.finished.append(args)

    observed = Capture()
    stream = Chunks([b"first", b"second"])
    upstream = httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    app = FastAPI()

    @app.get("/")
    def endpoint():
        return portal.ProxyResponse(upstream, {"content-type": "text/event-stream"}, observed)

    with TestClient(app) as client:
        assert client.get("/").content == b"firstsecond"
    assert observed.chunks == [b"first", b"second"]
    assert observed.finished == [(200, True, "text/event-stream")]
    assert stream.closed


@contextmanager
def client_for(provisioned, handler=None):
    config, _, _ = provisioned
    if handler is None:
        handler = lambda request: httpx.Response(200, stream=Chunks([b"workspace"]))
    app = portal.create_app(config, transport=httpx.MockTransport(handler))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client


def login(client, vault, tenant="trial-01", readonly=False):
    response = client.post(
        "/invite/login", headers={"Origin": ORIGIN},
        json={"code": vault.credential(tenant), "readonly": readonly},
    )
    assert response.status_code == 200
    return response


def test_login_page_and_unauthenticated_routes(provisioned):
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        response = client.get("/")
        assert response.status_code == 303 and response.headers["location"] == "/invite"
        page = client.get("/invite")
        assert "专属邀请码" in page.text and "1000万 tokens" in page.text
        assert '<a href="/invite/status">' in page.text
        assert '<a href="/invite/compute">GPU任务队列（进入后查看）</a>' in page.text
        assert "localStorage" not in page.text and "sessionStorage" not in page.text
        assert "autocomplete=\"off\"" in page.text
        assert "script-src 'nonce-" in page.headers["content-security-policy"]
        nonce = page.headers["content-security-policy"].split("'nonce-", 1)[1].split("'", 1)[0]
        assert f"<script nonce='{nonce}'>" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["x-frame-options"] == "DENY"
        for path in ("/api/projects", "/assets/app.js", "/invite/status", "/openapi.json",
                     "/compute", "/compute/jobs"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        response = client.get("/api/projects", headers={"Accept": "text/html"})
        assert response.status_code == 401
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/projects/p/stream", headers={"Origin": ORIGIN}):
                pass
        assert exc.value.code == 4401
        assert not calls


def test_analytics_requires_explicit_versioned_notice_and_guards_old_sessions(provisioned, tmp_path):
    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    analytics = Analytics(
        tmp_path / "research",
        {"trial-01": {"data_dir": tmp_path / "tenant", "internal_test": True}},
        tmp_path / "unused-meter.sqlite3", tmp_path / "unused-compute.sqlite3",
    )
    analytics.record_consent("trial-01", "operator-analytics-v1")
    app = portal.create_app(
        config, analytics=analytics,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, stream=Chunks([b'{"ok":true}']), headers={"content-type": "application/json"},
        )),
    )
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert 'id="data-notice"' in client.get("/invite").text
        assert analytics.notice_version == COMBINED_NOTICE_VERSION
        with analytics._db() as db:
            assert db.execute("SELECT count(*) FROM journey_events").fetchone()[0] == 0
        body = {"code": vault.credential("trial-01")}
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 403
        body.update(data_notice_accepted=True, notice_version="operator-analytics-v1")
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 403
        body["notice_version"] = analytics.notice_version
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        permission = client.get("/trial/data-permissions").json()
        assert permission["notice_version"] == NOTICE_VERSION
        assert permission["internal_training"] is True
        assert permission["external_sharing"] is False
        assert client.get("/api/projects").status_code == 200
        with analytics._db() as db:
            row = db.execute("SELECT tenant_id,method,path,status FROM events "
                             "WHERE path='/api/projects' ORDER BY id LIMIT 1").fetchone()
            assert tuple(row) == ("trial-01", "GET", "/api/projects", 200)
        assert client.get("/admin/api/dashboard").status_code == 403
        with analytics._db() as db:
            db.execute("DELETE FROM consents WHERE version=?", (analytics.notice_version,))
        assert client.get("/api/projects").status_code == 401
        assert 'id="data-notice"' in client.get("/invite").text
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/api/projects/p/stream", headers={"Origin": ORIGIN}):
                pass
        assert error.value.code == 4401


def test_research_lifespan_replay_feedback_export_deletion_and_restart(provisioned, tmp_path, monkeypatch):
    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    analytics = Analytics(
        tmp_path / "research",
        {tenant: {"data_dir": tmp_path / tenant, "internal_test": True}
         for tenant in ("trial-01", "trial-02")},
        tmp_path / "unused-meter.sqlite3", tmp_path / "unused-compute.sqlite3",
    )
    source = tmp_path / "trial-01/home/.argus-skill/projects/s-research"
    source.mkdir(parents=True)
    (source / "session.json").write_text(json.dumps({"id": "s-research", "display_name": "Synthetic"}))
    events = source / "events.jsonl"
    events.write_text('{"type":"ui.operator","text":"PRECONSENT_MUST_NOT_COPY"}\n')
    monkeypatch.setattr(portal, "RESEARCH_POLL_SECONDS", 0.02)
    handler = httpx.MockTransport(lambda request: httpx.Response(
        200, stream=Chunks([b'{"kind":"task","item_id":"task-one","status":"queued"}']),
        headers={"content-type": "application/json"},
    ))
    app = portal.create_app(config, analytics=analytics, transport=handler)
    body = {"code": vault.credential("trial-01"), "data_notice_accepted": True,
            "notice_version": COMBINED_NOTICE_VERSION}
    tester_base = "/research/api/projects/s-research"
    admin_base = "/admin/api/research/trial-01/s-research"
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get("/research/api/projects").status_code == 401
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        tester_cookie = client.cookies.get(portal.COOKIE)
        assert client.get("/invite/research").status_code == 200
        assert client.post("/api/projects/s-research/message", headers={"Origin": ORIGIN}, json={
            "text": "Make a synthetic result", "attachments": [{"contents": "ATTACHMENT_MUST_NOT_COPY"}],
        }).status_code == 200
        client.cookies.clear()  # No tester browser session or browser polling remains.
        with events.open("a") as stream:
            stream.write('{"type":"ui.argus","text":"AFTER_BROWSER_CLOSE","item_id":"task-one"}\n')
            stream.write('not-json\n')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with analytics._db() as db:
                found = db.execute(
                    "SELECT 1 FROM journey_events WHERE record LIKE '%AFTER_BROWSER_CLOSE%'",
                ).fetchone()
            if found:
                break
            time.sleep(0.02)
        assert found, "Persistent lifespan worker must collect without browser requests"
        client.get("/", params={"token": config["admin_login_token"]})
        replay = client.get(admin_base + "/replay").json()
        assert replay["completeness"]["complete"] is False
        assert replay["completeness"]["gap_events"] >= 2
        assert len({event["id"] for event in replay["events"]}) == len(replay["events"])
        assert [event["sequence"] for event in replay["events"]] == sorted(
            event["sequence"] for event in replay["events"])
        assert "PRECONSENT_MUST_NOT_COPY" not in json.dumps(replay)
        with analytics._db() as db:
            assert "ATTACHMENT_MUST_NOT_COPY" not in str([tuple(row) for row in db.execute("SELECT input FROM interactions")])
        saved_events = replay["events"]
        deviation = next(event["id"] for event in saved_events if event["kind"] == "ui.argus")
        assert client.post(admin_base + "/annotation", headers={"Origin": ORIGIN}, json={
            "user_goal": "Compare the synthetic results", "first_deviation_event_id": deviation,
            "satisfaction": "no", "note": "Human judgement, not inferred",
        }).status_code == 200
        exported = client.get(admin_base + "/export")
        assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
        assert exported.json()["annotation"]["satisfaction"] == "no"
        audit = client.get("/admin/api/research/audit").json()["events"]
        assert any(row["action"] == "replay.export" for row in audit)
        assert any(row["action"] == "admin.export" and row["outcome"] == "allowed" for row in audit)
        assert "Compare the synthetic" not in json.dumps(audit)
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, tester_cookie)
        assert client.get(admin_base + "/replay").status_code == 403
        assert client.post(tester_base + "/feedback", headers={"Origin": ORIGIN}, json={
            "verdict": "met_need", "task_id": "task-one", "note": "Explicit tester evaluation",
        }).json()["inferred_success"] is False
        assert client.get(tester_base + "/feedback").json()["feedback"][0]["verdict"] == "met_need"
    app = portal.create_app(config, analytics=analytics, transport=httpx.MockTransport(handler.handler))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.cookies.set(portal.COOKIE, tester_cookie)
        assert client.get(tester_base + "/replay").json()["events"] == saved_events
        assert client.delete(tester_base).status_code == 403
        deleted = client.delete(tester_base, headers={"Origin": ORIGIN})
        assert deleted.status_code == 200 and deleted.json()["runtime_files_deleted"] is False
        assert deleted.json()["future_project_capture_disabled"] is True
        assert client.get(tester_base + "/replay").status_code == 410
        assert client.post(tester_base + "/feedback", headers={"Origin": ORIGIN},
                           json={"verdict": "met_need"}).status_code == 410
        assert events.is_file() and "AFTER_BROWSER_CLOSE" in events.read_text()
        assert client.post("/api/projects/s-research/message", headers={"Origin": ORIGIN},
                           json={"text": "AFTER_DELETION_MUST_NOT_COPY"}).status_code == 200
        time.sleep(0.1)
        with analytics._db() as db:
            for table in ("journey_events", "interactions", "research_annotations", "research_feedback"):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        future = time.time() + 31 * 86400
        analytics.clock = lambda: future
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with analytics._db() as db:
                remaining = db.execute("SELECT count(*) FROM research_audit").fetchone()[0]
            if not remaining:
                break
            time.sleep(0.02)
        assert remaining == 0, "The background worker must expire research audit without a browser"


def test_research_invitation_disable_expiry_and_readonly(provisioned, tmp_path):
    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    analytics = Analytics(
        tmp_path / "research", {"trial-01": {"data_dir": tmp_path / "tenant", "internal_test": True}},
        tmp_path / "unused-meter.sqlite3", tmp_path / "unused-compute.sqlite3",
    )
    app = portal.create_app(config, analytics=analytics,
                            transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    body = {"code": vault.credential("trial-01"), "readonly": True,
            "data_notice_accepted": True, "notice_version": COMBINED_NOTICE_VERSION}
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        cookie = client.cookies.get(portal.COOKIE)
        assert client.delete("/research/api/projects/s-any", headers={"Origin": ORIGIN}).status_code == 403
        client.get("/", params={"token": config["admin_login_token"]})
        path = "/admin/api/research/testers/trial-01/access"
        assert client.post(path, headers={"Origin": ORIGIN},
                           json={"enabled": False, "expires_at": None}).status_code == 200
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, cookie)
        assert client.get("/research/api/projects").status_code == 401
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 401
        client.get("/", params={"token": config["admin_login_token"]})
        assert client.post(path, headers={"Origin": ORIGIN},
                           json={"enabled": True, "expires_at": time.time() - 10}).status_code == 200
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, cookie)
        assert client.get("/api/projects").status_code == 401
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 401


def test_team_training_and_optional_sharing_use_real_registered_routes(provisioned, tmp_path):
    import io
    import zipfile
    from pathlib import Path

    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    source = tmp_path / "tenant/home/.argus-skill/projects/s-training"
    source.mkdir(parents=True)
    (source / "session.json").write_text(json.dumps({"id": "s-training", "display_name": "Synthetic training"}))
    (source / "events.jsonl").touch()
    analytics = Analytics(
        tmp_path / "research", {"trial-01": {"data_dir": tmp_path / "tenant", "internal_test": False}},
        Path(config["state_dir"]) / "usage.sqlite3", tmp_path / "unused-compute.sqlite3",
    )
    app = portal.create_app(config, analytics=analytics, transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=Chunks([b'{"kind":"chat","reply":"Hello from a visible synthetic response"}']),
            headers={"content-type": "application/json"},
        ),
    ))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        body = {"code": vault.credential("trial-01"), "data_notice_accepted": True,
                "notice_version": COMBINED_NOTICE_VERSION}
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        tester_cookie = client.cookies.get(portal.COOKIE)
        permissions = client.get("/trial/data-permissions").json()
        assert permissions["internal_training"] is True and permissions["external_sharing"] is False
        permission_body = {"notice_version": permissions["notice_version"],
                           "internal_training": True, "external_sharing": False}
        assert client.put("/trial/data-permissions", json=permission_body).status_code == 403
        assert client.put("/trial/data-permissions", headers={"Origin": ORIGIN},
                          json=permission_body).status_code == 200
        assert client.post("/api/projects/s-training/message", headers={"Origin": ORIGIN},
                           json={"text": "Hello, please greet me"}).status_code == 200
        app.state.journal.poll()
        client.get("/", params={"token": config["admin_login_token"]})
        external = client.get("/admin/api/training/preview?purpose=external_sharing").json()
        assert external["counts"]["sft"] == 0
        assert not any(project["eligible"] for project in external["projects"])
        internal = client.get("/admin/api/training/preview?purpose=internal_training").json()
        assert internal["counts"]["sft"] == 0
        candidate = internal["candidates"][0]
        assert candidate["sample"]["messages"][0]["content"] == "Hello, please greet me"
        response = client.post("/admin/api/training/export", headers={"Origin": ORIGIN}, json={
            "purpose": "internal_training", "projects": [{"tenant_id": "trial-01", "sid": "s-training"}],
            "review": {"content_approved": True, "rights_reviewed": False,
                       "approved_event_ids": [candidate["event_id"]]},
        })
        assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["counts"]["sft"] == 1 and manifest["counts"]["tool_sft"] == 0
            assert manifest["purpose"] == "internal_training"
        audit = client.get("/admin/api/research/audit").json()["events"]
        assert any(row["action"] == "training.export.unspecified" and row["outcome"] == "completed" for row in audit)
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, tester_cookie)
        permission_body["internal_training"] = False
        assert client.put("/trial/data-permissions", headers={"Origin": ORIGIN},
                          json=permission_body).json()["internal_training"] is False


@pytest.mark.parametrize("origin", [None, "https://evil.test", "null", ORIGIN + ".evil.test"])
def test_login_requires_exact_origin(provisioned, origin):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        headers = {} if origin is None else {"Origin": origin}
        response = client.post("/invite/login", headers=headers, json={"code": vault.credential("trial-01")})
        assert response.status_code == 403
        assert portal.COOKIE not in response.cookies


@pytest.mark.parametrize("configured", [False, True])
def test_pinned_public_origin_handles_tls_termination_without_forwarded_headers(provisioned, configured):
    config, vault, _ = provisioned
    if configured:
        config["public_origin"] = "https://public.example.test"
    app = portal.create_app(config)
    # The tunnel preserves Host while its private connection uses HTTP.
    with TestClient(app, base_url="http://public.example.test", follow_redirects=False) as client:
        response = client.post("/invite/login", headers={"Origin": "https://public.example.test"},
                               json={"code": vault.credential("trial-01")})
        assert response.status_code == (200 if configured else 403)
        if configured:
            assert portal.COOKIE in response.cookies
        else:
            spoofed = client.post("/invite/login", json={"code": vault.credential("trial-01")}, headers={
                "Origin": "https://public.example.test", "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "public.example.test",
            })
            assert spoofed.status_code == 403
    # Native local same-origin access still works without a public-origin alias.
    with TestClient(app, base_url="http://127.0.0.1:8899", follow_redirects=False) as client:
        assert client.post("/invite/login", headers={"Origin": "http://127.0.0.1:8899"},
                           json={"code": vault.credential("trial-01")}).status_code == 200


@pytest.mark.parametrize("origin,host", [
    ("https://other.example.test", "public.example.test"),
    ("https://public.example.test.evil.test", "public.example.test"),
    ("https://public.example.test", "other.example.test"),
    ("https://public.example.test", "127.0.0.1:8899"),
    ("https://public.example.test:8443", "public.example.test"),
    ("null", "public.example.test"),
])
def test_public_origin_is_exact_and_bound_to_actual_host(provisioned, origin, host):
    config, vault, _ = provisioned
    config["public_origin"] = "https://public.example.test"
    with TestClient(portal.create_app(config), base_url="http://" + host) as client:
        response = client.post("/invite/login", json={"code": vault.credential("trial-01")}, headers={
            "Origin": origin, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "public.example.test",
        })
        assert response.status_code == 403
        assert portal.COOKIE not in response.cookies


@pytest.mark.parametrize("public_origin", [
    "http://public.example.test", "https://public.example.test/", "https://public.example.test/path",
    "https://public.example.test?", "https://public.example.test#fragment", "https://user:secret@example.invalid",
    "https://*.example.test", "https://public.example.test:", "https://public.example.test:0",
    "https://public.example.test:65536", "https://public..test", " https://public.example.test", 123,
])
def test_public_origin_configuration_accepts_only_https_origins(provisioned, public_origin):
    config, _, _ = provisioned
    with pytest.raises(ValueError, match="public_origin"):
        portal.Settings.load({**config, "public_origin": public_origin})


def test_public_origin_default_port_and_websocket_scheme(provisioned):
    from starlette.websockets import WebSocket

    config, _, _ = provisioned
    settings = portal.Settings.load({**config, "public_origin": "https://PUBLIC.example.test:443"})
    assert settings.public_origin == "https://public.example.test"
    app = portal.create_app(settings)
    scope = {"type": "websocket", "scheme": "ws", "server": ("public.example.test", 80),
             "path": "/api/projects/synthetic/stream", "query_string": b"", "app": app,
             "headers": [(b"host", b"public.example.test"), (b"origin", b"https://public.example.test")]}

    async def unused(*args):
        raise AssertionError("No network access is needed for origin validation")

    assert portal.same_origin(WebSocket(scope, unused, unused))
    scope["headers"][-1] = (b"origin", b"https://evil.test")
    assert not portal.same_origin(WebSocket(scope, unused, unused))


def test_cookie_encryption_flags_and_no_secret_exposure(provisioned):
    config, vault, _ = provisioned
    with client_for(provisioned) as client:
        result = login(client, vault)
        cookie = result.cookies[portal.COOKIE]
        payload = vault.cipher.decrypt(cookie.encode())
        assert payload.startswith(portal.COOKIE_DOMAIN)
        identity = json.loads(payload[len(portal.COOKIE_DOMAIN):])
        assert identity["tenant"] == "trial-01" and identity["role"] == "trial"
        assert identity["readonly"] is False
        assert 604790 < identity["exp"] - time.time() <= 604800
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=604800"):
            assert flag in result.headers["set-cookie"]
        for secret in (vault.credential("trial-01"), config["admin"]["token"], config["admin_login_token"],
                       config["tenants"]["trial-01"]["token"]):
            assert secret not in result.text + str(result.headers) + payload.decode()
        assert result.json() == {"redirect": "/invite"}


@pytest.mark.parametrize("payload", [
    b"not a session",
    portal.COOKIE_DOMAIN + b"{}",
    portal.COOKIE_DOMAIN + b"null",
    portal.COOKIE_DOMAIN + b'{"tenant":"trial-99","role":"trial","readonly":false,"exp":9999999999}',
])
def test_domain_separated_or_malformed_encrypted_cookie_fails_closed(provisioned, payload):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        client.cookies.set(portal.COOKIE, vault.cipher.encrypt(payload).decode())
        assert client.get("/api/projects").status_code == 401


@pytest.mark.parametrize("mode", ["garbage", "forged", "expired", "future", "bad-role", "bad-readonly"])
def test_invalid_cookies_fail_closed(provisioned, mode):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        identity = {"tenant": "trial-01", "role": "trial", "readonly": False,
                    "exp": int(time.time()) + 600}
        if mode == "expired":
            identity["exp"] = int(time.time()) - 1
        elif mode == "future":
            identity["exp"] = int(time.time()) + 2 * portal.SESSION_SECONDS
        elif mode == "bad-role":
            identity["role"] = "admin"
        elif mode == "bad-readonly":
            identity["readonly"] = "false"
        cookie = vault.cipher.encrypt(portal.COOKIE_DOMAIN + json.dumps(identity).encode()).decode()
        if mode == "garbage":
            cookie = "invalid"
        elif mode == "forged":
            cookie = Fernet(Fernet.generate_key()).encrypt(
                portal.COOKIE_DOMAIN + json.dumps(identity).encode()
            ).decode()
        client.cookies.set(portal.COOKIE, cookie)
        assert client.get("/invite/status").status_code == 401
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/projects/p/stream", headers={"Origin": ORIGIN}):
                pass
        assert exc.value.code == 4401


@pytest.mark.parametrize("body", [
    {"code": "bad"}, {"code": "argus_trial_" + "0" * 64},
    {"code": ["bad"]}, {"code": "bad", "tenant": "trial-02"},
    {"code": "bad", "readonly": "false"}, [],
])
def test_bad_login_has_no_reflected_input(provisioned, body):
    with client_for(provisioned) as client:
        result = client.post("/invite/login", json=body, headers={"Origin": ORIGIN})
        assert result.status_code in {400, 401}
        assert "argus_trial_" not in result.text
        assert portal.COOKIE not in result.cookies


def test_tenant_routing_and_header_isolation(provisioned):
    config, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"ok"]), headers={
            "Set-Cookie": "upstream-private-cookie=secret",
            "Authorization": "upstream-secret",
            "X-Internal": "do-not-forward",
            "Connection": "etag",
            "ETag": "private-hop",
            "Content-Type": "text/plain",
        })

    with client_for(provisioned, upstream) as client:
        login(client, vault)
        response = client.post(
            "/api/projects/trial-02-project/message?token=browser-secret&part=one&part=two",
            headers={
                "Origin": ORIGIN, "Authorization": "Bearer browser-secret",
                "X-Tenant": "trial-02", "X-Forwarded-Host": "evil.test",
                "X-Forwarded-Proto": "http", "Forwarded": "host=evil.test",
                "X-API-Key": "browser-secret", "Connection": "accept-language",
                "Accept-Language": "secret-hop-value",
            }, json={"text": "hello"},
        )
        assert response.status_code == 200 and response.text == "ok"
        assert all(key not in response.headers for key in
                   ("set-cookie", "authorization", "x-internal", "etag", "connection"))
        first = calls[-1]
        assert first.url.host == "trial-01"
        assert first.url.path == "/api/projects/trial-02-project/message"
        assert first.url.query == b"part=one&part=two"
        assert first.headers["authorization"] == "Bearer " + config["tenants"]["trial-01"]["token"]
        assert first.headers["host"] == "trial-01:8080"
        for name in ("cookie", "x-api-key", "x-forwarded-host", "x-forwarded-proto",
                     "forwarded", "x-tenant", "origin", "accept-language"):
            assert name not in first.headers
        assert "browser-secret" not in str(first.headers) + str(first.url)
        login(client, vault, "trial-02")
        client.get("/api/projects/trial-01-project/snapshot")
        second = calls[-1]
        assert second.url.host == "trial-02"
        assert second.headers["authorization"] == "Bearer " + config["tenants"]["trial-02"]["token"]
        assert "cookie" not in second.headers
        assert second.url.path == "/api/projects/trial-01-project/snapshot"


def test_quota_isolation_persistence_and_no_recovery(provisioned, monkeypatch):
    _, vault, store = provisioned
    first = store.reserve("trial-01", 500)
    store.settle(first, 123)
    store.reserve("trial-02", 250)

    def forbidden_recover(*args):
        pytest.fail("Portal must not recover the gateway ledger")

    monkeypatch.setattr(Store, "recover", forbidden_recover)
    for _ in range(2):
        with client_for(provisioned) as client:
            login(client, vault)
            first_status = client.get("/invite/status").json()
            assert first_status["key_id"] == "trial-01"
            assert first_status["tokens_used"] == 123
            assert first_status["token_limit"] == 10_000_000
            assert first_status["tokens_remaining"] == 9_999_877
            assert "global_tpm_reserved" not in first_status
            assert "active_requests" not in first_status
            login(client, vault, "trial-02")
            assert client.get("/invite/status").json()["tokens_used"] == 250
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM trial_requests WHERE state='active'").fetchone()[0] == 1


def test_admin_exchange_strips_token_and_preserves_project(provisioned):
    config, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"admin workspace"]))

    with client_for(provisioned, upstream) as client:
        token = config["admin_login_token"]
        result = client.get("/", params={"token": token, "project": "demo", "kiosk": "1", "other": "x"})
        assert result.status_code == 303
        assert result.headers["location"] == "/?project=demo&kiosk=1"
        assert token not in result.text + str(result.headers)
        assert not calls
        assert client.get(result.headers["location"]).status_code == 200
        assert calls[-1].url.host == "host.docker.internal"
        assert calls[-1].headers["authorization"] == "Bearer " + config["admin"]["token"]
        assert token not in str(calls[-1].headers) + str(calls[-1].url)
        assert "token" not in str(calls[-1].url)
        status = client.get("/invite/status").json()
        assert status["role"] == "admin" and status["readonly"] is True
        assert "tokens_used" not in status
        assert client.post("/api/projects/demo/message", json={}, headers={"Origin": ORIGIN}).status_code == 403
        assert client.get("/", params={"token": vault.credential("trial-01")}).status_code == 401
        assert client.get("/", params={"token": "wrong"}).status_code == 401
        assert client.get("/", params={"token": token}, headers={"Origin": "https://evil.test"}).status_code == 403


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "OPTIONS", "TRACE"])
def test_kiosk_blocks_all_non_read_methods_even_without_kiosk_query(provisioned, method):
    _, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        result = login(client, vault, readonly=True)
        assert result.json()["redirect"] == "/invite"
        response = client.request(method, "/api/projects/p/message", headers={"Origin": ORIGIN})
        assert response.status_code == 403
        assert not calls


def test_kiosk_removes_get_prewarm_side_effect(provisioned):
    _, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"{}"]))

    with client_for(provisioned, upstream) as client:
        login(client, vault, readonly=True)
        assert client.get("/api/projects/p/snapshot?prewarm=true&compact=true").status_code == 200
        assert calls[-1].url.query == b"compact=true"


def test_csrf_and_logout(provisioned):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        login(client, vault)
        for path in ("/api/projects/p/message", "/invite/logout"):
            assert client.post(path, json={}).status_code == 403
            assert client.post(path, json={}, headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.get("/invite/logout").status_code == 405
        assert client.get("/invite/status").status_code == 200
        result = client.post("/invite/logout", headers={"Origin": ORIGIN})
        assert result.status_code == 200
        assert "Max-Age=0" in result.headers["set-cookie"]
        assert client.get("/invite/status").status_code == 401
        assert client.post("/invite/logout", headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize("path", [
    "/api/runtime/source-update", "/api/runtime/source-update/apply",
    "/api/system/doctor", "/api/system/resources", "/api/metrics", "/metrics",
    "/api/projects/p/config/set", "/api/projects/p/config/budget",
    "/api/projects/p/skills", "/api/projects/p/identity", "/api/projects/p/doctor",
    "/api/projects/p/workdir", "/api/projects/p/launch-cwd",
    "/api/projects/p/daemon/upgrade", "/api/projects/p/daemon/upgrade-schedule",
    "/api/projects/p/daemon/replace", "/api/projects/p/%2563onfig/set",
    "/api/projects/p//config/set", "/openapi.json", "/docs",
])
def test_runtime_control_surfaces_are_blocked(provisioned, path):
    _, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        login(client, vault)
        for method in ("GET", "POST"):
            assert client.request(method, path, headers={"Origin": ORIGIN}).status_code == 403
        assert not calls


def test_model_configuration_is_readable_without_allowing_changes(provisioned):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        login(client, vault)
        assert client.get("/api/projects/p/config").status_code == 200
        assert client.head("/api/projects/p/config").status_code == 200
        for path in ("/api/projects/p/config", "/api/projects/p/config/set"):
            assert client.post(path, headers={"Origin": ORIGIN}, json={
                "name": "ARGUS_SKILL_MAP_MODEL", "value": "other-model",
            }).status_code == 403


def test_hosted_frontend_is_authenticated_and_keeps_apis_tenant_scoped(provisioned, tmp_path):
    config, vault, _ = provisioned
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text('<html><script>window.theme=1</script></html>')
    (frontend / "assets/app.js").write_text('window.app=1')
    config["frontend_dir"] = str(frontend)
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"old-container-response"]))

    with client_for(provisioned, upstream) as client:
        assert client.get("/").status_code == 303
        assert client.get("/assets/app.js").status_code == 401
        login(client, vault, readonly=True)
        response = client.get("/")
        nonce = response.headers["content-security-policy"].split("'nonce-", 1)[1].split("'", 1)[0]
        assert f'<script nonce="{nonce}">' in response.text
        assert client.get("/assets/app.js").text == "window.app=1"
        assert not calls
        assert client.get("/assets/previous.js").text == "old-container-response"
        assert client.get("/api/projects/p/status").status_code == 200
        assert all(call.headers["authorization"] == "Bearer internal-trial-01-secret" for call in calls)
        assert client.post("/assets/app.js", headers={"Origin": ORIGIN}).status_code == 403


def test_frontend_directory_requires_a_built_absolute_path(provisioned):
    config, _, _ = provisioned
    with pytest.raises(ValueError, match="frontend_dir"):
        portal.Settings.load({**config, "frontend_dir": "relative/path"})


def test_disabling_invitation_revokes_existing_browser_access(provisioned):
    _, vault, store = provisioned
    with client_for(provisioned) as client:
        login(client, vault)
        identity = client.get("/invite/status").json()["tester_id"]
        assert identity.startswith("tester_")
        store.set_access("trial-01", enabled=False)
        assert client.get("/api/projects").status_code == 401
        assert client.get("/invite/status").status_code == 401
    # A deliberately disabled tester must not prevent the entire portal starting.
    with client_for(provisioned) as client:
        assert client.get("/invite").status_code == 200


def test_only_successful_fingerprinted_assets_are_cached(provisioned):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        assert client.get("/assets/index-abcdefgh.js").headers["cache-control"] == "no-store"
        login(client, vault)
        for path in ("/assets/index-abcdefgh.js", "/assets/geist-normal-a1b2c3d4.woff2"):
            assert client.get(path).headers["cache-control"] == "private, max-age=31536000, immutable"
        for path in ("/", "/api/projects/p/snapshot", "/assets/app.js"):
            assert client.get(path).headers["cache-control"] == "no-store"


def test_unlimited_hosted_quota_is_explicit_and_preserves_usage(provisioned):
    config, vault, store = provisioned
    config["token_limit"] = None
    ticket = store.reserve("trial-01", 99)
    store.settle(ticket, 99)
    with client_for(provisioned) as client:
        assert "不设累计 token 上限" in client.get("/invite").text
        login(client, vault)
        status = client.get("/invite/status").json()
        assert status["token_unlimited"] is True
        assert status["token_limit"] is None
        assert status["tokens_remaining"] is None
        assert status["tokens_used"] == 99
        request = client.app.state.store.reserve("trial-01", 2_000_000)
        client.app.state.store.settle(request, 0)


def test_new_workspace_is_allowed_without_arbitrary_workdir(provisioned):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        login(client, vault)
        assert client.post("/api/daemons", json={"name": "my-project"}, headers={"Origin": ORIGIN}).status_code == 200
        for field in ("workdir", "launch_cwd"):
            assert client.post("/api/daemons", json={field: "/private"}, headers={"Origin": ORIGIN}).status_code == 403
        assert client.post("/api/new-global-control", json={}, headers={"Origin": ORIGIN}).status_code == 403


def test_bounded_body_and_generic_upstream_errors(provisioned, monkeypatch):
    _, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(500, stream=Chunks([b"private upstream token and path"]))

    with client_for(provisioned, upstream) as client:
        login(client, vault)
        response = client.post("/api/projects/p/message", content=b"x", headers={"Origin": ORIGIN})
        assert response.status_code == 500 and "private" not in response.text
        response = client.post("/api/projects/p/message", content=b"x",
                               headers={"Origin": ORIGIN, "Content-Length": str(portal.MAX_BODY_BYTES + 1)})
        assert response.status_code == 413
        assert len(calls) == 1
        response = client.post("/invite/login", content=(chunk for chunk in [b"x" * 2000, b"x" * 100]),
                               headers={"Origin": ORIGIN})
        assert response.status_code == 413


@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectError("private address"), 502),
    (httpx.ReadTimeout("private address"), 504),
])
def test_transport_failure_does_not_reflect_upstream(provisioned, error, expected):
    _, vault, _ = provisioned

    def upstream(request):
        raise error

    with client_for(provisioned, upstream) as client:
        login(client, vault)
        response = client.get("/api/projects")
        assert response.status_code == expected
        assert "private address" not in response.text


def test_streaming_response_closes_and_preserves_bytes(provisioned):
    _, vault, _ = provisioned
    stream = Chunks([b"data: first\n\n", b"data: second\n\n"])

    def upstream(request):
        return httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"})

    with client_for(provisioned, upstream) as client:
        login(client, vault)
        response = client.post("/api/projects/p/message/stream", json={}, headers={"Origin": ORIGIN})
        assert response.content == b"data: first\n\ndata: second\n\n"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["content-type"] == "text/event-stream"
        assert stream.closed and stream.reads == 2
    assert client.app.state.client.is_closed


def test_streaming_does_not_buffer_and_disconnect_closes():
    async def run():
        stream = Chunks([b"first", b"second"])
        upstream = httpx.Response(200, stream=stream)
        response = portal.ProxyResponse(upstream, {})
        disconnect = asyncio.Event()
        chunks = []

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message["body"])
                assert stream.reads == 1
                disconnect.set()
                await asyncio.Event().wait()

        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
        assert chunks == [b"first"] and stream.closed

    asyncio.run(run())


@pytest.mark.parametrize("location,expected", [
    ("/?project=p&token=internal-trial-01-secret", 303),
    ("https://evil.test/path", 502),
    ("//evil.test/path", 502),
    ("/?secret=internal-trial-01-secret", 502),
])
def test_redirects_never_expose_internal_tokens(provisioned, location, expected):
    _, vault, _ = provisioned
    with client_for(provisioned, lambda request: httpx.Response(303, headers={"Location": location})) as client:
        login(client, vault)
        response = client.get("/api/projects")
        assert response.status_code == expected
        assert "internal-trial-01-secret" not in response.text + str(response.headers)
        if expected == 303:
            assert response.headers["location"] == "/?project=p"


class FakeSocket:
    def __init__(self):
        self.messages = []
        self.closed = False
        self.queue = asyncio.Queue()
        self.queue.put_nowait('{"event":"ready"}')

    async def send(self, message):
        self.messages.append(message)
        self.queue.put_nowait(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


def test_websocket_forwarding_identity_auth_and_disconnect(provisioned, monkeypatch):
    config, vault, _ = provisioned
    connections = []

    def connect(url, **kwargs):
        socket = FakeSocket()
        connections.append((url, kwargs, socket))
        return socket

    monkeypatch.setattr(portal, "websocket_connect", connect)
    with client_for(provisioned) as client:
        login(client, vault)
        with client.websocket_connect(
            "wss://portal.test/api/projects/trial-02-project/stream?token=browser-secret&view=ui",
            headers={"Origin": ORIGIN, "Authorization": "browser-secret", "X-Tenant": "trial-02"},
        ) as ws:
            assert ws.receive_json() == {"event": "ready"}
            ws.send_text("hello")
            assert ws.receive_text() == "hello"
            ws.send_bytes(b"binary")
            assert ws.receive_bytes() == b"binary"
        url, kwargs, upstream = connections[-1]
        assert url == "ws://trial-01:8080/api/projects/trial-02-project/stream?view=ui&token=internal-trial-01-secret"
        assert kwargs["extra_headers"] == {"Authorization": "Bearer " + config["tenants"]["trial-01"]["token"]}
        assert "browser-secret" not in url + str(kwargs)
        assert upstream.closed and upstream.messages == ["hello", b"binary"]


def test_websocket_readonly_blocks_data_messages(provisioned, monkeypatch):
    _, vault, _ = provisioned
    connections = []

    def connect(*args, **kwargs):
        socket = FakeSocket()
        connections.append(socket)
        return socket

    monkeypatch.setattr(portal, "websocket_connect", connect)
    with client_for(provisioned) as client:
        login(client, vault, readonly=True)
        with client.websocket_connect("wss://portal.test/api/projects/p/stream", headers={"Origin": ORIGIN}) as ws:
            assert ws.receive_json() == {"event": "ready"}
            ws.send_text('{"operation":"write"}')
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4403
        assert not connections[0].messages and connections[0].closed


@pytest.mark.parametrize("path,origin", [
    ("/api/projects/p/stream", None),
    ("/api/projects/p/stream", "https://evil.test"),
    ("/api/runtime/stream", ORIGIN),
])
def test_websocket_origin_and_route_checked_before_upstream(provisioned, monkeypatch, path, origin):
    _, vault, _ = provisioned

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized websocket must not connect upstream")

    monkeypatch.setattr(portal, "websocket_connect", forbidden)
    with client_for(provisioned) as client:
        login(client, vault)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("wss://portal.test" + path, headers={} if origin is None else {"Origin": origin}):
                pass
        assert exc.value.code == 4403


def test_config_factory_env_and_local_cookie(provisioned, tmp_path, monkeypatch):
    config, vault, _ = provisioned
    config_path = tmp_path / "portal.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("ARGUS_WEB_TRIAL_CONFIG", str(config_path))
    assert isinstance(portal.create_app(), portal.FastAPI)
    config["secure_cookie"] = False
    with client_for(provisioned) as client:
        assert "Secure" not in login(client, vault).headers["set-cookie"]
    for field in ("state_dir", "key_file"):
        invalid = {**config, field: "relative"}
        with pytest.raises(ValueError, match="absolute"):
            portal.create_app(invalid)
    with pytest.raises(ValueError, match="exactly"):
        portal.create_app({**config, "tenants": {"trial-01": config["tenants"]["trial-01"]}})
    with pytest.raises(ValueError, match="existing"):
        portal.create_app({**config, "state_dir": str(tmp_path / "missing")})
    assert not (tmp_path / "missing").exists()
    with pytest.raises(ValueError, match="configuration fields"):
        portal.create_app({**config, "public_config": True})


def test_backend_config_rejects_shared_identity_and_url_credentials(provisioned):
    config, _, _ = provisioned
    for url in ("http://user:password@trial-01", "file:///etc/passwd", "http://trial-01/path"):
        with pytest.raises(ValueError):
            portal.create_app({**config, "admin": {"url": url, "token": "admin-token"}})
    with pytest.raises(ValueError, match="own private token"):
        portal.create_app({**config, "admin": {**config["admin"], "token": config["tenants"]["trial-01"]["token"]}})


def unix_config(provisioned):
    config, _, _ = provisioned
    for tenant, backend in config["tenants"].items():
        backend["url"] = "http://localhost"
        backend["uds"] = str(portal.Path(config["state_dir"]).parent / tenant / "run" / "web.sock")
    config["admin"]["url"] = "http://127.0.0.1:8896"
    return config


def test_uds_config_allows_shared_origin_but_requires_unique_absolute_sockets(provisioned):
    config = unix_config(provisioned)
    settings = portal.Settings.load(config)
    assert settings.tenants["trial-01"].url == settings.tenants["trial-02"].url
    assert settings.tenants["trial-01"].uds != settings.tenants["trial-02"].uds
    assert settings.admin.uds is None
    for invalid in ("relative/web.sock", "", None, "/some/\x00socket"):
        config["tenants"]["trial-01"]["uds"] = invalid
        with pytest.raises(ValueError, match="absolute Unix socket"):
            portal.Settings.load(config)
    config["tenants"]["trial-01"]["uds"] = config["tenants"]["trial-02"]["uds"]
    with pytest.raises(ValueError, match="own socket"):
        portal.Settings.load(config)


def test_http_uds_pools_are_selected_by_cookie_not_browser_input(provisioned, monkeypatch):
    config = unix_config(provisioned)
    _, vault, _ = provisioned
    pools = {}
    requests = []

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, *, uds, trust_env, limits):
            assert trust_env is False
            pools[uds] = self
            self.uds, self.closed = uds, False

        async def handle_async_request(self, request):
            requests.append((self.uds, request))
            return httpx.Response(200, stream=Chunks([b"ok"]),
                                  headers={"Set-Cookie": "backend=private"})

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(portal.httpx, "AsyncHTTPTransport", Transport)
    app = portal.create_app(config)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert len(pools) == 11
        for tenant, other in (("trial-01", "trial-02"), ("trial-02", "trial-01")):
            login(client, vault, tenant)
            response = client.get(
                f"/api/projects/{other}-project/snapshot?uds=/attacker.sock&tenant={other}",
                headers={"X-Tenant": other, "X-Argus-Backend": other},
            )
            assert response.status_code == 200
            assert "set-cookie" not in response.headers
            uds, request = requests[-1]
            assert uds == config["tenants"][tenant]["uds"]
            assert request.url.host == "localhost"
            assert request.url.path == f"/api/projects/{other}-project/snapshot"
            assert request.headers["authorization"] == "Bearer " + config["tenants"][tenant]["token"]
            assert "cookie" not in request.headers
            assert request.extensions["argus_backend"] == tenant
        assert client.get("/", params={"token": config["admin_login_token"]}).status_code == 303
        assert client.get("/api/projects").status_code == 200
        assert requests[-1][0] is None
        assert requests[-1][1].url.host == "127.0.0.1"
    assert all(pool.closed for pool in pools.values())
    assert app.state.client.is_closed


def test_websocket_uds_uses_own_socket_and_authenticated_uri(provisioned, monkeypatch):
    config = unix_config(provisioned)
    _, vault, _ = provisioned
    connections = []

    def unix_connect(path, *, uri, **kwargs):
        socket = FakeSocket()
        connections.append((path, uri, kwargs, socket))
        return socket

    def no_tcp(*args, **kwargs):
        pytest.fail("Unix-socket tenants must not fall back to TCP")

    monkeypatch.setattr(portal, "websocket_unix_connect", unix_connect)
    monkeypatch.setattr(portal, "websocket_connect", no_tcp)
    with client_for(provisioned) as client:
        login(client, vault, "trial-02")
        with client.websocket_connect(
            "wss://portal.test/api/projects/trial-01-project/stream?view=ui&token=browser",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json() == {"event": "ready"}
            ws.send_text("message")
            assert ws.receive_text() == "message"
        path, uri, kwargs, socket = connections[0]
        assert path == config["tenants"]["trial-02"]["uds"]
        assert uri == "ws://localhost/api/projects/trial-01-project/stream?view=ui&token=internal-trial-02-secret"
        assert kwargs["extra_headers"]["Authorization"] == "Bearer internal-trial-02-secret"
        assert socket.closed


def compute_config(provisioned):
    config = unix_config(provisioned)
    config["compute_url"] = "http://localhost"
    config["compute_uds"] = str(portal.Path(config["state_dir"]).parent / "compute-socket" / "compute.sock")
    return config


def test_compute_proxy_uses_invitation_identity_without_browser_key_leak(provisioned):
    config = compute_config(provisioned)
    _, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b'{"jobs":[]}']), headers={
            "Content-Type": "application/json",
            "Set-Cookie": "private=compute",
            "Authorization": request.headers["authorization"],
            "Content-Disposition": 'attachment; filename="' + request.headers["authorization"][7:] + '"',
        })

    with client_for(provisioned, upstream) as client:
        for tenant, other in (("trial-01", "trial-02"), ("trial-02", "trial-01")):
            login(client, vault, tenant)
            response = client.get(
                f"/compute/jobs/99/logs?tenant={other}&token=browser-secret",
                headers={"Authorization": "Bearer browser-secret", "X-Argus-Readonly": "true"},
            )
            assert response.status_code == 200 and response.json() == {"jobs": []}
            request = calls[-1]
            assert request.extensions["argus_backend"] == "compute"
            assert request.url.host == "localhost"
            assert request.url.path == "/compute/jobs/99/logs"
            assert request.url.query == f"tenant={other}".encode()
            assert request.headers["authorization"] == "Bearer " + vault.credential(tenant)
            assert request.headers["x-argus-readonly"] == "false"
            assert "cookie" not in request.headers
            assert config["tenants"][tenant]["token"] not in str(request.headers)
            assert "authorization" not in response.headers and "set-cookie" not in response.headers
            assert vault.credential(tenant) not in response.text + str(response.headers)
            assert vault.credential(tenant) not in str(client.cookies)
        client.get("/api/projects")
        assert calls[-1].extensions["argus_backend"] == "trial-02"
        assert calls[-1].headers["authorization"] == "Bearer " + config["tenants"]["trial-02"]["token"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/compute/status"), ("GET", "/compute/jobs"),
    ("GET", "/compute/jobs/1"), ("GET", "/compute/jobs/1/logs"),
    ("POST", "/compute/jobs"), ("POST", "/compute/jobs/1/cancel"),
])
def test_compute_allowed_routes_and_csrf(provisioned, method, path):
    compute_config(provisioned)
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        assert client.request(method, path, headers={"Accept": "text/html"}).status_code == 401
        login(client, vault)
        if method == "POST":
            assert client.post(path, json={}).status_code == 403
            assert client.post(path, json={}, headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.request(method, path, headers={"Origin": ORIGIN}).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("POST", "/compute/jobs"), ("POST", "/compute/jobs/1/cancel"),
    ("DELETE", "/compute/jobs/1"), ("PATCH", "/compute/jobs/1"),
    ("PUT", "/compute/jobs/1"), ("OPTIONS", "/compute/jobs"),
])
def test_compute_kiosk_mutations_never_reach_backend(provisioned, method, path):
    compute_config(provisioned)
    _, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        login(client, vault, readonly=True)
        assert client.request(method, path, headers={
            "Origin": ORIGIN, "X-Argus-Readonly": "false",
        }).status_code == 403
        assert not calls


def test_compute_kiosk_reads_propagate_server_owned_readonly_role(provisioned):
    compute_config(provisioned)
    _, vault, _ = provisioned
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([b"{}"]))

    with client_for(provisioned, upstream) as client:
        login(client, vault, readonly=True)
        assert client.get("/compute/status", headers={"X-Argus-Readonly": "false"}).status_code == 200
        assert calls[-1].headers["x-argus-readonly"] == "true"


def test_compute_is_not_an_admin_identity_or_unconfigured_fallback(provisioned):
    config, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        login(client, vault)
        assert client.get("/compute/status").status_code == 404
        assert not calls
    compute_config(provisioned)
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        client.get("/", params={"token": config["admin_login_token"]})
        assert client.get("/compute/status").status_code == 403
        assert client.post("/compute/jobs", headers={"Origin": ORIGIN}, json={}).status_code == 403
        assert not calls
        login(client, vault)
        for method, path in (("POST", "/compute/admin/reset"), ("GET", "/compute/../compute/admin"),
                             ("POST", "/compute/status"), ("GET", "/compute/jobs/1/cancel")):
            assert client.request(method, path, headers={"Origin": ORIGIN}).status_code == 403
        assert not calls


def test_compute_connection_pool_is_separate_and_closed(provisioned, monkeypatch):
    config = compute_config(provisioned)
    _, vault, _ = provisioned
    pools = {}
    calls = []

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, *, uds, **kwargs):
            pools[uds] = self
            self.uds, self.closed = uds, False

        async def handle_async_request(self, request):
            calls.append((self.uds, request))
            return httpx.Response(200, stream=Chunks([b"{}"]))

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(portal.httpx, "AsyncHTTPTransport", Transport)
    with TestClient(portal.create_app(config), base_url=ORIGIN, follow_redirects=False) as client:
        login(client, vault)
        assert client.get("/compute/status").status_code == 200
        assert len(pools) == 12
        assert calls[-1][0] == config["compute_uds"]
        assert calls[-1][1].headers["authorization"] == "Bearer " + vault.credential("trial-01")
    assert all(pool.closed for pool in pools.values())


def test_optional_compute_endpoint_configuration(provisioned):
    config, _, _ = provisioned
    with pytest.raises(ValueError, match="requires compute_url"):
        portal.Settings.load({**config, "compute_uds": "/private/compute.sock"})
    with pytest.raises(ValueError, match="absolute Unix socket"):
        portal.Settings.load({**config, "compute_url": "http://localhost", "compute_uds": "relative"})
    with pytest.raises(ValueError, match="HTTP origin"):
        portal.Settings.load({**config, "compute_url": "file:///private"})
    settings = portal.Settings.load({**config, "compute_url": "http://127.0.0.1:9000"})
    assert settings.compute.url == "http://127.0.0.1:9000" and settings.compute.uds is None


@pytest.mark.parametrize("readonly", [False, True])
def test_authenticated_launcher_links_workspace_compute_quota_and_logout(provisioned, readonly):
    compute_config(provisioned)
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        response = login(client, vault, readonly=readonly)
        assert response.json()["redirect"] == "/invite"
        page = client.get(response.json()["redirect"])
        assert page.status_code == 200 and "我的研究空间" in page.text
        assert 'id="login"' not in page.text
        assert "trial-01" in page.text
        assert 'href="/invite/compute">GPU任务队列' in page.text
        assert 'href="/invite/compute">模型与GPU额度' in page.text
        assert 'href="/invite/status"' in page.text
        assert 'method="post" action="/invite/logout"' in page.text
        assert "<script" not in page.text
        assert vault.credential("trial-01") not in page.text
        workspace = "/?kiosk=1" if readonly else "/"
        assert f'href="{workspace}">进入工作区' in page.text
        assert ("只读浏览" in page.text) is readonly
        assert page.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in page.headers["content-security-policy"]
        logout_response = client.post(
            "/invite/logout", headers={"Origin": ORIGIN, "Accept": "text/html"},
        )
        assert logout_response.status_code == 303
        assert logout_response.headers["location"] == "/invite"
        assert "Max-Age=0" in logout_response.headers["set-cookie"]
        assert 'id="login"' in client.get("/invite").text


@pytest.mark.parametrize("readonly", [False, True])
def test_compute_dashboard_and_external_script_authentication(provisioned, readonly):
    from argus_skill.trial.compute_page import PAGE, SCRIPT

    compute_config(provisioned)
    _, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        for path in ("/invite/compute", "/invite/compute.js"):
            assert client.get(path).status_code == 401
        response = client.get("/invite/compute", headers={"Accept": "text/html"})
        assert response.status_code == 303 and response.headers["location"] == "/invite"
        assert client.get("/invite/compute.js", headers={"Accept": "text/html"}).status_code == 401
        login(client, vault, readonly=readonly)
        page, script = client.get("/invite/compute"), client.get("/invite/compute.js")
        assert page.status_code == script.status_code == 200
        assert page.text == PAGE and script.text == SCRIPT
        assert page.headers["content-type"].startswith("text/html")
        assert script.headers["content-type"].startswith("application/javascript")
        assert '<script src="/invite/compute.js"' in page.text
        for response in (page, script):
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert vault.credential("trial-01") not in response.text
        assert not calls


def test_dashboard_rejects_admin_and_missing_compute_without_upstream(provisioned):
    config, vault, _ = provisioned
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        login(client, vault)
        assert "尚未配置" in client.get("/invite").text
        assert client.get("/invite/compute").status_code == 404
        assert client.get("/invite/compute.js").status_code == 404
    compute_config(provisioned)
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        assert client.get("/", params={"token": config["admin_login_token"]}).status_code == 303
        assert 'href="/invite/compute"' not in client.get("/invite").text
        assert client.get("/invite/compute").status_code == 403
        assert client.get("/invite/compute.js").status_code == 403
        assert not calls


@pytest.mark.parametrize("private_login_enabled", [False, True])
def test_legacy_demo_token_only_redirects_to_invitation(provisioned, private_login_enabled):
    config, _, _ = provisioned
    private_login = config["admin_login_token"]
    if not private_login_enabled:
        config.pop("admin_login_token")
    calls = []
    with client_for(provisioned, lambda request: calls.append(request)) as client:
        response = client.get("/", params={
            "token": config["admin"]["token"], "project": "old-demo", "kiosk": "1",
        })
        assert response.status_code == 303
        assert response.headers["location"] == "/invite"
        assert "set-cookie" not in response.headers
        assert config["admin"]["token"] not in response.text + str(response.headers)
        assert 'id="login"' in client.get(response.headers["location"]).text
        assert client.get("/api/projects").status_code == 401
        if not private_login_enabled:
            assert client.get("/", params={"token": private_login}).status_code == 401
        assert not calls


@pytest.mark.parametrize("private_login_enabled", [False, True])
def test_previously_minted_public_admin_cookies_are_invalid(provisioned, private_login_enabled):
    config, vault, _ = provisioned
    if not private_login_enabled:
        config.pop("admin_login_token")
    old_identity = {"tenant": "admin", "role": "admin", "readonly": False,
                    "exp": int(time.time()) + portal.SESSION_SECONDS}
    old_cookie = vault.cipher.encrypt(
        portal.COOKIE_DOMAIN + json.dumps(old_identity).encode()
    ).decode()
    with client_for(provisioned) as client:
        client.cookies.set(portal.COOKIE, old_cookie)
        assert client.get("/invite/status").status_code == 401
        assert client.get("/api/projects").status_code == 401
        assert 'id="login"' in client.get("/invite").text
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("wss://portal.test/api/projects/p/stream", headers={"Origin": ORIGIN}):
                pass
        assert exc.value.code == 4401


@pytest.mark.parametrize("change", ["rotate", "disable"])
def test_private_admin_rotation_revokes_only_admin_sessions(provisioned, change):
    config, vault, store = provisioned
    reservation = store.reserve("trial-01", 23)
    store.settle(reservation, 23)
    with client_for(provisioned) as client:
        result = client.get("/", params={"token": config["admin_login_token"], "kiosk": "1"})
        assert result.status_code == 303
        admin_cookie = result.cookies[portal.COOKIE]
        payload = vault.cipher.decrypt(admin_cookie.encode()).decode()
        assert config["admin_login_token"] not in payload
        assert config["admin"]["token"] not in payload
        assert "admin_binding" in payload
        assert "admin_binding" not in client.get("/invite/status").json()
        tenant_cookie = login(client, vault).cookies[portal.COOKIE]
    if change == "rotate":
        config["admin_login_token"] += "-rotated"
    else:
        config.pop("admin_login_token")
    with client_for(provisioned) as client:
        client.cookies.set(portal.COOKIE, admin_cookie)
        assert client.get("/api/projects").status_code == 401
        client.cookies.clear()
        client.cookies.set(portal.COOKIE, tenant_cookie)
        assert client.get("/invite/status").json()["tokens_used"] == 23
        if change == "rotate":
            client.cookies.clear()
            response = client.get("/", params={"token": config["admin_login_token"]})
            assert response.status_code == 303
            assert client.get("/invite/status").json()["role"] == "admin"


@pytest.mark.parametrize("invalid", ["", False, "has whitespace", "argus_trial_" + "a" * 64])
def test_invalid_private_admin_login_configuration(provisioned, invalid):
    config, _, _ = provisioned
    with pytest.raises(ValueError, match="distinct private login"):
        portal.Settings.load({**config, "admin_login_token": invalid})


def test_private_admin_login_cannot_reuse_backend_credentials(provisioned):
    config, _, _ = provisioned
    for token in (config["admin"]["token"], config["tenants"]["trial-01"]["token"]):
        with pytest.raises(ValueError, match="distinct private login"):
            portal.Settings.load({**config, "admin_login_token": token})
    assert portal.Settings.load({**config, "admin_login_token": None}).admin_login_token is None


def test_private_admin_websocket_still_uses_internal_demo_token(provisioned, monkeypatch):
    config, _, _ = provisioned
    connections = []

    def connect(uri, **kwargs):
        socket = FakeSocket()
        connections.append((uri, kwargs, socket))
        return socket

    monkeypatch.setattr(portal, "websocket_connect", connect)
    with client_for(provisioned) as client:
        client.get("/", params={"token": config["admin_login_token"], "kiosk": "1"})
        with client.websocket_connect("wss://portal.test/api/projects/demo/stream", headers={"Origin": ORIGIN}) as ws:
            assert ws.receive_json() == {"event": "ready"}
            ws.send_text("not permitted")
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4403
        uri, options, socket = connections[0]
        assert uri == "ws://host.docker.internal:8896/api/projects/demo/stream?token=" + config["admin"]["token"]
        assert options["extra_headers"]["Authorization"] == "Bearer " + config["admin"]["token"]
        assert config["admin_login_token"] not in uri + str(options)
        assert socket.closed and not socket.messages


def test_web_allowance_default_does_not_change_store_desktop_default(provisioned):
    config, vault, desktop_default_store = provisioned
    assert desktop_default_store.token_limit == 1_000_000
    reservation = desktop_default_store.reserve("trial-01", 450)
    desktop_default_store.settle(reservation, 321)
    assert portal.Settings.load(config).token_limit == 10_000_000
    with client_for(provisioned) as client:
        login(client, vault)
        assert client.app.state.store.token_limit == 10_000_000
        status = client.get("/invite/status").json()
        assert status["token_limit"] == 10_000_000
        assert status["tokens_used"] == 321
        assert status["tokens_remaining"] == 9_999_679
    assert desktop_default_store.token_limit == 1_000_000
    assert desktop_default_store.status("trial-01")["tokens_used"] == 321
    assert desktop_default_store.status("trial-01")["token_limit"] == 1_000_000


@pytest.mark.parametrize("limit,copy", [(10_000_000, "1000万"), (2_000_000, "200万"), (12_345, "12,345")])
def test_configured_web_token_limit_drives_status_and_copy_without_reset(provisioned, limit, copy):
    config, vault, store = provisioned
    config["token_limit"] = limit
    reservation = store.reserve("trial-01", 99)
    store.settle(reservation, 99)
    with client_for(provisioned) as client:
        assert f"{copy} tokens" in client.get("/invite").text
        login(client, vault)
        status = client.get("/invite/status").json()
        assert status["token_limit"] == limit
        assert status["tokens_used"] == 99
        assert status["tokens_remaining"] == limit - 99


@pytest.mark.parametrize("invalid", [0, -1, True, "10000000", 1.5])
def test_web_token_limit_requires_positive_integer(provisioned, invalid):
    config, _, _ = provisioned
    with pytest.raises(ValueError, match="positive integer"):
        portal.Settings.load({**config, "token_limit": invalid})


def test_invitation_only_copy_and_private_admin_entry_stays_hidden(provisioned):
    _, vault, _ = provisioned
    with client_for(provisioned) as client:
        entry = client.get("/invite")
        assert "邀请码入口" in entry.text and "进入工作区" in entry.text
        assert "无需注册或其他身份验证" in entry.text
        assert "同一邀请码可在不同设备继续进入同一独立工作空间" in entry.text
        assert "登录" not in entry.text
        assert 'id="code" type="password"' in entry.text
        for account_field in ('type="email"', 'name="username"', 'name="password"', 'href="/admin"'):
            assert account_field not in entry.text
        assert "admin_login_token" not in entry.text and "oauth" not in entry.text.lower()
        login(client, vault)
        launcher = client.get("/invite")
        assert "登录" not in launcher.text and "账户" not in launcher.text
        assert 'href="/admin"' not in launcher.text
        assert "退出当前空间" in launcher.text


@pytest.mark.parametrize("path", ["/invite/status", "/invite/compute.js", "/api/projects", "/compute/status"])
def test_invalid_session_prompts_for_invitation_only(provisioned, path):
    with client_for(provisioned) as client:
        client.cookies.set(portal.COOKIE, "invalid-session")
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"] == "请先输入邀请码"


def test_same_invitation_resumes_same_workspace_on_different_devices(provisioned):
    _, vault, store = provisioned
    reservation = store.reserve("trial-01", 75)
    store.settle(reservation, 75)

    def upstream(request):
        return httpx.Response(200, stream=Chunks([request.url.host.encode()]))

    with client_for(provisioned, upstream) as first, client_for(provisioned, upstream) as second:
        login(first, vault, "trial-01")
        login(second, vault, "trial-01")
        assert first.get("/api/projects").text == second.get("/api/projects").text == "trial-01"
        for device in (first, second):
            status = device.get("/invite/status").json()
            assert status["key_id"] == "trial-01"
            assert status["tokens_used"] == 75
        login(second, vault, "trial-02")
        assert second.get("/api/projects").text == "trial-02"
        assert first.get("/api/projects").text == "trial-01"


def test_team_training_notice_requires_submission_and_keeps_external_choice_separate(provisioned, tmp_path):
    from argus_skill.trial.analytics import Analytics

    config, vault, _ = provisioned
    analytics = Analytics(
        tmp_path / "training-research",
        {"trial-01": {"data_dir": tmp_path / "tenant", "internal_test": True}},
        tmp_path / "meter-unused", tmp_path / "compute-unused",
    )
    app = portal.create_app(config, analytics=analytics, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True}),
    ))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        page = client.get("/invite")
        assert "内部模型训练" in page.text and "调用参数和执行结果" in page.text
        assert 'id="external-sharing-notice" type="checkbox">' in page.text
        with analytics._db() as db:
            assert db.execute("SELECT count(*) FROM consents").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM training_permissions").fetchone()[0] == 0
        body = {"code": vault.credential("trial-01"), "data_notice_accepted": True,
                "notice_version": COMBINED_NOTICE_VERSION}
        invalid = {**body, "external_sharing_accepted": "true"}
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=invalid).status_code == 400
        old = {**body, "notice_version": "operator-analytics-v2"}
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=old).status_code == 403
        with analytics._db() as db:
            assert db.execute("SELECT count(*) FROM training_permissions").fetchone()[0] == 0
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        permission = client.get("/trial/data-permissions").json()
        assert permission["internal_training"] is True
        assert permission["external_sharing"] is False
        assert permission["granted_at"]["internal_training"] >= permission["onboarding"]["accepted_at"]
        original_grant = permission["granted_at"]["internal_training"]
        body["external_sharing_accepted"] = True
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
        permission = client.get("/trial/data-permissions").json()
        assert permission["external_sharing"] is True
        assert permission["granted_at"]["internal_training"] == original_grant
