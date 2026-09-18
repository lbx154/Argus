"""A2 author regressions: actual backend/API/callers, fake provider transport only."""
import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.dispatch_admission import AccountingAdmission
from argus.core.models import RunnerOptions
from argus.core.project import project_fingerprint
from argus.core.session import SessionMeta, write_session_meta
from argus.verticals.research._reviewer_runner_fallback import (
    run_reviewer_prompt_via_runner, ReviewerRunnerError,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('ARGUS_', 'COPILOT_', 'CODEX_')):
            monkeypatch.delenv(key, raising=False)
    for key, value in {
        'ARGUS_SKILL_HOME': str(tmp_path / 'home'), 'HOME': str(tmp_path / 'user'),
        'ARGUS_SKILL_COST_CONTROL': '0', 'ARGUS_SKILL_CODEX_GUARD': '0',
        'ARGUS_SKILL_SAFE_MODE': '1', 'ARGUS_SKILL_REVIEWER_BACKEND': 'codex',
        'ARGUS_SKILL_REVIEWER_MODEL': 'gpt-5.4',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def transport(monkeypatch):
    from argus.agent_cli.agent_cli_runner import AgentCliRunner
    from argus.agent_cli.models import AgentRunResult
    calls = []
    def fake(self, **kwargs):
        calls.append(kwargs)
        return AgentRunResult(command=['synthetic'], exit_code=0,
            thread_id='synthetic-thread', agent_messages=['synthetic review'],
            json_events=[], stdout_lines=[], stderr_lines=[], turn_completed=True,
            turn_failed=False)
    monkeypatch.setattr(AgentCliRunner, 'run_exec', fake)
    return calls


def register(tmp_path, *, sid=None, workdir=None, bind=True):
    home = tmp_path / 'home'
    wd = workdir or tmp_path / 'manuscript'
    wd.mkdir(exist_ok=True)
    sid = sid or project_fingerprint(wd).fingerprint
    write_session_meta(home, SessionMeta(id=sid, created=1, last_active=1,
        workdir=str(wd) if bind else ''))
    return home, home / 'projects' / sid, wd


def pause_api(home, project):
    from fastapi.testclient import TestClient
    from argus.webapi.server import create_app
    with TestClient(create_app(global_root=home, auth_token='synthetic-auth')) as client:
        response = client.post(f'/api/projects/{project.name}/dispatch-safety/quiesce',
            headers={'Authorization': 'Bearer synthetic-auth'},
            json={'expected_epoch': 0, 'reason': 'synthetic pause'})
    assert response.status_code == 200 and response.json()['quiescent'] is True


def review(wd):
    return run_reviewer_prompt_via_runner('synthetic prose', working_dir=str(wd),
        run_label='research.academic_language_review', timeout=10)


def run(backend, wd=None):
    return backend.run_exec(prompt='synthetic', options=RunnerOptions(
        model='gpt-5.4', working_dir=str(wd) if wd else None),
        run_label='synthetic', resume_thread_id='synthetic-resume')


@pytest.mark.parametrize('legacy', [False, True])
def test_real_fallback_canonical_success_then_api_pause(tmp_path, transport, legacy):
    home, owner, wd = register(tmp_path, sid=None if legacy else 's-synthetic', bind=not legacy)
    assert review(wd)[0] == 'synthetic review'
    assert len(transport) == 1
    pause_api(home, owner)
    with pytest.raises(ReviewerRunnerError, match='PAUSED'):
        review(wd)
    assert len(transport) == 1


@pytest.mark.parametrize('caller', ['academic_language_review', 'paper_infrastructure_review'])
def test_real_paper_wrapper_reaches_paused_fallback(tmp_path, transport, caller):
    import importlib
    from tests.skills.researched_venues import EIGHT_PAGE_CONFERENCE
    mod = importlib.import_module('argus.verticals.research.' + caller)
    home, owner, wd = register(tmp_path)
    pause_api(home, owner)
    args = dict(root=wd, source_text_by_path={'paper/main.tex': 'Synthetic prose.'},
                env=None, timeout=10, venue=EIGHT_PAGE_CONFERENCE)
    if caller == 'academic_language_review':
        args['deterministic'] = {'score_1_to_5': 4, 'section_scores': {},
                                 'required_checks': {}, 'issues': []}
    with pytest.raises(RuntimeError, match='PAUSED'):
        mod._run_model_review(**args)
    assert transport == []


@pytest.mark.parametrize('case', ['unregistered', 'wrong-workdir', 'missing-workdir'])
def test_project_fallback_never_degrades_to_unscoped(tmp_path, transport, case):
    if case == 'wrong-workdir':
        register(tmp_path)
    wd = tmp_path / 'unknown'
    if case != 'missing-workdir':
        wd.mkdir()
    with pytest.raises(ReviewerRunnerError):
        review(wd)
    assert not transport


def test_explicit_wrong_project_denied(tmp_path, transport):
    home, owner, wd = register(tmp_path)
    other_wd = tmp_path / 'other'; other_wd.mkdir()
    _, other, _ = register(tmp_path, sid='s-other', workdir=other_wd)
    pause_api(home, owner)
    b = AgentCliBackend(backend='codex')
    b.set_usage_context(project_root=other, global_root=home)
    result = run(b, wd)
    assert result.exit_code != 0 and 'does not belong' in result.fatal_error
    assert not transport


@pytest.mark.parametrize('case', ['root', 'id', 'explicit', 'missing'])
def test_inherited_owner_conflicts_deny(tmp_path, monkeypatch, transport, case):
    home, owner, wd = register(tmp_path, sid='s-parent')
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(owner))
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', owner.name)
    b = AgentCliBackend(backend='codex')
    if case == 'root':
        monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(wd))
    elif case == 'id':
        monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', 's-wrong')
    elif case == 'missing':
        monkeypatch.delenv('ARGUS_SKILL_SESSION_ROOT')
    else:
        b.set_usage_context(project_root=home / 'projects/s-other', global_root=home)
    assert run(b, wd).exit_code != 0 and not transport


