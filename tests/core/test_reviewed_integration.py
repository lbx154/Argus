"""A3/B3 seam controls. Real admission/finalizer; only transport and named faults fake."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.agent_cli_backend._accounting_admission import accounting_admission
from argus.core import cost_control as cc
from argus.core.accounting_integrity import AccountingIntegrityError
from argus.core.dispatch_admission import AccountingAdmission
from argus.core.dispatch_safety import quiesce_project
from argus.core.usage import UsageLedger, UsageRecord
from tests.core import test_execution_ownership as fixtures
from tests.core.test_execution_ownership import register, run

isolated = fixtures.isolated
transport = fixtures.transport


@pytest.mark.parametrize('policy', ['0', '1'])
def test_unscoped_log_pause_has_no_authority_but_unknown_debt_survives(tmp_path, monkeypatch, transport, policy):
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', policy)
    home = tmp_path / 'home'
    incidental = tmp_path / 'logs'
    quiesce_project(root=tmp_path, project=incidental, expected_epoch=0, reason='synthetic')
    fence = (incidental / 'dispatch-safety.json').read_bytes()
    monkeypatch.setenv('ARGUS_SKILL_AGENT_IO_LOG', str(incidental / 'events.jsonl'))
    result = run(AgentCliBackend(backend='codex'))
    assert result.exit_code == 0 and len(transport) == 1
    assert (incidental / 'dispatch-safety.json').read_bytes() == fence
    records = UsageLedger(incidental).records()
    assert len(records) == 1 and records[0].call_id == result.call_id
    state = json.loads((home / 'cost-control.json').read_text())
    # Preserve baseline log-directed accounting storage, but never grant it
    # execution authority or pretend missing price evidence is a free call.
    assert len(state['unresolved']) == 1
    assert state['unresolved'][0]['call_id'] == result.call_id
    assert state['acknowledgements'] == {}
    before = (home / 'cost-control.json').read_bytes()
    # Consistently retained unknown billing is not corrupt persistence. Integrity
    # may pass; monetary policy (when enabled) independently blocks unknown cost.
    cc.accounting_integrity_preflight(global_root=home)
    assert cc.cost_control_snapshot(global_root=home)['unresolved_calls'] == 1
    assert (home / 'cost-control.json').read_bytes() == before
    if policy == '1':
        assert run(AgentCliBackend(backend='codex')).exit_code != 0
        assert len(transport) == 1


def _paid(project):
    return UsageRecord.from_jsonable(dict(call_id='existing-paid', project_id=project.name,
        provider='codex', model='gpt-5.4', run_label='test', status='completed',
        pricing_status='priced', cost_usd=30, started_at=1, completed_at=2))


def test_real_denied_adapter_supplies_mandatory_rejecting_hooks(tmp_path, monkeypatch):
    home, project, wd = register(tmp_path)
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', '1')
    monkeypatch.setenv('ARGUS_SKILL_GLOBAL_DAILY_CAP_USD', '1')
    import time
    record = _paid(project)
    record = replace(record, completed_at=time.time())
    UsageLedger(project).append(record)
    ctx = SimpleNamespace(call_id='denied', usage_project_root=project,
        usage_mission_id=None, backend=SimpleNamespace(_backend_name='codex'),
        run_label='test', usage_global_root=home, execution_project_root=project)
    result = accounting_admission(ctx, 'gpt-5.4')
    AccountingAdmission.validate(result)
    assert result.allowed is False and result.reservation is None
    assert result.reason and result.report_budget_events is False
    before = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError, match='no dispatch permission'):
        result.before_dispatch()
    with pytest.raises(RuntimeError, match='no finalizer obligation'):
        result.execution_failed('not a real call')
    assert {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('policy', ['0', '1'])
def test_b_late_damage_denies_through_a_guard_and_retains_original_obligation(tmp_path, monkeypatch, transport, policy):
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', policy)
    home, project, wd = register(tmp_path)
    b = AgentCliBackend(backend='codex')
    b.set_usage_context(project_root=project, global_root=home)
    original = b._translate_options
    damaged = b'{"deliberately_incomplete":'
    def corrupt_after_registration(options):
        (project / 'usage.jsonl').write_bytes(damaged)
        return original(options)
    monkeypatch.setattr(b, '_translate_options', corrupt_after_registration)
    result = run(b, wd)
    assert result.exit_code != 0 and not transport
    assert (project / 'usage.jsonl').read_bytes() == damaged
    state = json.loads((home / 'cost-control.json').read_text())
    assert len(state['reservations']) == 1
    assert state['reservations'][0]['call_id'] == result.call_id
    intents = list((home / 'cost-finalizers').glob('*.json'))
    assert len(intents) == 1
    intent = json.loads(intents[0].read_text())
    assert intent['reservation']['call_id'] == result.call_id
    assert intent['phase'] == 'failed'
    with pytest.raises(AccountingIntegrityError):
        cc.accounting_integrity_preflight(project_root=project, global_root=home)


def test_direct_accounting_callers_keep_default_project_pause(tmp_path):
    home, project, wd = register(tmp_path)
    quiesce_project(root=home, project=project, expected_epoch=0, reason='synthetic direct pause')
    from argus.core.dispatch_safety import DispatchSafetyError
    with pytest.raises(DispatchSafetyError):
        cc.accounting_integrity_preflight(project_root=project, global_root=home)
    with pytest.raises(DispatchSafetyError):
        cc.reserve_call_budget(call_id='direct', project_root=project, mission_id=None,
            provider='codex', model='gpt-5.4', run_label='test', global_root=home)
    assert not (home / 'cost-control.json').exists()


@pytest.mark.parametrize('policy', ['0', '1'])
def test_unscoped_accounting_log_damage_cannot_bypass_integrity(tmp_path, monkeypatch, transport, policy):
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', policy)
    incidental = tmp_path / 'logs'
    incidental.mkdir()
    raw = b'{"truncated":'
    (incidental / 'usage.jsonl').write_bytes(raw)
    monkeypatch.setenv('ARGUS_SKILL_AGENT_IO_LOG', str(incidental / 'events.jsonl'))
    result = run(AgentCliBackend(backend='codex'))
    assert result.exit_code != 0 and 'accounting integrity' in result.fatal_error
    assert not transport and (incidental / 'usage.jsonl').read_bytes() == raw
    assert not (tmp_path / 'home/cost-control.json').exists()
