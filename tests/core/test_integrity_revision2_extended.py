"""Adversarial revision-2 contracts; only synthetic disposable accounting state."""
import errno
import json
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest

from argus.core import cost_control as costs
from argus.core.accounting_integrity import AccountingIntegrityError, strict_jsonl
from argus.core.dispatch_safety import (
    DispatchSafetyError,
    assert_project_dispatch,
    quiesce_project,
    safety_snapshot,
)
from argus.core.usage import UsageLedger, _rewrite_usage_rows
from tests.core import test_integrity_revision2 as support

isolated = support.isolated
project = support.project
receipt = support.receipt
reserve = support.reserve


def existing_bytes(root):
    return {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_wrong_project_and_mutated_root_rejected_before_any_write(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    r.observe_cost(30)
    record = receipt(project)
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        r.settle(replace(record, project_id='wrong-project'))
    assert existing_bytes(tmp_path) == before
    other = tmp_path / 'elsewhere' / project.name
    r.project_root = other
    with pytest.raises(AccountingIntegrityError):
        r.prepare_finalization(record)
    assert existing_bytes(tmp_path) == before
    r.project_root = project
    r.intent.detach()


def test_resumed_display_model_role_aliases_do_not_change_call_identity(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    original = replace(receipt(project), model='provider-canonical-alias',
                       run_label='reviewer-r2', thread_id='resumed-session')
    assert r.settle(original)
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert UsageLedger(project).records()[0] == original


def test_closed_not_started_release_requires_evidence_but_not_usage_file(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    assert r.release(reason='interrupted before spawn')
    assert not (project / 'usage.jsonl').exists()
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    data = json.loads(r.intent.path.read_text())
    del data['release_evidence']
    r.intent.path.write_text(json.dumps(data))
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_not_started_release_cannot_discard_observation(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    r.observe_cost(4, tokens=100)
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        r.release()
    assert existing_bytes(tmp_path) == before
    assert json.loads((tmp_path / 'cost-control.json').read_text())['reservations'][0]['observed_cost_usd'] == 4
    r.intent.detach()


@pytest.mark.parametrize('fault', ['file_fsync', 'directory_fsync', 'state_replace', 'intent_close'])
def test_settlement_crash_boundaries_never_report_closed_success(tmp_path, project, fault):
    r, _ = reserve(tmp_path, project)
    original = receipt(project, cost=30)
    real_fsync = os.fsync
    real_replace = os.replace
    real_complete = r.intent.complete

    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        path = os.readlink(f'/proc/self/fd/{fd}')
        if ((fault == 'file_fsync' and path == str(project / 'usage.jsonl'))
                or (fault == 'directory_fsync' and directory and path == str(project))):
            raise OSError(errno.ENOSPC, 'synthetic fsync failure')
        return real_fsync(fd)

    def rename(src, dst):
        if fault == 'state_replace' and str(dst).endswith('/cost-control.json'):
            raise OSError(errno.ENOSPC, 'synthetic rename failure')
        return real_replace(src, dst)

    def complete(**kw):
        if fault == 'intent_close':
            raise OSError(errno.ENOSPC, 'synthetic closed-intent failure')
        return real_complete(**kw)

    with patch('os.fsync', side_effect=fsync), patch('os.replace', side_effect=rename), patch.object(r.intent, 'complete', side_effect=complete):
        with pytest.raises(OSError):
            r.settle(original)
    r.finalization_failed('synthetic interrupted settlement')
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert json.loads(r.intent.path.read_text())['phase'] == 'failed'
    if (project / 'usage.jsonl').exists():
        assert UsageLedger(project).records()[0].cost_usd == 30


def test_legitimate_repricing_keeps_exact_prior_receipt_and_readers_readonly(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    r.observe_cost(5)
    first = replace(receipt(project, cost=None), pricing_status='partial')
    r.settle(first)
    ledger = UsageLedger(project)
    second = replace(first, cost_usd=5, pricing_status='priced', model='canonical-model',
                     thread_id='late-original-session', cost_basis='token', schema_version=2)
    with ledger._locked():
        _rewrite_usage_rows(ledger.path, [second.to_jsonable()])
    third = replace(second, cost_usd=6)
    with ledger._locked():
        _rewrite_usage_rows(ledger.path, [third.to_jsonable()])
    raw = strict_jsonl(ledger.path, require_call_id=True)[0]
    assert raw['accounting_history'] == [first.to_jsonable(), second.to_jsonable()]
    before = existing_bytes(tmp_path)
    for days in (0, 1, 2, 10):
        assert costs.cost_admission_reason(global_root=tmp_path, now=time.time()+86400*days, cap=7) == ''
        assert costs.cost_control_snapshot(global_root=tmp_path, now=time.time()+86400*days)['unresolved_calls'] == 0
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before
    ledger.path.write_text(json.dumps(third.to_jsonable())+'\n')
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


@pytest.mark.parametrize('kind', ['unknown', 'partial'])
def test_closed_incomplete_obligation_cannot_lose_observed_lower_bound(tmp_path, project, kind):
    r, _ = reserve(tmp_path, project)
    r.observe_cost(30, tokens=100)
    if kind == 'unknown':
        r.settle_unknown(reason='missing provider tail')
    else:
        r.settle(replace(receipt(project, cost=1), pricing_status='partial'))
    path = tmp_path / 'cost-control.json'
    state = json.loads(path.read_text())
    state['unresolved'] = []
    path.write_text(json.dumps(state))
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


@pytest.mark.parametrize('late', ['none', 'partial', 'complete'])
def test_repeated_midnight_separates_daily_spend_and_historical_discharge(tmp_path, project, late):
    day = costs._local_day_start(time.time())
    with patch('time.time', return_value=day+3600):
        r, _ = reserve(tmp_path, project)
        r.observe_cost(5)
        r.settle_unknown(reason='missing tail')
        if late != 'none':
            record = receipt(project, cost=5 if late == 'complete' else 2)
            if late == 'partial':
                record = replace(record, pricing_status='partial')
            UsageLedger(project).append(record)
    before = existing_bytes(tmp_path)
    for days in (1, 2, 5):
        at = day + 86400*days + 3600
        reason = costs.cost_admission_reason(global_root=tmp_path, cap=1, now=at)
        snapshot = costs.cost_control_snapshot(global_root=tmp_path, now=at)
        if late == 'complete':
            assert reason == '' and snapshot['unresolved_calls'] == 0
            assert costs.global_daily_usage_summary(global_root=tmp_path, now=at).known_cost_usd == 0
        else:
            assert reason and snapshot['unresolved_calls'] == 1
            assert snapshot['unacknowledged_observed_cost_usd'] == (3 if late == 'partial' else 5)
    assert existing_bytes(tmp_path) == before


@pytest.mark.parametrize('surface', ['state', 'intent', 'fence', 'ledger'])
def test_duplicate_object_keys_block_without_mutation(tmp_path, project, surface):
    r, _ = reserve(tmp_path, project)
    if surface == 'state':
        path = tmp_path / 'cost-control.json'
        raw = path.read_text().replace('"version": 3', '"version": 2, "version": 3')
    elif surface == 'intent':
        path = r.intent.path
        raw = path.read_text().replace('"phase": "open"', '"phase": "closed", "phase": "open"')
    elif surface == 'fence':
        path = project / 'dispatch-safety.json'
        raw = '{"paused":true,"paused":false}'
    else:
        path = project / 'usage.jsonl'
        raw = json.dumps(receipt(project).to_jsonable()).replace('"cost_usd": 1.25', '"cost_usd": 100, "cost_usd": 1.25')+'\n'
    path.write_text(raw)
    before = existing_bytes(tmp_path)
    with pytest.raises(DispatchSafetyError if surface == 'fence' else AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before
    r.intent.detach()


@pytest.mark.parametrize('field,value', [('cost_usd', True), ('cost_usd', '1'), ('cost_usd', -1),
    ('cost_usd', float('inf')), ('input_tokens', 1.5), ('output_tokens', -1),
    ('input_tokens', True), ('schema_version', True), ('schema_version', '2'),
    ('project_id', ''), ('call_id', 0), ('pricing_status', 'anything'),
    ('started_at', None), ('completed_at', '10'), ('model_usage', {})])
def test_strict_usage_fields_before_reads_append_or_rewrite(tmp_path, project, field, value):
    ledger = UsageLedger(project)
    raw = json.dumps({**receipt(project).to_jsonable(), field: value})+'\n'
    ledger.path.write_text(raw)
    for operation in (lambda: ledger.records(), lambda: ledger.append(receipt(project, 'next')),
                      lambda: _rewrite_usage_rows(ledger.path, [])):
        with pytest.raises(AccountingIntegrityError):
            operation()
        assert ledger.path.read_text() == raw


@pytest.mark.parametrize('version', [1, 2, 3])
@pytest.mark.parametrize('field,value', [('observed_cost_usd', float('nan')), ('observed_cost_usd', -1),
    ('observed_cost_usd', '4'), ('observed_tokens', True), ('observed_tokens', 0.5),
    ('observed_tokens', None), ('amount_usd', False), ('pid', '123')])
def test_strict_state_types_for_all_supported_versions(tmp_path, project, version, field, value):
    r, _ = reserve(tmp_path, project)
    path = tmp_path / 'cost-control.json'
    state = json.loads(path.read_text())
    state['version'] = version
    state['reservations'][0][field] = value
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert path.read_bytes() == before
    r.intent.detach()


@pytest.mark.parametrize('surface', ['ledger', 'state', 'intent'])
def test_all_history_conflicting_duplicate_ids(tmp_path, project, surface):
    r, _ = reserve(tmp_path, project)
    if surface == 'ledger':
        first = replace(receipt(project, cost=0), completed_at=time.time()-3*86400)
        second = replace(first, cost_usd=100, completed_at=time.time())
        (project / 'usage.jsonl').write_text(json.dumps(first.to_jsonable())+'\n'+json.dumps(second.to_jsonable())+'\n')
    elif surface == 'state':
        path = tmp_path / 'cost-control.json'
        state = json.loads(path.read_text())
        state['reservations'].append({**state['reservations'][0], 'observed_cost_usd':100})
        path.write_text(json.dumps(state))
    else:
        path = r.intent.path
        data = json.loads(path.read_text())
        data['reservation']['id'] = 'different-reservation'
        from argus.core.finalization_intents import _lease_path
        _lease_path(tmp_path, 'different-reservation').write_text(json.dumps(data))
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before
    r.intent.detach()


@pytest.mark.parametrize('change', [{'epoch': True}, {'epoch': -1}, {'epoch': 1.0}, {'epoch': '1'},
    {'audit': []}, {'audit': {}}, {'reason': 'different'}, {'version': True}, {'paused': False}])
def test_fence_epoch_and_audit_schema(tmp_path, project, change):
    assert safety_snapshot(project)['paused'] is False
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='synthetic')
    path = project / 'dispatch-safety.json'
    state = json.loads(path.read_text())
    path.write_text(json.dumps({**state, **change}))
    before = path.read_bytes()
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)
    assert path.read_bytes() == before


def test_concurrent_finalization_is_once_and_cannot_resurrect_debt(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    original = receipt(project)
    r.observe_cost(1)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: r.settle(original), range(8)))
    assert sum(results) == 1
    r.observe_cost(50)
    state = json.loads((tmp_path / 'cost-control.json').read_text())
    assert state['reservations'] == [] and state['unresolved'] == []
    assert UsageLedger(project).summary().known_cost_usd == 1.25
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_external_project_snapshot_race_is_denied_without_lock_inversion(tmp_path, project):
    external = tmp_path / 'outside-project'
    external.mkdir()
    a, _ = reserve(tmp_path, external, 'a')
    original = costs._global_records
    def interleave(*args, **kw):
        records = original(*args, **kw)
        if not a._closed:
            a.settle(receipt(external, 'a', 30))
        return records
    with patch.object(costs, '_global_records', side_effect=interleave):
        b, reason = reserve(tmp_path, project, 'b', cap=10)
    assert b is None and ('snapshot changed' in reason or 'budget exhausted' in reason)
    assert UsageLedger(external).summary().known_cost_usd == 30


def test_canonical_project_symlink_alias_is_not_wrong_project(tmp_path, project):
    alias = tmp_path / 'display-alias'
    alias.symlink_to(project, target_is_directory=True)
    r, _ = reserve(tmp_path, alias)
    assert r.project_root == project.resolve()
    assert r.settle(receipt(project))
    costs.accounting_integrity_preflight(project_root=alias, global_root=tmp_path)


def test_reconciliation_cannot_change_bound_session_or_immutable_call_fields(tmp_path, project):
    ledger = UsageLedger(project)
    first = replace(receipt(project), thread_id='original-provider-session')
    ledger.append(first)
    before = ledger.path.read_bytes()
    for change in ({'thread_id':'unrelated-session'}, {'provider':'other-provider'},
                   {'project_id':'other'}, {'started_at':first.started_at+1}):
        with ledger._locked(), pytest.raises(AccountingIntegrityError):
            _rewrite_usage_rows(ledger.path, [{**first.to_jsonable(), **change}])
        assert ledger.path.read_bytes() == before


@pytest.mark.parametrize('change', [{'at':None}, {'at':True}, {'at':-1}, {'owner_pid':False},
                                    {'from_epoch':False}, {'epoch':True}, {'reason':''}])
def test_fence_audit_typed_fields(tmp_path, project, change):
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='pause')
    path = project / 'dispatch-safety.json'
    data = json.loads(path.read_text())
    data['audit'][0].update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)


def test_cross_project_duplicate_call_conflict_is_checked_before_day_filter(tmp_path, project):
    other = tmp_path / 'projects' / 'other'
    other.mkdir()
    UsageLedger(project).append(replace(receipt(project, cost=0), completed_at=time.time()-3*86400))
    UsageLedger(other).append(receipt(other, cost=100))
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before


def test_identical_duplicate_receipts_are_idempotent_not_conflicts(tmp_path, project):
    record = receipt(project)
    ledger = UsageLedger(project)
    raw = json.dumps(record.to_jsonable())+'\n'
    ledger.path.write_text(raw+raw)
    assert ledger.records() == [record]
    assert ledger.append(record) is False
    assert ledger.path.read_text() == raw+raw


def test_policy_off_receipt_and_error_journal_enospc_still_blocks_next_transport(tmp_path, project, monkeypatch):
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.core.models import RunnerOptions
    from tests.core.test_integrity_revision2 import _make_cli_result, fixture_support
    fixture_support.fake_agent_cli.__wrapped__(monkeypatch)
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', '0')
    backend = AgentCliBackend(backend='codex')
    backend.set_usage_context(project_root=project, global_root=tmp_path, mission_id='mission')
    calls = []
    def fail_write(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'synthetic all finalizer writes fail')
    def provider(*args, **kwargs):
        calls.append(1)
        monkeypatch.setattr('argus.core.finalization_intents.durable_json', fail_write)
        return _make_cli_result(json_events=[{'type':'token_count','input_tokens':100,'output_tokens':10}])
    monkeypatch.setattr(type(backend._runner), 'run_exec', provider)
    first = backend.run_exec(prompt='synthetic', options=RunnerOptions(model='gpt-5.6-sol'), run_label='engineer')
    assert first.exit_code == -1 and len(calls) == 1
    second = backend.run_exec(prompt='synthetic', options=RunnerOptions(model='gpt-5.6-sol'), run_label='reviewer')
    assert second.exit_code == -1 and len(calls) == 1
    state = json.loads((tmp_path / 'cost-control.json').read_text())
    assert state['reservations']
    intent = next((tmp_path / 'cost-finalizers').glob('*.json'))
    assert json.loads(intent.read_text())['phase'] == 'open'
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


@pytest.mark.parametrize('change', [{'cost_usd':-1}, {'input_tokens':True}, {'cost_usd':float('nan')}])
def test_invalid_in_memory_receipt_does_not_mutate_any_durable_evidence(tmp_path, project, change):
    r, _ = reserve(tmp_path, project)
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        r.prepare_finalization(replace(receipt(project), **change))
    assert existing_bytes(tmp_path) == before
    r.intent.detach()


def test_admission_cannot_prune_an_active_finalizer_just_because_receipt_arrived(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    record = receipt(project)
    UsageLedger(project).append(record)
    nxt, reason = reserve(tmp_path, project, 'next')
    assert nxt and not reason
    state = json.loads((tmp_path / 'cost-control.json').read_text())
    assert {row['call_id'] for row in state['reservations']} == {r.call_id, 'next'}
    r.settle(record)
    nxt.release()
    costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_open_intent_without_state_obligation_fails_closed(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    path = tmp_path / 'cost-control.json'
    data = json.loads(path.read_text())
    data['reservations'] = []
    path.write_text(json.dumps(data))
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before
    r.intent.detach()


def test_new_external_project_conflicting_id_cannot_be_deduplicated_away(tmp_path, project):
    external = tmp_path / 'unregistered-external'
    external.mkdir()
    UsageLedger(project).append(replace(receipt(project, cost=0), completed_at=time.time()-86400))
    UsageLedger(external).append(receipt(external, cost=100))
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        reserve(tmp_path, external, 'next', cap=1)
    assert existing_bytes(tmp_path) == before


@pytest.mark.parametrize('surface', ['intent', 'state'])
@pytest.mark.parametrize('field', ['amount_usd', 'pid', 'created_at', 'model', 'provider'])
def test_required_obligation_fields_cannot_be_null(tmp_path, project, surface, field):
    r, _ = reserve(tmp_path, project)
    path = r.intent.path if surface == 'intent' else tmp_path / 'cost-control.json'
    data = json.loads(path.read_text())
    row = data['reservation'] if surface == 'intent' else data['reservations'][0]
    row[field] = None
    path.write_text(json.dumps(data))
    before = existing_bytes(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert existing_bytes(tmp_path) == before
    r.intent.detach()


@pytest.mark.parametrize('kind', ['unknown', 'partial'])
def test_closed_pending_obligation_cannot_move_to_an_unrelated_root(tmp_path, project, kind):
    r, _ = reserve(tmp_path, project)
    r.observe_cost(30)
    if kind == 'unknown':
        r.settle_unknown(reason='missing provider tail')
    else:
        r.settle(replace(receipt(project, cost=1), pricing_status='partial'))
    path = tmp_path / 'cost-control.json'
    state = json.loads(path.read_text())
    state['unresolved'][0]['project_root'] = str(tmp_path / 'unrelated' / project.name)
    path.write_text(json.dumps(state))
    with pytest.raises(AccountingIntegrityError):
        costs.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_empty_terminal_reason_cannot_drop_the_obligation(tmp_path, project):
    r, _ = reserve(tmp_path, project)
    before = existing_bytes(tmp_path)
    for operation in (lambda: r.release(reason=''), lambda: r.settle_unknown(reason='')):
        with pytest.raises(ValueError):
            operation()
        assert existing_bytes(tmp_path) == before
    r.intent.detach()
