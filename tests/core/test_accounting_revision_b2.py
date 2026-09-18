"""Acceptance regressions for independent B1–B4 (not defect-asserting probes)."""
import json
import os
import threading
import time
from dataclasses import replace
from unittest.mock import patch

import pytest

from argus.core import cost_control as cc
from argus.core import finalization_intents as fi
from argus.core.accounting_integrity import AccountingIntegrityError
from argus.core.usage import UsageLedger, UsageRecord, summarize_usage


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('ARGUS_', 'COPILOT_')):
            monkeypatch.delenv(key, raising=False)
    for key, value in {'HOME': str(tmp_path), 'ARGUS_SKILL_HOME': str(tmp_path),
                       'ARGUS_SKILL_COST_CONTROL': '1',
                       'ARGUS_SKILL_UNPRICED_COST_POLICY': 'block'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'projects' / 'acceptance'
    p.mkdir(parents=True)
    return p


def receipt(project, call='original', amount=30):
    return UsageRecord.from_jsonable(dict(call_id=call, project_id=project.name,
        provider='pi', model='synthetic', run_label='test', status='completed',
        pricing_status='priced', cost_usd=amount, started_at=time.time()-1, completed_at=time.time()))


def reserve(root, project, call='original', **kw):
    return cc.reserve_call_budget(call_id=call, project_root=project, mission_id=None,
        provider='pi', model='synthetic', run_label='test', global_root=root,
        global_daily_cap_usd=10, **kw)


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def event_record(project, call, cost, **kw):
    return replace(receipt(project, call, cost), provider='copilot', model_usage=({
        'session_id': 'shared-session', 'usage_event_id': 42, 'cost_usd': cost, **kw},))


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('old', [False, True])
def test_conflicting_events_block_before_daily_fold(tmp_path, project, external, old):
    other = tmp_path / 'external' if external else project
    other.mkdir(exist_ok=True)
    first = event_record(project, 'first', 0)
    if old:
        first = replace(first, started_at=1, completed_at=2)
    UsageLedger(project).append(first)
    late = event_record(other, 'late', 100)
    before = contents(tmp_path)
    if not external:
        with pytest.raises(AccountingIntegrityError):
            UsageLedger(other).append(late)
        assert contents(tmp_path) == before
    else:
        UsageLedger(other).append(late)
        before = contents(tmp_path)
        with pytest.raises(AccountingIntegrityError):
            reserve(tmp_path, other, 'next')
        assert contents(tmp_path) == before


@pytest.mark.parametrize('external', [False, True])
def test_identical_events_remain_single_charge(tmp_path, project, external):
    other = tmp_path / 'external' if external else project
    other.mkdir(exist_ok=True)
    UsageLedger(project).append(event_record(project, 'first', 3))
    UsageLedger(other).append(event_record(other, 'second', 3))
    call, why = reserve(tmp_path, other, 'next')
    assert call is not None and not why
    assert cc.global_daily_usage_summary(global_root=tmp_path).known_cost_usd == 3
    call.release()


@pytest.mark.parametrize('detail,fields', [
    ({}, {}), ({'cost_usd': 1}, {}),
    ({'cost_usd': 100, 'input_tokens': 1}, {'input_tokens': 100}),
    ({'cost_usd': 100}, {'total_nano_aiu': 10_000_000_000_000}),
    ({'cost_usd': 100, 'total_nano_aiu': 1}, {}),
])
def test_lossy_detail_rejected_before_mutation(tmp_path, project, detail, fields):
    call, _ = reserve(tmp_path, project)
    bad = replace(receipt(project, amount=100), model_usage=(detail,), **fields)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(bad)
    assert contents(tmp_path) == before
    call.intent.detach()


@pytest.mark.parametrize('lifecycle', ['failed', 'prepared'])
@pytest.mark.parametrize('reason', ['cleanup', 'not_started'])
def test_release_cannot_forgive_executed_or_failed(tmp_path, project, lifecycle, reason):
    call, _ = reserve(tmp_path, project)
    if lifecycle == 'failed':
        call.finalization_failed('provider response lost')
    else:
        call.prepare_finalization(receipt(project))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.release(reason=reason)
    assert contents(tmp_path) == before
    assert not call._closed
    call.intent.detach()


def test_genuinely_not_started_release_valid(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    assert call.release(reason='pre-spawn denial')
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_money_fold_itself_refuses_conflicts_and_hidden_cost(project):
    with pytest.raises(AccountingIntegrityError):
        summarize_usage([event_record(project, 'first', 0), event_record(project, 'second', 100)])
    with pytest.raises(AccountingIntegrityError):
        summarize_usage([replace(receipt(project, amount=100), model_usage=({},))])


def test_250_closed_intents_single_scan_and_real_settlement(tmp_path, project):
    def bill(i):
        return replace(receipt(project, f'call-{i}', 0), started_at=1, completed_at=2)
    call, _ = reserve(tmp_path, project, 'call-0')
    call.settle(bill(0))
    template = json.loads(call.intent.path.read_text())
    for i in range(1, 250):
        data = json.loads(json.dumps(template))
        for key in ('reservation', 'obligation'):
            data[key]['id'] = f'id-{i}'
            data[key]['call_id'] = f'call-{i}'
        data['receipts'] = [bill(i).to_jsonable()]
        fi._lease_path(tmp_path, f'id-{i}').write_text(json.dumps(data))
    UsageLedger(project).append_many(bill(i) for i in range(1, 250))
    active, _ = reserve(tmp_path, project, 'call-250')
    entered = threading.Event()
    outcome = []
    scans = []
    original = fi.strict_jsonl
    def counted(*args, **kwargs):
        scans.append(args[0])
        entered.set()
        return original(*args, **kwargs)
    def settle():
        assert entered.wait(20)
        try:
            outcome.append(active.settle(bill(250)))
        except Exception as exc:
            outcome.append(exc)
            active.finalization_failed('settlement: ' + type(exc).__name__)
    worker = threading.Thread(target=settle)
    worker.start()
    with patch.object(fi, 'strict_jsonl', side_effect=counted):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    worker.join(30)
    assert not worker.is_alive()
    assert len(scans) == 1
    assert outcome == [True]
    assert json.loads(active.intent.path.read_text())['phase'] == 'closed'
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert len(UsageLedger(project).records()) == 251


@pytest.mark.parametrize('field', ['input_tokens', 'cached_input_tokens', 'cache_write_tokens',
                                  'output_tokens', 'reasoning_output_tokens', 'total_nano_aiu'])
def test_every_fold_identity_field_is_checked_before_filter(tmp_path, project, field):
    # For nano-AIU change cost consistently too; the event is still contradictory.
    first = event_record(project, 'first', 0, **{field: 0})
    late = event_record(project, 'late', 0 if field != 'total_nano_aiu' else 1e-11,
                        **{field: 1})
    UsageLedger(project).append(replace(first, started_at=1, completed_at=2))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        UsageLedger(project).append(late)
    assert contents(tmp_path) == before


def test_new_external_project_preflight_checks_combined_evidence(tmp_path, project):
    external = tmp_path / 'external'
    external.mkdir()
    UsageLedger(project).append(event_record(project, 'first', 0))
    UsageLedger(external).append(event_record(external, 'late', 100))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=external, global_root=tmp_path)
    assert contents(tmp_path) == before


def test_normalization_cannot_erase_conflicting_event(project):
    raw = receipt(project).to_jsonable()
    raw['model_usage'] = [dict(session_id=' shared ', usage_event_id=0, cost_usd=0),
                          dict(session_id='shared', usage_event_id=0, cost_usd=100)]
    with pytest.raises(AccountingIntegrityError):
        UsageRecord.from_jsonable(raw)


@pytest.mark.parametrize('amount', [0.01, 3, 1234])
def test_cost_lower_bound_generalized_not_single_hundred_example(tmp_path, project, amount):
    call, _ = reserve(tmp_path, project)
    bad = replace(receipt(project, amount=amount), model_usage=({'cost_usd': amount / 2},))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(bad)
    assert contents(tmp_path) == before
    call.intent.detach()


def test_duplicate_models_cannot_explain_twice_the_bill(tmp_path, project):
    item = dict(session_id='shared', usage_event_id=42, cost_usd=3)
    bad = replace(receipt(project, amount=6), model_usage=(item, item))
    with pytest.raises(AccountingIntegrityError):
        UsageLedger(project).append(bad)
    good = replace(bad, cost_usd=3)
    UsageLedger(project).append(good)
    assert UsageLedger(project).summary().known_cost_usd == 3


def test_missing_token_detail_retains_explicit_conservative_floor(project):
    from argus.core.usage import _deduplicated_usage_contributions
    record = replace(event_record(project, 'first', 3), input_tokens=100)
    rows = _deduplicated_usage_contributions([record])
    assert any(r.get('provenance') == 'call_aggregate_token_lower_bound'
               and r.get('input_tokens') == 100 for r in rows)
    summary = summarize_usage([record])
    assert summary.known_cost_usd == 3
    assert summary.input_tokens == 100


def test_possible_execution_is_durable_and_cannot_be_released(tmp_path, project):
    from types import SimpleNamespace

    from argus.adapters.agent_cli_backend._accounting_admission import accounting_admission
    ctx = SimpleNamespace(call_id='original', usage_project_root=project,
        execution_project_root=project,
        usage_global_root=tmp_path, usage_mission_id=None, run_label='test',
        backend=SimpleNamespace(_backend_name='pi'))
    admission = accounting_admission(ctx, 'synthetic')
    call = admission.reservation
    admission.before_dispatch()
    assert json.loads(call.intent.path.read_text()).get('execution_started') is True
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.release(reason='not_started')
    assert contents(tmp_path) == before
    call.finalization_failed('unknown execution outcome')
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_failed_lifecycle_cannot_be_reset_by_preparing_denial(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    call.finalization_failed('unknown execution outcome')
    denied = replace(receipt(project, amount=0), status='denied', pricing_status='not_billed')
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.prepare_finalization(denied)
    assert contents(tmp_path) == before
    with pytest.raises(AccountingIntegrityError):
        call.release(reason='not_started')
    assert contents(tmp_path) == before


def test_prepared_genuine_denial_releases_without_execution(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    call.prepare_finalization(replace(receipt(project, amount=0), status='denied',
                                      pricing_status='not_billed'), error='pre-spawn denial')
    assert call.release(reason='pre-spawn denial')
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)


def test_invocation_cache_does_not_hide_replaced_canonical_receipt(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    call.settle(receipt(project, amount=3))
    cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    # Replace, do not edit the original receipt in place; old view must not live
    # beyond a preflight invocation even when length and call identity match.
    path = project / 'usage.jsonl'
    row = json.loads(path.read_text())
    row['cost_usd'] = 4
    replacement = project / 'replacement'
    replacement.write_text(json.dumps(row) + '\n')
    replacement.replace(path)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert contents(tmp_path) == before


def test_aggregate_nano_receipt_preserves_known_money_without_dollar_field(project):
    from argus.core.usage import _deduplicated_usage_contributions
    record = replace(receipt(project, amount=None), pricing_status='partial',
                     total_nano_aiu=100_000_000_000)
    assert summarize_usage([record]).known_cost_usd == 1
    assert _deduplicated_usage_contributions([record])[0]['provenance'] == 'canonical_aggregate_nano_aiu'


def test_aggregate_nano_and_dollars_cannot_contradict(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(replace(receipt(project, amount=1), total_nano_aiu=1))
    assert contents(tmp_path) == before
    call.intent.detach()


def test_closed_release_without_checked_lifecycle_cannot_hide_legacy_failure(tmp_path, project):
    call, _ = reserve(tmp_path, project)
    call.release()
    data = json.loads(call.intent.path.read_text())
    for field in ('lifecycle_checked', 'execution_started', 'failure_recorded'):
        data['release_evidence'].pop(field, None)
    data['errors'] = ['provider response lost', 'cleanup']
    call.intent.path.write_text(json.dumps(data))
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=tmp_path)
    assert contents(tmp_path) == before


@pytest.mark.parametrize('amount,missing', [(100, 1e-11), (1e12, 0.5)])
def test_money_validation_tolerance_cannot_forgive_fraction_of_bill(tmp_path, project, amount, missing):
    call, _ = reserve(tmp_path, project)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.settle(replace(receipt(project, amount=amount), model_usage=({'cost_usd': amount-missing},)))
    assert contents(tmp_path) == before
    call.intent.detach()


@pytest.mark.parametrize('pricing', ['priced', 'not_billed'])
def test_prepared_denied_label_cannot_forgive_positive_receipt(tmp_path, project, pricing):
    call, _ = reserve(tmp_path, project)
    before = contents(tmp_path)
    with pytest.raises(AccountingIntegrityError):
        call.prepare_finalization(replace(receipt(project, amount=3), status='denied', pricing_status=pricing))
    assert contents(tmp_path) == before
    # Rejected input never entered transport or created debt; ordinary release is valid.
    assert call.release(reason='not_started')
