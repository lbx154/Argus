from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import portalocker
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from argus_skill.trial import store as store_module
from argus_skill.trial.gateway import Settings, create_app, prepare, usage_total
from argus_skill.trial.responses import completion
from argus_skill.trial.secrets import Vault, write_private
from argus_skill.trial.store import Store, TrialError

GITHUB_SECRET = "test-github-credential-do-not-expose"
ACCESS_SECRET = "test-upstream-credential-do-not-expose"
KEY_ID = "a" * 64
PAYLOAD = {"model": "argus-trial", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 100}


@pytest.fixture
def settings(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    vault = Vault(key, state / "github-token.enc")
    vault.save(GITHUB_SECRET)
    return Settings(state, key, timeout=1)


def response_data():
    return {
        "id": "completion-test", "object": "response", "created_at": 1, "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def upstream(request):
    assert str(request.url) == "https://api.githubcopilot.com/responses"
    assert request.headers["authorization"] == f"Bearer {GITHUB_SECRET}"
    assert request.headers["copilot-integration-id"] == "copilot-developer-cli"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.5"
    assert body["reasoning"] == {"effort": "high"}
    assert body["max_output_tokens"] == 100 and body["store"] is False
    return httpx.Response(200, json=response_data(), headers={"X-Secret": ACCESS_SECRET})


def issued_auth(client, key_id=KEY_ID):
    credential = client.app.state.vault.credential(key_id)
    client.app.state.store.issue(key_id, credential)
    return {"Authorization": "Bearer " + credential}


def test_model_burst_waits_for_slots_without_failing_tasks(settings):
    active = peak = 0

    async def slow_upstream(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json=response_data())

    app = create_app(settings, transport=httpx.MockTransport(slow_upstream))
    with TestClient(app) as client:
        auth = issued_auth(client)
        with ThreadPoolExecutor(max_workers=14) as pool:
            responses = list(pool.map(
                lambda _: client.post("/v1/chat/completions", headers=auth, json=PAYLOAD),
                range(14),
            ))
        assert all(response.status_code == 200 for response in responses)
        assert peak == 10
        assert app.state.store.status(KEY_ID)["active_requests"] == 0
        assert app.state.store.status(KEY_ID)["tokens_used"] == 14 * 15
        assert app.state.request_slots._value == 10


def test_cancelled_queued_request_does_not_spend_or_leak_slot(settings):
    async def scenario():
        app = create_app(settings, transport=httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app):
            credential = app.state.vault.credential(KEY_ID)
            app.state.store.issue(KEY_ID, credential)
            app.state.request_slots = asyncio.Semaphore(0)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": "Bearer " + credential},
            ) as client:
                waiting = asyncio.create_task(client.post("/v1/chat/completions", json=PAYLOAD))
                await asyncio.sleep(0.01)
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
                assert app.state.store.status(KEY_ID)["tokens_used"] == 0
                app.state.request_slots.release()
                assert (await client.post("/v1/chat/completions", json=PAYLOAD)).status_code == 200
                assert app.state.request_slots._value == 1

    asyncio.run(scenario())


def test_key_metering_restart_and_no_secret_exposure(settings):
    transport = httpx.MockTransport(upstream)
    with TestClient(create_app(settings, transport=transport)) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json=PAYLOAD)
        assert result.status_code == 200
        assert result.json()["model"] == "argus-trial"
        assert "x-secret" not in result.headers
        assert GITHUB_SECRET not in result.text and ACCESS_SECRET not in result.text
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 15
        assert issued_auth(client) == auth
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 15
        assert client.get("/trial/status", headers=issued_auth(client, "b" * 64)).json()["tokens_used"] == 0
    with TestClient(create_app(settings, transport=transport)) as client:
        assert issued_auth(client) == auth
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 15
    for path in settings.state_dir.iterdir():
        assert GITHUB_SECRET.encode() not in path.read_bytes()
        assert ACCESS_SECRET.encode() not in path.read_bytes()


