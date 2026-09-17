from __future__ import annotations

import asyncio
import sqlite3
import threading

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from argus.trial import web_portal as portal
from argus.trial.secrets import Vault
from argus.trial.store import Store
from argus.trial.training_data import COMBINED_NOTICE_VERSION

ORIGIN = "https://portal.test"


@pytest.fixture
def provisioned(tmp_path):
    state = tmp_path / "meter"
    state.mkdir()
    key = tmp_path / "master.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    vault = Vault(key, state / "github-token.enc")
    store = Store(state / "usage.sqlite3", key_limit=len(portal.TENANT_IDS))
    for tenant in portal.TENANT_IDS:
        store.issue(tenant, vault.credential(tenant))
    config = {
        "state_dir": str(state), "key_file": str(key),
        "tenants": {
            tenant: {"url": f"http://{tenant}:8080", "token": f"internal-{tenant}-secret"}
            for tenant in sorted(portal.TENANT_IDS)
        },
    }
    return config, vault, store


async def login(client, vault, tenant="trial-01", *, readonly=False):
    response = await client.post("/invite/login", headers={"Origin": ORIGIN}, json={
        "code": vault.credential(tenant), "readonly": readonly,
        "data_notice_accepted": True,
        "notice_version": COMBINED_NOTICE_VERSION,
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("with_analytics", [False, True])
def test_slow_authorization_leaves_event_loop_and_other_tenant_control_available(
    provisioned, tmp_path, monkeypatch, with_analytics,
):
    from argus.trial.analytics import Analytics

    config, vault, store = provisioned
    analytics = Analytics(
        tmp_path / "analytics",
        {tenant: {"data_dir": tmp_path / tenant, "internal_test": False} for tenant in config["tenants"]},
        store.path, tmp_path / "compute.sqlite3",
    ) if with_analytics else None
    seen = []

    async def backend(request):
        seen.append((request.url.path, request.headers["authorization"]))
        return httpx.Response(200, stream=httpx.ByteStream(b'{"ok":true}'),
                              headers={"Content-Type": "application/json"})

    app = portal.create_app(config, transport=httpx.MockTransport(backend), analytics=analytics)
    entered, release = threading.Event(), threading.Event()
    authorization_threads = []

    async def scenario():
        async with app.router.lifespan_context(app):
            async with (
                httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as slow_client,
                httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as other_client,
            ):
                await login(slow_client, vault)
                await login(other_client, vault, "trial-02")
                original = app.state.store.check_access
                loop_thread = threading.get_ident()

                def slow_access(tenant):
                    authorization_threads.append((tenant, threading.get_ident()))
                    if tenant == "trial-01":
                        entered.set()
                        assert release.wait(3), "Authorization was not released"
                    return original(tenant)

                monkeypatch.setattr(app.state.store, "check_access", slow_access)
                slow = asyncio.create_task(slow_client.get("/api/projects"))
                try:
                    assert await asyncio.to_thread(entered.wait, 1)
                    response = await asyncio.wait_for(other_client.post(
                        "/api/projects/demo/daemon/stop", headers={"Origin": ORIGIN}, json={},
                    ), 0.75)
                    assert response.status_code == 200
                    assert not slow.done()
                    assert seen[-1] == (
                        "/api/projects/demo/daemon/stop", "Bearer internal-trial-02-secret",
                    )
                finally:
                    release.set()
                    await slow
                assert all(thread != loop_thread for _, thread in authorization_threads)
                assert [tenant for tenant, _ in authorization_threads].count("trial-01") == 1
                # A request-scoped result must never become a cross-request access cache.
                await asyncio.to_thread(store.set_access, "trial-02", enabled=False)
                denied = await other_client.post(
                    "/api/projects/demo/daemon/stop", headers={"Origin": ORIGIN}, json={},
                )
                assert denied.status_code == 401

    asyncio.run(scenario())


def test_reserved_pool_forwards_control_while_real_data_connection_is_occupied(provisioned, monkeypatch):
    config, vault, _ = provisioned
    real_transport = httpx.AsyncHTTPTransport
    pools = []

    def small_data_pool(**kwargs):
        limits = kwargs["limits"]
        pools.append((limits.max_connections, limits.max_keepalive_connections))
        if limits.max_connections == 100:
            kwargs["limits"] = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        return real_transport(**kwargs)

    monkeypatch.setattr(portal.httpx, "AsyncHTTPTransport", small_data_pool)

    async def scenario():
        stream_entered, release_stream = asyncio.Event(), asyncio.Event()
        control_entered, release_controls = asyncio.Queue(), asyncio.Event()
        objective_entered, release_objectives = asyncio.Queue(), asyncio.Event()
        seen = []
        handlers = set()

        async def backend(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                while True:
                    try:
                        raw = await reader.readuntil(b"\r\n\r\n")
                    except asyncio.IncompleteReadError:
                        return
                    lines = raw.decode().split("\r\n")
                    method, path, _ = lines[0].split(" ")
                    headers = {key.lower(): value for key, value in
                               (line.split(": ", 1) for line in lines[1:] if ": " in line)}
                    await reader.readexactly(int(headers.get("content-length", "0")))
                    seen.append((method, path, headers))
                    if path.endswith("/daemon/stop?hold=1"):
                        control_entered.put_nowait(True)
                        await release_controls.wait()
                    if path.endswith("/continuous?hold=1"):
                        objective_entered.put_nowait(True)
                        await release_objectives.wait()
                    if path.endswith("/message/stream"):
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
                        await writer.drain()
                        stream_entered.set()
                        await release_stream.wait()
                        writer.write(b"0\r\n\r\n")
                    else:
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config["tenants"]["trial-01"]["url"] = f"http://127.0.0.1:{port}"
        app = portal.create_app(config)
        try:
            async with server, app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
                    await login(client, vault)
                    stream = asyncio.create_task(client.post(
                        "/api/projects/demo/message/stream", headers={"Origin": ORIGIN}, json={"text": "run"},
                    ))
                    reads = []
                    controls = []
                    objectives = []
                    try:
                        await asyncio.wait_for(stream_entered.wait(), 1)
                        reads.append(asyncio.create_task(client.get("/api/projects")))
                        await asyncio.sleep(0.05)
                        assert not reads[0].done()
                        for path in (
                            "/api/projects/demo/daemon/stop", "/api/projects/demo/mission/abort",
                            "/api/projects/demo/continuous", "/api/projects/demo/backlog/item/stop",
                            "/api/projects/demo/message/cancel",
                        ):
                            response = await asyncio.wait_for(client.post(
                                path, headers={"Origin": ORIGIN, "X-Argus-Backend": "trial-02"},
                                json={"enabled": False},
                            ), 0.75)
                            assert response.status_code == 200
                            assert seen[-1][:2] == ("POST", path)
                            headers = {key.lower(): value for key, value in seen[-1][2].items()}
                            assert headers["authorization"] == "Bearer internal-trial-01-secret"
                            assert "x-argus-backend" not in headers and "cookie" not in headers
                        assert not reads[0].done() and not stream.done()
                        # Client-controlled method/path/header changes cannot gain a control slot.
                        denied = await client.post(
                            "/api/projects/demo/daemon/replace", headers={"Origin": ORIGIN}, json={},
                        )
                        assert denied.status_code == 403
                        # Goal changes may block in a model call themselves.
                        # Filling their two slots must still leave Stop reachable.
                        for number in range(2):
                            objectives.append(asyncio.create_task(client.post(
                                "/api/projects/demo/continuous?hold=1", headers={"Origin": ORIGIN},
                                json={"enabled": True, "objective": f"Goal {number}"},
                            )))
                        for _ in range(2):
                            await asyncio.wait_for(objective_entered.get(), 1)
                        stop = await asyncio.wait_for(client.post(
                            "/api/projects/demo/daemon/stop", headers={"Origin": ORIGIN}, json={},
                        ), 0.75)
                        assert stop.status_code == 200
                        assert seen[-1][:2] == ("POST", "/api/projects/demo/daemon/stop")
                        assert not any(goal.done() for goal in objectives)
                        pause = await asyncio.wait_for(client.post(
                            "/api/projects/demo/continuous", headers={"Origin": ORIGIN},
                            json={"enabled": False},
                        ), 0.75)
                        assert pause.status_code == 200
                        assert seen[-1][:2] == ("POST", "/api/projects/demo/continuous")
                        assert not any(goal.done() for goal in objectives)
                        objective_overflow = await asyncio.wait_for(client.post(
                            "/api/projects/demo/continuous?overflow=1",
                            headers={"Origin": ORIGIN, "X-Argus-Continuous-Pause": "true"},
                            json={"enabled": True, "objective": "Extra goal"},
                        ), 1.75)
                        assert objective_overflow.status_code == 504
                        assert all("overflow=1" not in path for _, path, _ in seen)
                        release_objectives.set()
                        assert all(response.status_code == 200 for response in await asyncio.gather(*objectives))
                        # A blocked backend cannot grow the reserved pool beyond
                        # four connections, or leave its next caller waiting forever.
                        for _ in range(4):
                            controls.append(asyncio.create_task(client.post(
                                "/api/projects/demo/daemon/stop?hold=1",
                                headers={"Origin": ORIGIN}, json={},
                            )))
                        for _ in range(4):
                            await asyncio.wait_for(control_entered.get(), 1)
                        overflow = await asyncio.wait_for(client.post(
                            "/api/projects/demo/daemon/stop?overflow=1",
                            headers={"Origin": ORIGIN}, json={},
                        ), 1.75)
                        assert overflow.status_code == 504
                        assert all("overflow=1" not in path for _, path, _ in seen)
                        release_controls.set()
                        assert all(response.status_code == 200 for response in await asyncio.gather(*controls))
                        await login(client, vault, readonly=True)
                        readonly = await client.post(
                            "/api/projects/demo/daemon/stop", headers={"Origin": ORIGIN}, json={},
                        )
                        assert readonly.status_code == 403
                    finally:
                        release_objectives.set()
                        release_controls.set()
                        release_stream.set()
                        await asyncio.gather(stream, *reads, *controls, *objectives)
                transport = app.state.client._transport
                assert set(transport.transports) == set(config["tenants"])
                assert set(transport.control_transports) == set(config["tenants"])
                assert set(transport.objective_transports) == set(config["tenants"])
                assert all(len({transport.transports[name], transport.control_transports[name],
                                transport.objective_transports[name]}) == 3 for name in config["tenants"])
            assert app.state.client.is_closed
            assert pools.count((100, 20)) == 11 and pools.count((4, 2)) == 11 and pools.count((2, 2)) == 11
        finally:
            release_objectives.set()
            release_controls.set()
            release_stream.set()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("value,expected", [
    ("1", "1"), ("86400", "86400"),
    ("Sun, 13 Sep 2026 12:00:00 GMT", "Sun, 13 Sep 2026 12:00:00 GMT"),
    ("-1", None), ("86401", None), ("1, 2", None),
    ("/private/backend/file", None), ("internal-trial-01-secret", None),
    ("Sun, 99 Sep 2026 12:00:00 GMT", None),
])
def test_retry_after_reaches_browser_without_backend_error_content(provisioned, value, expected):
    config, vault, _ = provisioned
    app = portal.create_app(config, transport=httpx.MockTransport(lambda request: httpx.Response(
        503, headers={"Retry-After": value},
        json={"detail": "internal-trial-01-secret /private/backend/file"},
    )))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json={
            "code": vault.credential("trial-01"),
        }).status_code == 200
        response = client.get("/api/projects")
        assert response.status_code == 503
        assert response.headers.get("Retry-After") == expected
        assert response.json() == {"detail": "Workspace request failed"}
        assert "internal-trial" not in response.text and "/private" not in response.text


def test_retry_after_nominated_as_hop_header_is_not_forwarded(provisioned):
    config, vault, _ = provisioned
    app = portal.create_app(config, transport=httpx.MockTransport(lambda request: httpx.Response(
        503, headers={"Retry-After": "1", "Connection": "Retry-After"},
    )))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json={
            "code": vault.credential("trial-01"),
        }).status_code == 200
        response = client.get("/api/projects")
        assert response.status_code == 503 and "Retry-After" not in response.headers


@pytest.mark.parametrize("failure", [sqlite3.OperationalError, OSError])
def test_authorization_storage_failure_stays_closed_and_sanitized(provisioned, monkeypatch, failure):
    config, vault, _ = provisioned
    forwarded = []
    app = portal.create_app(config, transport=httpx.MockTransport(lambda request: forwarded.append(request)))

    def unavailable(tenant):
        raise failure("internal-trial-01-secret /private/backend/file")

    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/invite/login", headers={"Origin": ORIGIN}, json={
            "code": vault.credential("trial-01"),
        }).status_code == 200
        monkeypatch.setattr(app.state.store, "check_access", unavailable)
        response = client.post("/api/projects/demo/daemon/stop", headers={"Origin": ORIGIN}, json={})
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {"detail": "Workspace authorization unavailable"}
        assert not forwarded
