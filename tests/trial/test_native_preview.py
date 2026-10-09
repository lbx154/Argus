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


def test_meter_rejects_unmetered_payloads_and_releases_slots_on_setup_failure(config, ledger):
    client = TestClient(model_app(config, ledger))
    auth = {'Authorization': 'Bearer meter-a'}
    assert client.post('/v1/responses', json={}).status_code == 401
    assert client.post('/v1/responses', headers=auth, json={'model': 'other'}).status_code == 400
    assert client.post('/v1/responses', headers=auth, json={'model': MODEL, 'tools': [{'type': 'web_search'}]}).status_code == 400
    assert client.post('/v1/responses', headers=auth, json={'model': MODEL, 'input': [{'type': 'input_image'}]}).status_code == 400
    for _ in range(3):  # A broken operator login must release both semaphore and reservation.
        assert client.post('/v1/responses', headers=auth, json={'model': MODEL, 'input': 'hello'}).status_code == 502
    assert ledger.status('alice')['used_usd'] == 0


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
    for name in ('FRONTDOOR_MODEL', 'PLAN_PREVIEW_MODEL', 'BOUNDED_DAG_MODEL', 'REWRITE_MODEL'):
        index = args.index('ARGUS_SKILL_' + name)
        assert args[index + 1] == MODEL