def test_inherited_owner_disambiguates_shared_workdir(tmp_path, monkeypatch, transport):
    home, owner, wd = register(tmp_path, sid='s-owner')
    register(tmp_path, sid='s-other', workdir=wd)
    with pytest.raises(ReviewerRunnerError, match='ambiguous'):
        review(wd)
    assert not transport
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(owner))
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', owner.name)
    assert review(wd)[0] == 'synthetic review'
    pause_api(home, owner)
    with pytest.raises(ReviewerRunnerError, match='PAUSED'):
        review(wd)
    assert len(transport) == 1


def test_workspace_symlink_resolves_same_pause(tmp_path, transport):
    home, owner, wd = register(tmp_path)
    alias = tmp_path / 'alias'; alias.symlink_to(wd, target_is_directory=True)
    pause_api(home, owner)
    with pytest.raises(ReviewerRunnerError, match='PAUSED'):
        review(alias)
    assert not transport


@pytest.mark.parametrize('case', ['project', 'collection', 'metadata'])
def test_state_symlinks_deny_without_transport(tmp_path, transport, case):
    home, owner, wd = register(tmp_path)
    if case == 'metadata':
        path = owner / 'session.json'; target = tmp_path / 'synthetic-meta'
    elif case == 'collection':
        path = owner.parent; target = tmp_path / 'synthetic-projects'
    else:
        path = owner; target = tmp_path / 'synthetic-state'
    path.rename(target); path.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ReviewerRunnerError, match='symlink'):
        review(wd)
    assert not transport


def test_log_override_cannot_move_execution_authority(tmp_path, monkeypatch, transport):
    home, owner, wd = register(tmp_path)
    incidental = tmp_path / 'logs/events.jsonl'
    monkeypatch.setenv('ARGUS_SKILL_AGENT_IO_LOG', str(incidental))
    pause_api(home, owner)
    b = AgentCliBackend(backend='codex')
    result = run(b, wd)
    assert result.exit_code != 0 and 'PAUSED' in result.fatal_error
    assert not transport and not incidental.exists()


def test_standalone_log_is_not_project_authority(tmp_path, monkeypatch, transport):
    from argus.core.dispatch_safety import quiesce_project
    incidental = tmp_path / 'logs'
    quiesce_project(root=tmp_path, project=incidental, expected_epoch=0, reason='synthetic')
    monkeypatch.setenv('ARGUS_SKILL_AGENT_IO_LOG', str(incidental / 'events.jsonl'))
    # Generic no-owner/no-workdir transport is the documented standalone mode.
    assert run(AgentCliBackend(backend='codex')).exit_code == 0
    assert len(transport) == 1
    b = AgentCliBackend(backend='codex', require_project=True)
    assert run(b).exit_code != 0 and len(transport) == 1
    assert run(b.fork()).exit_code != 0 and len(transport) == 1


