"""Correct-behavior regressions derived from preserved independent reproductions.
Synthetic only. Original reviewer tests remain unchanged outside this checkout.
"""
import errno
import json
import os
import stat
import time
from unittest.mock import patch

import pytest
import test_agent_cli_backend as fixture_support
from test_agent_cli_backend import _make_cli_result

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core import cost_control as costs
from argus.core.accounting_integrity import AccountingIntegrityError, strict_jsonl
from argus.core.dispatch_safety import DispatchSafetyError, assert_project_dispatch, quiesce_project
from argus.core.models import RunnerOptions
from argus.core.usage import UsageLedger, UsageRecord
from argus.webapi.server import create_app  # collect before the fake CLI fixture


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('ARGUS_', 'COPILOT_')):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ARGUS_SKILL_HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setenv('COPILOT_HOME', str(tmp_path / 'copilot'))
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', '1')
    monkeypatch.setenv('ARGUS_SKILL_CODEX_GUARD', '0')
    monkeypatch.setenv('ARGUS_SKILL_UNPRICED_COST_POLICY', 'block')
    monkeypatch.chdir(tmp_path)

@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'projects' / 'synthetic'
    p.mkdir(parents=True)
    return p

def reserve(root, project, call='synthetic', cap=1000, **kw):
    return costs.reserve_call_budget(call_id=call, project_root=project,
        mission_id='mission', provider='pi', model='synthetic-model',
        run_label='engineer', global_root=root, global_daily_cap_usd=cap, **kw)

def receipt(project, call='synthetic', cost=1.25):
    return UsageRecord.from_jsonable(dict(call_id=call, project_id=project.name,
        provider='pi', model='synthetic-model', run_label='engineer',
        status='completed', pricing_status='priced', cost_usd=cost,
        started_at=time.time()-1, completed_at=time.time()))

def test_regression_policy_off_enospc_blocks_second_provider(tmp_path, project, monkeypatch):
    fixture_support.fake_agent_cli.__wrapped__(monkeypatch)
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', '0')
    backend = AgentCliBackend(backend='codex')
    backend.set_usage_context(project_root=project, global_root=tmp_path, mission_id='mission')
    calls = []
    def provider(*args, **kwargs):
        calls.append(1)
        return _make_cli_result(json_events=[{'type':'token_count','input_tokens':100,'output_tokens':10}], thread_id='synthetic-session')
    monkeypatch.setattr(type(backend._runner), 'run_exec', provider)
    with patch.object(UsageLedger, 'append', side_effect=OSError(errno.ENOSPC, 'synthetic')):
        first = backend.run_exec(prompt='synthetic', options=RunnerOptions(model='gpt-5.6-sol'), run_label='engineer')
        assert first.exit_code == -1 and len(calls) == 1
        with pytest.raises(AccountingIntegrityError):
            costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
        second = backend.run_exec(prompt='synthetic', options=RunnerOptions(model='gpt-5.6-sol'), run_label='reviewer')
    assert second.exit_code == -1 and len(calls) == 1
    assert json.loads((tmp_path / "cost-control.json").read_text())["reservations"]
    assert list((tmp_path / "cost-finalizers").glob("*.json"))

def test_regression_wrong_call_receipt_retains_observed_obligation(tmp_path, project):
    r, why = reserve(tmp_path, project, 'actual-call')
    assert r and not why
    r.observe_cost(30)
    wrong = receipt(project, 'different-call', 1)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(AccountingIntegrityError):
        r.settle(wrong)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not UsageLedger(project).records()
    nxt, why = reserve(tmp_path, project, 'next', cap=10)
    assert nxt is None and 'budget exhausted' in why


def test_regression_closed_intent_missing_ledger_is_rejected(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    r.settle(receipt(project, cost=30))
    # Inject loss of the new ledger directory entry, as permitted by absent
    # parent-directory fsync. This models a crash outcome, not a real power cut.
    (project / 'usage.jsonl').unlink()
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, 'next', cap=10)
    assert UsageLedger(project).summary().known_cost_usd == 0


def test_regression_usage_creation_fsyncs_project_directory(tmp_path, project):
    calls = []
    real = os.fsync
    def spy(fd):
        calls.append(('dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file', os.readlink(f'/proc/self/fd/{fd}')))
        return real(fd)
    with patch('os.fsync', side_effect=spy):
        UsageLedger(project).append(receipt(project))
    assert any(kind == 'file' for kind, _ in calls)
    assert any(kind == 'dir' and path == str(project) for kind, path in calls)