@pytest.mark.parametrize("mutation", [
    {"model": "expensive-model"}, {"max_tokens": -1}, {"max_tokens": True},
    {"max_tokens": 20000}, {"max_completion_tokens": 100}, {"n": 2},
    {"store": True}, {"usage": {"total_tokens": 0}}, {"base_url": "https://attacker.invalid"},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/image"}}]}]},
    {"tools": [{"type": "web_search"}]},
])
def test_rejects_budget_bypasses(settings, mutation):
    with TestClient(create_app(settings, transport=httpx.MockTransport(upstream))) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json={**PAYLOAD, **mutation})
        assert result.status_code == 400
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 0


def test_auth_and_model_routes(settings):
    with TestClient(create_app(settings)) as client:
        for path in ("/trial/status", "/v1/models"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer invented"}).status_code == 401
        assert client.post("/v1/chat/completions", json=PAYLOAD).status_code == 401
        assert client.get("/v1/models", headers=issued_auth(client)).json()["data"][0]["id"] == "argus-trial"
        assert client.get("/admin/token").status_code == 404


def test_global_tpm_timeout_is_exposed_without_spending_key_allowance(settings, monkeypatch):
    monkeypatch.setattr(store_module, "GLOBAL_TPM", 100_000)
    app = create_app(replace(settings, timeout=1.1), transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        auth = issued_auth(client)
        store = app.state.store
        store.clock = lambda: 1000
        issued_auth(client, "b" * 64)
        blocker = store.reserve("b" * 64, 100_000)
        store.settle(blocker, 100_000)
        store.clock = lambda: 1043
        attempts = 0
        reserve = store.reserve

        def tracked_reserve(key_id, amount):
            nonlocal attempts
            attempts += 1
            return reserve(key_id, amount)

        monkeypatch.setattr(store, "reserve", tracked_reserve)
        response = client.post("/v1/chat/completions", headers=auth, json=PAYLOAD)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "trial_tpm_exceeded"
        assert response.headers["retry-after"] == "17"
        assert attempts >= 2
        status = client.get("/trial/status", headers=auth).json()
        assert status["tokens_used"] == 0 and status["global_tpm_remaining"] == 0
        assert status["active_requests"] == 0
        assert app.state.request_slots._value == 10


@pytest.mark.parametrize("release_capacity", ["settle", "expire"])
def test_global_tpm_wait_succeeds_when_capacity_returns(settings, monkeypatch, release_capacity):
    monkeypatch.setattr(store_module, "GLOBAL_TPM", 100_000)

    async def run():
        app = create_app(replace(settings, timeout=2), transport=httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app):
            store = app.state.store
            now = [1000]
            store.clock = lambda: now[0]
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            store.issue("blocker", "blocker-credential")
            blocker = store.reserve("blocker", 100_000)
            if release_capacity == "expire":
                store.settle(blocker, 100_000)
            waiting = asyncio.Event()
            reserve = store.reserve

            def tracked_reserve(key_id, amount):
                try:
                    return reserve(key_id, amount)
                except TrialError as exc:
                    assert exc.code == "trial_tpm_exceeded"
                    waiting.set()
                    raise

            monkeypatch.setattr(store, "reserve", tracked_reserve)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test",
                headers={"Authorization": "Bearer " + credential},
            ) as client:
                task = asyncio.create_task(client.post("/v1/chat/completions", json=PAYLOAD))
                await asyncio.wait_for(waiting.wait(), 0.8)
                assert not task.done()
                assert store.status(KEY_ID)["tokens_used"] == 0
                assert app.state.request_slots._value == 9
                if release_capacity == "settle":
                    store.settle(blocker, 1)
                else:
                    now[0] += 61
                response = await asyncio.wait_for(task, 1.5)
                assert response.status_code == 200
                status = store.status(KEY_ID)
                assert status["tokens_used"] == 15 and status["active_requests"] == 0
                with store.transaction() as db:
                    assert db.execute("SELECT COUNT(*) FROM trial_requests WHERE key_id=?", (KEY_ID,)).fetchone()[0] == 1
                assert app.state.request_slots._value == 10

    asyncio.run(run())


@pytest.mark.parametrize("end_wait", ["cancel", "disable"])
def test_aborted_tpm_wait_does_not_spend_or_leak_slot(settings, monkeypatch, end_wait):
    monkeypatch.setattr(store_module, "GLOBAL_TPM", 100_000)

    async def run():
        upstream_calls = 0

        def handler(request):
            nonlocal upstream_calls
            upstream_calls += 1
            return upstream(request)

        app = create_app(replace(settings, timeout=2), transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            store.issue("blocker", "blocker-credential")
            blocker = store.reserve("blocker", 100_000)
            waiting = asyncio.Event()
            reserve = store.reserve

            def tracked_reserve(key_id, amount):
                try:
                    return reserve(key_id, amount)
                except TrialError as exc:
                    if exc.code == "trial_tpm_exceeded":
                        waiting.set()
                    raise

            monkeypatch.setattr(store, "reserve", tracked_reserve)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test",
                headers={"Authorization": "Bearer " + credential},
            ) as client:
                task = asyncio.create_task(client.post("/v1/chat/completions", json=PAYLOAD))
                await asyncio.wait_for(waiting.wait(), 0.8)
                assert app.state.request_slots._value == 9
                if end_wait == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    store.set_access(KEY_ID, enabled=False)
                    response = await asyncio.wait_for(task, 1.5)
                    assert response.status_code == 403
                    assert response.json()["error"]["code"] == "trial_access_disabled"
                status = store.status(KEY_ID)
                assert status["tokens_used"] == 0 and status["active_requests"] == 1
                with store.transaction() as db:
                    assert db.execute("SELECT COUNT(*) FROM trial_requests WHERE key_id=?", (KEY_ID,)).fetchone()[0] == 0
                assert app.state.request_slots._value == 10
                assert upstream_calls == 0
                store.set_access(KEY_ID, enabled=True)
                store.settle(blocker, 1)
                assert (await client.post("/v1/chat/completions", json=PAYLOAD)).status_code == 200
                assert upstream_calls == 1
                assert app.state.request_slots._value == 10

    asyncio.run(run())


@pytest.mark.parametrize("waiting_for", ["slot", "tpm"])
def test_disconnected_admission_wait_does_not_spend_or_leak_slot(settings, monkeypatch, waiting_for):
    monkeypatch.setattr(store_module, "GLOBAL_TPM", 100_000)

    async def run():
        def handler(request):
            pytest.fail("A disconnected admission must not contact the provider")

        app = create_app(replace(settings, timeout=2), transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            if waiting_for == "slot":
                app.state.request_slots = asyncio.Semaphore(0)
            else:
                store.issue("blocker", "blocker-credential")
                store.reserve("blocker", 100_000)
            waiting = asyncio.Event()
            if waiting_for == "tpm":
                reserve = store.reserve

                def tracked_reserve(key_id, amount):
                    try:
                        return reserve(key_id, amount)
                    except TrialError as exc:
                        assert exc.code == "trial_tpm_exceeded"
                        waiting.set()
                        raise

                monkeypatch.setattr(store, "reserve", tracked_reserve)
            sent_body = False
            disconnected = asyncio.Event()
            messages = []

            async def receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": json.dumps(PAYLOAD).encode()}
                if waiting_for == "slot":
                    waiting.set()
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1", "method": "POST", "scheme": "http",
                "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
                "query_string": b"", "root_path": "", "server": ("test", 80),
                "client": ("127.0.0.1", 1),
                "headers": [(b"authorization", ("Bearer " + credential).encode()), (b"content-type", b"application/json")],
            }
            task = asyncio.create_task(app(scope, receive, send))
            await asyncio.wait_for(waiting.wait(), 0.8)
            disconnected.set()
            await asyncio.wait_for(task, 1.5)
            assert messages[0]["status"] == 499
            status = store.status(KEY_ID)
            assert status["tokens_used"] == 0
            assert status["active_requests"] == (0 if waiting_for == "slot" else 1)
            with store.transaction() as db:
                assert db.execute("SELECT COUNT(*) FROM trial_requests WHERE key_id=?", (KEY_ID,)).fetchone()[0] == 0
            assert app.state.request_slots._value == (0 if waiting_for == "slot" else 10)

    asyncio.run(run())


def test_slot_and_tpm_wait_share_one_admission_deadline(settings, monkeypatch):
    monkeypatch.setattr(store_module, "GLOBAL_TPM", 100_000)

    async def run():
        app = create_app(replace(settings, timeout=0.5), transport=httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential(KEY_ID)
            store.issue(KEY_ID, credential)
            store.issue("blocker", "blocker-credential")
            store.reserve("blocker", 100_000)
            app.state.request_slots = asyncio.Semaphore(0)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test",
                headers={"Authorization": "Bearer " + credential},
            ) as client:
                task = asyncio.create_task(client.post("/v1/chat/completions", json=PAYLOAD))
                await asyncio.sleep(0.3)
                assert not task.done()
                app.state.request_slots.release()
                response = await asyncio.wait_for(task, 0.35)
                assert response.status_code == 429
                assert response.json()["error"]["code"] == "trial_tpm_exceeded"
                assert store.status(KEY_ID)["tokens_used"] == 0
                assert app.state.request_slots._value == 1

    asyncio.run(run())


def test_no_login_fails_closed_without_charging(settings):
    (settings.state_dir / "github-token.enc").unlink()
    with TestClient(create_app(settings)) as client:
        auth = issued_auth(client)
        assert client.get("/healthz").status_code == 503
        assert client.post("/v1/chat/completions", headers=auth, json=PAYLOAD).status_code == 503
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 0


def test_ledger_atomic_quota_and_recovery(tmp_path):
    store = Store(tmp_path / "usage.db")
    store.issue(KEY_ID, "credential")
    with store.transaction() as db:
        db.execute("UPDATE trial_keys SET used=900000")

    def reserve():
        try:
            return store.reserve(KEY_ID, 100_000)
        except TrialError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: reserve(), range(20)))
    assert sum(isinstance(item, int) for item in results) == 1
    assert results.count("trial_quota_exceeded") == 19
    assert store.status(KEY_ID)["tokens_used"] == 1_000_000
    store.recover()
    assert store.status(KEY_ID)["active_requests"] == 0
    assert store.status(KEY_ID)["tokens_remaining"] == 0


