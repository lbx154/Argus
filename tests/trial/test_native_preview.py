from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy.trial.native_egress import public_address
from deploy.trial.native_preview import (
    COOKIE,
    MODEL,
    Ledger,
    QuotaExceeded,
    model_app,
    portal_app,
    provider_cost,
    token_cost,
)
from deploy.trial.native_runtime import sandbox_command


@pytest.fixture
def config(tmp_path):
    return {'model': MODEL, 'revision': 'test-sha', 'session_secret': 'private-session-key',
            'relay_secret': 'private-relay-key', 'copilot_home': str(tmp_path / 'not-logged-in'),
            'tenants': {'alice': {'invite': 'invite-a', 'model_token': 'meter-a', 'web_token': 'web-a', 'socket': str(tmp_path / 'a.sock')},
                        'bob': {'invite': 'invite-b', 'model_token': 'meter-b', 'web_token': 'web-b', 'socket': str(tmp_path / 'b.sock')}}}


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / 'ledger.sqlite3', total_usd=50, user_usd=10)


def test_two_limits_are_atomic_and_never_reset(tmp_path):
    ledger = Ledger(tmp_path / 'ledger', total_usd=50, user_usd=10)
    def reserve(_):
        try:
            return ledger.reserve('alice', 1)
        except QuotaExceeded:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = [identity for identity in pool.map(reserve, range(50)) if identity]
    assert len(ids) == 10
    for tenant in ('bob', 'c', 'd', 'e'):
        ledger.reserve(tenant, 10)
    with pytest.raises(QuotaExceeded):
        ledger.reserve('new-user', 0.000001)
    reopened = Ledger(tmp_path / 'ledger', total_usd=50, user_usd=10)
    assert reopened.status('alice')['remaining_usd'] == 0
    reopened.settle(ids[0], 0.2)  # Opening the portal cannot mark live calls uncertain.
    assert reopened.status('alice')['remaining_usd'] == pytest.approx(0.8)
    with pytest.raises(ValueError, match='cannot be reset'):
        Ledger(tmp_path / 'ledger', total_usd=500, user_usd=100)


def test_a_call_larger_than_what_is_left_is_admitted_for_the_rest(ledger):
    first = ledger.reserve('alice', 6.88)
    ledger.settle(first, 6.88)
    capped = ledger.reserve('alice', 4.5)  # a long-context ceiling above the $3.12 left
    assert ledger.status('alice')['remaining_usd'] == 0
    ledger.settle(capped, 0.9)  # settles at the real cost
    assert ledger.status('alice')['remaining_usd'] == pytest.approx(2.22)
    last = ledger.reserve('alice', 5)
    ledger.settle(last, 2.5)  # overdraws by at most that one call
    assert ledger.status('alice')['remaining_usd'] == 0
    with pytest.raises(QuotaExceeded):
        ledger.reserve('alice', 0.5)


def test_missing_receipts_stay_charged_and_settlement_is_idempotent(ledger):
    identity = ledger.reserve('alice', 3)
    ledger.settle(identity, None)
    ledger.settle(identity, 0)
    assert ledger.status('alice')['used_usd'] == 3
    identity = ledger.reserve('alice', 2)
    ledger.settle(identity, 0.1234561)
    ledger.settle(identity, 1)
    assert ledger.status('alice')['used_usd'] == pytest.approx(3.123457)
    identity = ledger.reserve('bob', 1)
    ledger.recover()
    ledger.settle(identity, 0)
    assert ledger.status('bob')['used_usd'] == 1


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_amounts(ledger, value):
    with pytest.raises(ValueError):
        ledger.reserve('alice', value)


def test_authoritative_billing_only():
    from argus.provider_integrations.copilot_usage import NANO_AIU_PER_USD
    assert provider_cost({'copilot_usage': {'total_nano_aiu': NANO_AIU_PER_USD}}) == 1
    for event in ({}, {'copilot_usage': {'total_nano_aiu': -1}}, {'copilot_usage': {'total_nano_aiu': True}}):
        assert provider_cost(event) is None