def test_late_state_symlink_swap_denies(tmp_path, monkeypatch, transport):
    home, owner, wd = register(tmp_path)
    b = AgentCliBackend(backend='codex')
    b.set_usage_context(project_root=owner, global_root=home)
    original = b._translate_options
    def translated(options):
        target = tmp_path / 'moved-state'
        owner.rename(target); owner.symlink_to(target, target_is_directory=True)
        return original(options)
    monkeypatch.setattr(b, '_translate_options', translated)
    assert run(b, wd).exit_code != 0 and not transport


def test_real_supervisor_uses_inherited_session_not_fingerprint(tmp_path, monkeypatch, transport):
    from argus.tools.subagent import _llm
    home, owner, wd = register(tmp_path, sid='s-supervisor-owner')
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(owner))
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', owner.name)
    monkeypatch.setenv('ARGUS_SKILL_SUPERVISOR_BACKEND', 'codex')
    monkeypatch.setenv('ARGUS_SKILL_SUPERVISOR_RUNNER_BIN', '/usr/bin/true')
    _llm._SUPERVISOR_BACKENDS.clear()
    assert _llm._run_backend_turn('synthetic', 'gpt-5.4', str(wd), None,
        'subagent:synthetic').exit_code == 0
    pause_api(home, owner)
    assert _llm._run_backend_turn('synthetic', 'gpt-5.4', str(wd), None,
        'subagent:synthetic').exit_code != 0
    assert len(transport) == 1
    _llm._SUPERVISOR_BACKENDS.clear()


@pytest.mark.parametrize('allowed', ['false', 1, True])
def test_duck_typed_invalid_adapter_denied(tmp_path, monkeypatch, transport, allowed):
    from argus.adapters.agent_cli_backend import _accounting_admission as adapter
    home, owner, wd = register(tmp_path)
    monkeypatch.setattr(adapter, 'accounting_admission', lambda *a:
        SimpleNamespace(allowed=allowed, reservation=None, reason='',
            before_dispatch=None if allowed is True else lambda: None,
            execution_failed=lambda reason: None, report_budget_events=False))
    b = AgentCliBackend(backend='codex'); b.set_usage_context(project_root=owner, global_root=home)
    assert run(b, wd).exit_code != 0 and not transport


def test_admission_redacts_keeps_metadata_without_accounting_writes(tmp_path, monkeypatch, transport):
    from argus.adapters.agent_cli_backend import _accounting_admission as adapter
    home, owner, wd = register(tmp_path)
    damaged = [owner / 'usage.jsonl', home / 'cost-control.json']
    for path in damaged:
        path.write_bytes(b'synthetic truncated accounting evidence')
    marker = 'SYNTHETIC-A2-SECRET-123456789'
    original = OSError(errno.ENOSPC, 'original storage failure ' + marker)
    def fail(*args):
        raise original
    monkeypatch.setattr(adapter, 'accounting_admission', fail)
    b = AgentCliBackend(backend='codex', known_secret_values_override=[marker])
    b.set_usage_context(project_root=owner, global_root=home)
    result = run(b, wd)
    assert result.exit_code == -1 and result.stop_kind == 'backend_unavailable'
    assert marker not in result.fatal_error and 'original storage failure' in result.fatal_error
    assert 'OSError' in result.fatal_error and result.thread_id == 'synthetic-resume'
    assert result.call_id and result.started_at > 0 and result.completed_at >= result.started_at
    assert result.duration_ms >= 0 and str(original).endswith(marker)
    assert not transport and not (home / 'cost-finalizers').exists()
    assert all(p.read_bytes() == b'synthetic truncated accounting evidence' for p in damaged)