def test_gateway_limits_ten_concurrent_requests(settings):
    async def run():
        started = 0
        all_started, release = asyncio.Event(), asyncio.Event()

        async def held_upstream(request):
            nonlocal started
            started += 1
            if started == 10:
                all_started.set()
            await release.wait()
            return httpx.Response(200, json=response_data())

        app = create_app(settings, transport=httpx.MockTransport(held_upstream))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                credential = app.state.vault.credential(KEY_ID)
                app.state.store.issue(KEY_ID, credential)
                headers = {"Authorization": "Bearer " + credential}
                tasks = [asyncio.create_task(client.post("/v1/chat/completions", headers=headers, json=PAYLOAD)) for _ in range(10)]
                await asyncio.wait_for(all_started.wait(), 0.8)
                queued = asyncio.create_task(client.post("/v1/chat/completions", headers=headers, json=PAYLOAD))
                await asyncio.sleep(0.01)
                assert not queued.done()
                assert started == 10
                release.set()
                results = await asyncio.gather(*tasks, queued)
                assert all(result.status_code == 200 for result in results)
                status = (await client.get("/trial/status", headers=headers)).json()
                assert status["tokens_used"] == 165 and status["active_requests"] == 0
                assert (await client.post("/v1/chat/completions", headers=headers, json=PAYLOAD)).status_code == 200
    asyncio.run(run())