def test_login_cookie_auth_and_tenant_budgets(config, ledger):
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    assert client.get('/api/projects').status_code == 401
    assert client.get('/', follow_redirects=False).headers['location'] == '/login'
    assert client.post('/login', data={'invite': 'wrong'}).status_code == 401
    assert client.post('/login', data={'invite': 'invite-a'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    assert response.status_code == 303
    assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    ledger.settle(ledger.reserve('alice', 1), 0.1)
    assert client.get('/trial/budget').json()['used_usd'] == 0.1
    assert client.post('/api/daemons', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/projects/test/config/set', json={}).status_code == 403
    client.post('/login', data={'invite': 'invite-b'}, follow_redirects=False)
    assert client.get('/trial/budget').json()['used_usd'] == 0
    # Alone: which of two same-named cookies arrives last depends on the HTTP client.
    client.cookies.clear()
    client.cookies.set(COOKIE, 'alice:9999999999:forged')
    assert client.get('/trial/budget').status_code == 401


def test_websocket_relay_auth_and_http_body(config, ledger):
    client = TestClient(portal_app(config, ledger))
    with client.websocket_connect('/_relay', headers={'Authorization': 'Bearer private-relay-key'}) as ws:
        ws.send_json({'method': 'GET', 'path': '/healthz', 'host': 'preview.example', 'headers': {}, 'body': ''})
        assert ws.receive_json()['status'] == 200
        assert json.loads(ws.receive_bytes())['revision'] == 'test-sha'
        assert ws.receive_json()['type'] == 'end'
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('/_relay'):
        pass


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '192.168.1.1'])
def test_proxy_blocks_private_destinations(host):
    with pytest.raises(ValueError, match='Private'):
        asyncio.run(public_address(host, 443))


def test_proxy_pins_validated_address_and_rejects_mixed_dns(monkeypatch):
    async def check():
        loop = asyncio.get_running_loop()
        async def dns(*args, **kwargs):
            return [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        monkeypatch.setattr(loop, 'getaddrinfo', dns)
        with pytest.raises(ValueError):
            await public_address('rebind.example', 443)
    asyncio.run(check())
    with pytest.raises(ValueError):
        asyncio.run(public_address('example.com', 22))


def test_sandbox_has_no_host_network_home_or_cross_tenant_mount(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'),
                  meter_runtime=str(tmp_path / 'meter'), copilot_package=str(tmp_path / 'public-package'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    command = sandbox_command(config, 'alice', ['/bin/true'])
    assert '--unshare-all' in command and '--share-net' not in command
    assert '--proc' in command and '--cap-drop' in command
    assert str(tmp_path / 'alice') in command and str(tmp_path / 'bob') not in command
    assert str(Path.home()) not in command
    assert config['copilot_home'] not in command
    assert command[-1] == '/bin/true'


@pytest.mark.parametrize('streaming', [True, False])
def test_meter_streams_real_receipt_and_settles_exactly(config, ledger, monkeypatch, streaming):
    home = Path(config['copilot_home'])
    home.mkdir()
    (home / 'config.json').write_text(json.dumps({'lastLoggedInUser': {'host': 'test', 'login': 'operator'},
                                                'authTokens': {'test:operator': {'token': 'operator-only'}}}))
    from argus.provider_integrations.copilot_usage import NANO_AIU_PER_USD
    seen = []
    def provider(request):
        seen.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer operator-only'
        event = {'type': 'response.completed', 'copilot_usage': {'total_nano_aiu': NANO_AIU_PER_USD // 10},
                 'response': {'id': 'response-1', 'status': 'completed', 'output': []}}
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                              content=('data: ' + json.dumps(event) + '\n\n').encode())
    original = httpx.AsyncClient
    monkeypatch.setattr('deploy.trial.native_preview.httpx.AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(provider)))
    client = TestClient(model_app(config, ledger))
    r = client.post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                    json={'model': MODEL, 'input': 'hello', 'max_output_tokens': 256, 'stream': streaming})
    assert r.status_code == 200
    assert seen[0]['stream'] is True and seen[0]['store'] is False
    assert ledger.status('alice')['used_usd'] == 0.1
    assert ledger.status('bob')['used_usd'] == 0
    if streaming:
        assert 'response.completed' in r.text
    else:
        assert r.json()['status'] == 'completed'


def test_token_usage_is_priced_and_missing_usage_keeps_the_reservation():
    from argus.core.pricing import quote_token_usage
    usage = {'input_tokens': 1_000_000, 'output_tokens': 100_000, 'input_tokens_details': {'cached_tokens': 400_000}}
    expected = quote_token_usage(MODEL, input_tokens=1_000_000, output_tokens=100_000, cached_input_tokens=400_000).cost_usd
    assert token_cost({'type': 'response.completed', 'response': {'usage': usage}}) == pytest.approx(expected)
    assert expected > 0
    for event in ({}, {'response': {}}, {'response': {'usage': {'input_tokens': 5}}},
                  {'response': {'usage': {'input_tokens': -1, 'output_tokens': 1}}}):
        assert token_cost(event) is None


def test_meter_sends_to_a_configured_upstream_and_charges_its_usage(config, ledger, monkeypatch, tmp_path):
    key = tmp_path / 'upstream.key'
    key.write_text('upstream-secret\n')
    config['upstream'] = {'base_url': 'https://relay.example/v1/', 'token_file': str(key)}
    seen = []
    def provider(request):
        seen.append(str(request.url))
        assert request.headers['authorization'] == 'Bearer upstream-secret'
        event = {'type': 'response.completed', 'response': {'id': 'r', 'status': 'completed', 'output': [],
                 'usage': {'input_tokens': 100_000, 'output_tokens': 10_000}}}
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                              content=('data: ' + json.dumps(event) + '\n\n').encode())
    original = httpx.AsyncClient
    monkeypatch.setattr('deploy.trial.native_preview.httpx.AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(provider)))
    r = TestClient(model_app(config, ledger)).post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                                                   json={'model': MODEL, 'input': 'hello', 'max_output_tokens': 256})
    assert r.status_code == 200
    assert seen == ['https://relay.example/v1/responses']
    from argus.core.pricing import quote_token_usage
    expected = quote_token_usage(MODEL, input_tokens=100_000, output_tokens=10_000, cached_input_tokens=0).cost_usd
    assert ledger.status('alice')['used_usd'] == pytest.approx(expected, abs=1e-6)


def test_meter_exhaustion_never_submits_to_provider(config, ledger, monkeypatch):
    ledger.reserve('alice', 10)
    def unexpected(**kwargs):
        raise AssertionError('Quota rejection must precede upstream submission')
    monkeypatch.setattr('deploy.trial.native_preview.httpx.AsyncClient', unexpected)
    client = TestClient(model_app(config, ledger))
    assert client.post('/v1/responses', headers={'Authorization': 'Bearer meter-a'},
                       json={'model': MODEL, 'input': 'hello'}).status_code == 402


def test_browser_stream_requires_session_and_same_origin_and_cannot_choose_other_api(config, ledger):
    from starlette.websockets import WebSocketDisconnect
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example'}):
        pass
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://evil.example', 'Cookie': cookie}):
        pass
    with client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        ws.send_json({'method': 'POST', 'path': '/api/system/update', 'body': ''})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect('wss://preview.example/trial/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        ws.send_json({'method': 'POST', 'path': '/api/projects/s-test/message/stream', 'body': '',
                      'host': 'attacker.example', 'headers': {'cookie': 'forged'}})
        assert ws.receive_json()['status'] == 503  # It uses the authenticated tenant's unavailable socket.
        assert '工作区' in json.loads(ws.receive_bytes())['detail']
        assert ws.receive_json()['type'] == 'end'


def test_all_auxiliary_model_routes_are_pinned(config, tmp_path):
    config.update(source=str(tmp_path / 'source'), python_runtime=str(tmp_path / 'python'),
                  venv=str(tmp_path / 'venv'), node_runtime=str(tmp_path / 'node'),
                  meter_runtime=str(tmp_path / 'meter'), copilot_package=str(tmp_path / 'package'))
    config['tenants']['alice']['directory'] = str(tmp_path / 'alice')
    args = sandbox_command(config, 'alice')
    for name in ('LIGHT_MODEL', 'MANAGER_MODEL'):
        index = args.index('ARGUS_SKILL_' + name)
        assert args[index + 1] == MODEL


def test_host_watchdog_observes_real_child_memory_and_threads():
    import subprocess
    import sys
    import time

    from deploy.trial.native_runtime import ResourceWatchdog

    child = subprocess.Popen([sys.executable, '-c', 'import time; data=bytearray(24*1024*1024); time.sleep(30)'])
    try:
        monitor = ResourceWatchdog(child.pid, memory_bytes=16 * 1024 * 1024)
        reason = ''
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not reason:
            reason = monitor.exceeded()
            time.sleep(0.05)
        assert reason == 'memory'
        assert ResourceWatchdog(child.pid, tasks=0).exceeded() == 'process/thread'
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize('reason', ['memory', 'process/thread', 'resource inspection'])
def test_transient_runtime_limits_exit_for_automatic_recovery(reason):
    import subprocess
    import sys

    from deploy.trial.native_runtime import stop_for_limit

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    with pytest.raises(SystemExit) as result:
        stop_for_limit(child, reason)
    assert result.value.code == 75
    assert child.poll() is not None


def test_full_storage_does_not_restart_in_a_loop():
    import subprocess
    import sys

    from deploy.trial.native_runtime import stop_for_limit

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    assert stop_for_limit(child, 'workspace storage') is None
    assert child.poll() is not None


def test_unavailable_workspace_shows_retry_page_and_preserves_allowance(config, ledger):
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    ledger.settle(ledger.reserve('alice', 1), 0.25)
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    response = client.get('/')
    assert response.status_code == 503
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['retry-after'] == '5'
    assert 'http-equiv="refresh" content="5"' in response.text
    assert '文件和试用余额已保留' in response.text
    assert client.get('/trial/budget').json()['remaining_usd'] == 9.75
    response = client.get('/api/projects')
    assert response.status_code == 503 and response.headers['retry-after'] == '5'
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with client.websocket_connect('wss://preview.example/api/projects/s-test/stream', headers={'Origin': 'https://preview.example', 'Cookie': cookie}) as ws:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as result:
            ws.receive_json()
        assert result.value.code == 1013


def test_workspace_stream_carries_the_token_in_a_header_not_the_url(config, ledger, monkeypatch):
    calls = []

    class Backend:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    def connect(path, *, uri, **kwargs):
        calls.append((uri, kwargs))
        return Backend()

    monkeypatch.setattr('deploy.trial.native_preview.unix_connect', connect)
    client = TestClient(portal_app(config, ledger), base_url='https://preview.example')
    client.post('/login', data={'invite': 'invite-a'}, follow_redirects=False)
    cookie = COOKIE + '=' + client.cookies.get(COOKIE)
    with client.websocket_connect('wss://preview.example/api/projects/s-test/stream?replay=40&token=browser',
                                  headers={'Origin': 'https://preview.example', 'Cookie': cookie}):
        pass
    uri, kwargs = calls[0]
    assert uri == 'ws://localhost/api/projects/s-test/stream?replay=40'
    assert kwargs['extra_headers'] == {'Authorization': 'Bearer web-a'}


def test_watchdog_accepts_normal_multirole_copilot_memory():
    import os
    from types import SimpleNamespace

    from deploy.trial.native_runtime import ResourceWatchdog

    class ClientProcess:
        def __init__(self, pid, memory, threads):
            self.pid, self.memory, self.thread_count = pid, memory, threads
        def children(self, recursive):
            return [ClientProcess(2, 1100 * 1024**2, 100)]
        def memory_info(self):
            return SimpleNamespace(rss=self.memory)
        def num_threads(self):
            return self.thread_count
        def cpu_times(self):
            return SimpleNamespace(user=0, system=0, children_user=0, children_system=0)

    monitor = ResourceWatchdog(os.getpid())
    monitor.process = ClientProcess(1, 1200 * 1024**2, 99)
    assert monitor.exceeded() == ''  # The previously observed 2.2 GiB / 199-thread case must remain usable.
    monitor.process.memory = 9 * 1024**3
    assert monitor.exceeded() == 'memory'


def test_storage_limit_allows_a_normal_repository_clone(tmp_path):
    from deploy.trial.native_runtime import storage_exceeded

    checkout = tmp_path / 'repository'
    checkout.mkdir()
    with (checkout / 'fixture').open('wb') as file:
        file.truncate(251 * 1024**2)
    assert not storage_exceeded(str(tmp_path))
    with (checkout / 'fixture').open('wb') as file:
        file.truncate(2 * 1024**3 + 1)
    assert storage_exceeded(str(tmp_path))