def test_nonaccounting_result_preparation_redacts_all_channels(tmp_path):
    from argus.adapters.agent_cli_backend._exec_context import _ExecContext
    from argus.adapters.agent_cli_backend._exec_finalize import finalize_without_accounting
    from argus.core.models import RunnerResult
    b = AgentCliBackend(backend='codex', known_secret_values_override=['SYNTHETIC-A2-SECRET-123456789'])
    ctx = _ExecContext(backend=b, prompt='', options=RunnerOptions(), run_label='synthetic',
        resume_thread_id='resume', call_id='call', started_at=1, log_path=None, io_mode='',
        usage_project_root=None, usage_mission_id=None, usage_global_root=None)
    ctx.bound_provider_session_id = 'bound'
    marker = b._known_secret_values_override[0]
    result = finalize_without_accounting(ctx, RunnerResult(exit_code=-1,
        fatal_error=marker, agent_messages=[marker], stdout_lines=[marker], stderr_lines=[marker]))
    assert marker not in repr(result)
    assert result.call_id == 'call' and result.thread_id == 'bound'


def test_real_map_detached_no_tools_turn_keeps_project_fence(tmp_path, transport):
    from argus.webapi.map_model import MapModel, MapGenerationError, _run_map_turn
    home, owner, wd = register(tmp_path)
    config = MapModel(backend='codex', model='gpt-5.4', effort=None, runner_bin='/synthetic')
    assert _run_map_turn('synthetic', None, config, project_root=owner,
                         global_root=home).exit_code == 0
    pause_api(home, owner)
    with pytest.raises(MapGenerationError):
        _run_map_turn('synthetic', None, config, project_root=owner, global_root=home)
    assert len(transport) == 1


@pytest.mark.parametrize('raw', [b'{}', b'{"id":"wrong"}', b'{"id":"a","id":"b"}', b'null'])
def test_damaged_session_registration_does_not_become_unscoped(tmp_path, transport, raw):
    home, owner, wd = register(tmp_path)
    (owner / 'session.json').write_bytes(raw)
    with pytest.raises(ReviewerRunnerError):
        review(wd)
    assert not transport and (owner / 'session.json').read_bytes() == raw


def test_fork_keeps_binding_and_does_not_retarget_other_backend(tmp_path, transport):
    home, owner, wd = register(tmp_path)
    b = AgentCliBackend(backend='codex', require_project=True)
    b.set_usage_context(project_root=owner, global_root=home, mission_id='synthetic-parent')
    child = b.fork()
    other_wd = tmp_path / 'other'; other_wd.mkdir()
    _, other, _ = register(tmp_path, sid='s-other', workdir=other_wd)
    b.set_usage_context(project_root=other, global_root=home)
    pause_api(home, owner)
    assert run(child, wd).exit_code != 0
    assert run(b, other_wd).exit_code == 0
    assert len(transport) == 1


@pytest.mark.parametrize('name', ['dispatch-safety.json', 'dispatch-execution.lock'])
def test_fence_and_lease_symlinks_fail_closed(tmp_path, transport, name):
    home, owner, wd = register(tmp_path)
    # A dangling fence must not be mistaken for absent/unpaused evidence.
    target = tmp_path / 'missing-synthetic-target'
    (owner / name).symlink_to(target)
    with pytest.raises(ReviewerRunnerError):
        review(wd)
    assert not transport and not target.exists()


def test_api_refuses_symlinked_project_without_writing_target(tmp_path):
    from fastapi.testclient import TestClient
    from argus.webapi.server import create_app
    home, owner, wd = register(tmp_path)
    target = tmp_path / 'synthetic-target'
    owner.rename(target); owner.symlink_to(target, target_is_directory=True)
    with TestClient(create_app(global_root=home, auth_token='synthetic-auth')) as client:
        response = client.post(f'/api/projects/{owner.name}/dispatch-safety/quiesce',
            headers={'Authorization': 'Bearer synthetic-auth'},
            json={'expected_epoch': 0, 'reason': 'synthetic pause'})
    assert response.status_code in {404, 503}
    assert not (target / 'dispatch-safety.json').exists()


def test_api_server_owner_is_not_replaced_by_process_session_env(tmp_path, monkeypatch):
    home, owner, wd = register(tmp_path, sid='s-target')
    _, other, _ = register(tmp_path, sid='s-process')
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(other))
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', other.name)
    pause_api(home, owner)
    assert (owner / 'dispatch-safety.json').exists()
    assert not (other / 'dispatch-safety.json').exists()