def test_disconnect_keeps_charge_and_releases_slot(settings):
    async def run():
        closed = asyncio.Event()

        class WaitingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        def handler(request):
            return httpx.Response(200, stream=WaitingStream())

        app = create_app(settings, transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            credential = app.state.vault.credential(KEY_ID)
            app.state.store.issue(KEY_ID, credential)
            payload = {**PAYLOAD, "stream": True}
            sent_body = False
            disconnected = asyncio.Event()

            async def receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": json.dumps(payload).encode()}
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    disconnected.set()
                    await asyncio.sleep(0)

            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1", "method": "POST", "scheme": "http",
                "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
                "query_string": b"", "root_path": "", "server": ("test", 80),
                "client": ("127.0.0.1", 1),
                "headers": [(b"authorization", ("Bearer " + credential).encode()), (b"content-type", b"application/json")],
            }
            await asyncio.wait_for(app(scope, receive, send), 1)
            assert closed.is_set()
            status = app.state.store.status(KEY_ID)
            assert status["active_requests"] == 0
            assert status["tokens_used"] == prepare(payload, settings.model)[1]
    asyncio.run(run())


def test_provider_timeout_cannot_hold_slot_or_refund_unknown_work(settings):
    from dataclasses import replace

    async def handler(request):
        await asyncio.Event().wait()

    settings = replace(settings, timeout=0.05)
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        auth = issued_auth(client)
        assert client.post("/v1/chat/completions", headers=auth, json=PAYLOAD).status_code == 502
        status = client.get("/trial/status", headers=auth).json()
        assert status["active_requests"] == 0
        assert status["tokens_used"] == prepare(PAYLOAD, settings.model)[1]