@pytest.mark.parametrize('variant', ['duplicate-key', 'duplicate-call', 'negative'])
def test_regression_ambiguous_or_negative_ledger_is_rejected(tmp_path, project, variant):
    row = receipt(project, cost=0).to_jsonable()
    if variant == 'duplicate-key':
        text = json.dumps(row).replace('"cost_usd": 0.0', '"cost_usd": 100.0, "cost_usd": 0.0')
        assert text.count('"cost_usd"') == 2
    elif variant == 'duplicate-call':
        text = json.dumps(row) + '\n' + json.dumps({**row, 'cost_usd': 100.0})
    else:
        text = json.dumps({**row, 'cost_usd': -100.0}) + '\n' + json.dumps({**row, 'call_id':'positive', 'cost_usd':50.0})
    tape = project / 'usage.jsonl'
    tape.write_text(text + '\n')
    before = tape.read_bytes()
    with pytest.raises(AccountingIntegrityError):
        strict_jsonl(tape, require_call_id=True)
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, project, 'next', cap=1)
    assert tape.read_bytes() == before


def test_regression_empty_existing_fence_cannot_clear_sticky_pause(tmp_path, project):
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='synthetic pause')
    (project / 'dispatch-safety.json').write_text('{}\n')
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)

def test_regression_settled_old_day_debt_stays_discharged(tmp_path, project):
    day = costs._local_day_start(time.time())
    with patch('time.time', return_value=day+3600):
        r, _ = reserve(tmp_path, project)
        r.observe_cost(5)
        r.settle_unknown(reason='synthetic delayed receipt')
        UsageLedger(project).append(receipt(project, cost=5))
        UsageLedger(project).reconcile()
        assert costs.cost_admission_reason(global_root=tmp_path, now=day+7200) == ''
    reason = costs.cost_admission_reason(global_root=tmp_path, now=day+86400+3600)
    assert reason == ''
    snap = costs.cost_control_snapshot(global_root=tmp_path, now=day+86400+3600)
    assert snap['unacknowledged_observed_cost_usd'] == 0

def test_regression_nonfinite_v3_observation_is_rejected(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    path = tmp_path / 'cost-control.json'
    state = json.loads(path.read_text())
    state['reservations'][0]['observed_cost_usd'] = float('nan')
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    nxt, why = reserve(tmp_path, project, 'next', cap=1)
    assert nxt is None and 'invalid' in why
    assert path.read_bytes() == before
    r.intent.detach()


def test_control_readers_preserve_existing_bytes(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    r.settle(receipt(project))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    UsageLedger(project).records()
    UsageLedger(project).summary()
    costs.cost_control_snapshot(global_root=tmp_path)
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert {p for p in tmp_path.rglob('*') if p.is_file()} == set(before)


def test_control_api_scope_auth_and_strict_epoch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from argus.core.session import SessionMeta, write_session_meta
    for name in ('synthetic-a', 'synthetic-b'):
        write_session_meta(tmp_path, SessionMeta(id=name, created=1, last_active=1))
    header = {'Authorization':'Bearer synthetic-only-token'}
    path = '/api/projects/synthetic-a/dispatch-safety'
    with TestClient(create_app(global_root=tmp_path, auth_token='synthetic-only-token')) as client:
        assert client.get(path).status_code == 401
        for epoch in (True, '0', 0.5, -1):
            assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':epoch,'reason':'hold'}).status_code == 422
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold','project_root':'/tmp/other'}).status_code == 422
        assert client.post('/api/projects/missing/dispatch-safety/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 404
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 200
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 409
        assert client.get('/api/projects/synthetic-b/dispatch-safety', headers=header).json()['paused'] is False


def test_regression_concurrent_settlement_invalidates_admission_snapshot(tmp_path, project):
    # Deterministic interleaving: B captures ledger, A settles, B acquires lock.
    # Inherited defect on the reviewed baseline; revised admission must refuse.
    a, _ = reserve(tmp_path, project, 'a')
    original = costs._global_records
    def stale_after_settlement(*args, **kwargs):
        records = original(*args, **kwargs)
        if not a._closed:
            a.settle(receipt(project, 'a', 30))
        return records
    with patch.object(costs, '_global_records', side_effect=stale_after_settlement):
        b, why = reserve(tmp_path, project, 'b', cap=10)
    assert b is None and ("snapshot changed" in why or "budget exhausted" in why)
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert UsageLedger(project).summary().known_cost_usd == 30
    again, why = reserve(tmp_path, project, "retry", cap=10)
    assert again is None and "budget exhausted" in why