def sse_transport(*, usage=True, done=True, status=200):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True and "stream_options" not in payload
        content = 'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
        if done:
            data = response_data()
            if not usage:
                data.pop("usage")
            content += "data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n"
        return httpx.Response(status, text=content, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("usage,done,charge", [(True, True, 15), (False, True, None), (True, False, None)])
def test_stream_usage_and_incomplete_streams(settings, usage, done, charge):
    payload = {**PAYLOAD, "stream": True, "stream_options": {"include_usage": False}}
    with TestClient(create_app(settings, transport=sse_transport(usage=usage, done=done))) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json=payload)
        assert result.status_code == 200
        expected = charge if charge is not None else prepare(payload, settings.model)[1]
        status = client.get("/trial/status", headers=auth).json()
        assert status["tokens_used"] == expected and status["active_requests"] == 0
        assert ("[DONE]" in result.text) == (charge is not None)
        assert ("error" in result.text) == (charge is None)


@pytest.mark.parametrize("status,charged", [(400, False), (401, False), (422, False), (429, False), (500, True), (302, True)])
def test_upstream_errors_do_not_leak_credentials_or_follow_redirects(settings, status, charged):
    def handler(request):
        return httpx.Response(status, text=GITHUB_SECRET + ACCESS_SECRET, headers={"Location": "https://attacker.invalid", "Set-Cookie": ACCESS_SECRET})
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json=PAYLOAD)
        assert result.status_code == (400 if status in (400, 422) else 503 if status == 429 else 502)
        if status in (400, 422):
            assert result.json()["error"]["code"] == "provider_rejected_request"
        assert GITHUB_SECRET not in result.text and ACCESS_SECRET not in result.text
        assert "set-cookie" not in result.headers and "location" not in result.headers
        status = client.get("/trial/status", headers=auth).json()
        assert status["tokens_used"] == (prepare(PAYLOAD, settings.model)[1] if charged else 0)
        assert status["active_requests"] == 0


def test_provider_metadata_cannot_redirect_credentials(settings):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(200, json={**response_data(),
            "endpoints": {"api": "https://githubcopilot.com.attacker.invalid"}})

    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        auth = issued_auth(client)
        for _ in range(2):
            response = client.post("/v1/chat/completions", headers=auth, json=PAYLOAD)
            assert response.status_code == 200 and "endpoints" not in response.json()
        assert visited == ["https://api.githubcopilot.com/responses"] * 2


def test_single_process_lock(settings):
    with TestClient(create_app(settings)):
        with pytest.raises(portalocker.exceptions.LockException):
            with TestClient(create_app(settings)):
                pass


def test_cached_and_reasoning_tokens_not_double_counted():
    assert usage_total({"usage": {
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 90},
        "completion_tokens_details": {"reasoning_tokens": 10},
    }}) == 120
    assert usage_total({"usage": {"prompt_tokens": -5, "completion_tokens": 1}}) is None
    assert usage_total({"usage": {"prompt_tokens": True, "completion_tokens": 1}}) is None


def test_vault_tampering_and_file_permissions(settings):
    vault = Vault(settings.key_file, settings.state_dir / "github-token.enc")
    assert vault.read() == GITHUB_SECRET
    assert vault.token_path.stat().st_mode & 0o777 == 0o600
    vault.token_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        vault.read()
    settings.key_file.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        Vault(settings.key_file, vault.token_path)


def test_trial_enforces_high_and_preserves_parallel_local_tool_history():
    data = {**PAYLOAD, "reasoning_effort": "low", "temperature": 0.2, "messages": [
        {"role": "system", "content": "Use local tools."},
        {"role": "user", "content": [{"type": "text", "text": "Read both files."}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": call, "type": "function", "function": {"name": "view", "arguments": '{"path":"file"}'}}
            for call in ("call_a", "call_b")
        ]},
        {"role": "tool", "tool_call_id": "call_a", "content": "File A"},
        {"role": "tool", "tool_call_id": "call_b", "content": "File B"},
    ], "tools": [{"type": "function", "function": {"name": "view", "parameters": {"type": "object"}}}],
       "tool_choice": {"type": "function", "function": {"name": "view"}}}
    payload, _ = prepare(data, "gpt-5.5")
    assert payload["reasoning"] == {"effort": "high"} and payload["model"] == "gpt-5.5"
    assert payload["max_output_tokens"] == 100 and payload["store"] is False
    assert "temperature" not in payload
    assert payload["input"][-2:] == [
        {"type": "function_call_output", "call_id": "call_a", "output": "File A"},
        {"type": "function_call_output", "call_id": "call_b", "output": "File B"},
    ]
    assert payload["input"][2]["call_id"] == "call_a"
    assert payload["tool_choice"] == {"type": "function", "name": "view"}


@pytest.mark.parametrize("stream", [False, True])
def test_custom_patch_tool_round_trip_preserves_raw_input_and_billing(settings, stream):
    patch = "*** Begin Patch\n*** Add File: result.txt\n+verified\n*** End Patch\n"
    grammar = {"type": "grammar", "grammar": {"syntax": "lark", "definition": 'start: "patch"'}}
    tools = [
        {"type": "function", "function": {"name": "view", "parameters": {"type": "object"}}},
        {"type": "custom", "custom": {"name": "apply_patch", "description": "Edit local files.", "format": grammar}},
    ]
    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["tools"] == [
            {"type": "function", "name": "view", "parameters": {"type": "object"}},
            {"type": "custom", "name": "apply_patch", "description": "Edit local files.",
             "format": {"type": "grammar", "syntax": "lark", "definition": 'start: "patch"'}},
        ]
        assert payload["tool_choice"] == {"type": "custom", "name": "apply_patch"}
        data = response_data()
        if len(requests) == 1:
            data["output"] = [
                {"type": "function_call", "call_id": "call_view", "name": "view", "arguments": "{}"},
                {"type": "custom_tool_call", "call_id": "call_patch", "name": "apply_patch", "input": patch},
            ]
        else:
            assert payload["input"][-4:] == [
                {"type": "function_call", "call_id": "call_view", "name": "view", "arguments": "{}"},
                {"type": "custom_tool_call", "call_id": "call_patch", "name": "apply_patch", "input": patch},
                {"type": "function_call_output", "call_id": "call_view", "output": "empty"},
                {"type": "custom_tool_call_output", "call_id": "call_patch", "output": "File created"},
            ]
        if stream:
            return httpx.Response(200, text="data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n")
        return httpx.Response(200, json=data)

    with TestClient(create_app(settings, transport=httpx.MockTransport(upstream))) as client:
        auth = issued_auth(client)
        request = {**PAYLOAD, "stream": stream, "tools": tools,
                   "tool_choice": {"type": "custom", "custom": {"name": "apply_patch"}}}
        result = client.post("/v1/chat/completions", headers=auth, json=request)
        assert result.status_code == 200, result.text
        body = json.loads(result.text.splitlines()[0][6:]) if stream else result.json()
        choice = body["choices"][0]
        message = choice["delta" if stream else "message"]
        calls = [{key: value for key, value in tool.items() if key != "index"}
                 for tool in message["tool_calls"]]
        assert calls[1] == {"id": "call_patch", "type": "custom",
                            "custom": {"name": "apply_patch", "input": patch}}
        assert choice["finish_reason"] == "tool_calls"
        request["messages"] = [
            *PAYLOAD["messages"],
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "tool_call_id": "call_view", "content": "empty"},
            {"role": "tool", "tool_call_id": "call_patch", "content": "File created"},
        ]
        assert client.post("/v1/chat/completions", headers=auth, json=request).status_code == 200
        assert len(requests) == 2
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 30


@pytest.mark.parametrize("stream", [False, True])
def test_tool_calls_and_reasoning_usage_reach_client_without_provider_metadata(settings, stream):
    data = response_data()
    data["output"] = [
        {"type": "reasoning", "encrypted_content": "private-provider-state"},
        *[{"type": "function_call", "call_id": call, "name": "view", "arguments": '{}'}
          for call in ("call_a", "call_b")],
    ]
    data["usage"] = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                     "input_tokens_details": {"cached_tokens": 80},
                     "output_tokens_details": {"reasoning_tokens": 10}}

    def upstream(request):
        assert json.loads(request.content)["reasoning"] == {"effort": "high"}
        return (httpx.Response(200, text="data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n")
                if stream else httpx.Response(200, json=data))

    with TestClient(create_app(settings, transport=httpx.MockTransport(upstream))) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json={**PAYLOAD, "stream": stream})
        assert result.status_code == 200 and "private-provider-state" not in result.text
        body = json.loads(result.text.splitlines()[0][6:]) if stream else result.json()
        choice = body["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        assert [call["id"] for call in choice["delta" if stream else "message"]["tool_calls"]] == ["call_a", "call_b"]
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 120
        assert usage_total(body) == 120


def test_output_cap_is_reported_as_length_and_usage_is_preserved():
    data = {**response_data(), "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    result = completion(data)
    assert result["choices"][0]["finish_reason"] == "length"
    assert usage_total(result) == 15


@pytest.mark.parametrize("mutation", [
    {"reasoning_effort": "arbitrary"},
    {"messages": [{"role": "tool", "content": "missing call id"}]},
    {"tool_choice": {"type": "function"}},
    {"tools": [{"type": "custom", "custom": "invalid"}]},
    {"tools": [{"type": "custom"}]},
    {"tool_choice": {"type": "custom"}},
    {"tool_choice": {"type": "web_search"}},
    {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "call_patch", "type": "custom", "custom": {"name": "apply_patch", "input": {}}},
    ]}]},
])
def test_invalid_client_parameters_fail_before_spending(settings, mutation):
    with TestClient(create_app(settings)) as client:
        auth = issued_auth(client)
        result = client.post("/v1/chat/completions", headers=auth, json={**PAYLOAD, **mutation})
        assert result.status_code == 400
        assert client.get("/trial/status", headers=auth).json()["tokens_used"] == 0
