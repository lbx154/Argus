"""A3-only controls; actual callers/backend, synthetic state and fake transport.

Reuse the committed A2 fixtures without changing their tests or assertions.
Only the explicitly named I/O/redactor-failure controls inject additional faults.
"""
import errno
import json
import traceback
from pathlib import Path

import pytest

from tests.core.test_execution_ownership import (
    isolated, transport, register, pause_api, review,
)
from argus.core.dispatch_ownership import resolve_dispatch_project, DispatchOwnershipError
from argus.core.project import project_fingerprint
from argus.verticals.research._reviewer_runner_fallback import ReviewerRunnerError


@pytest.fixture(autouse=True)
def supervisor_config(isolated, monkeypatch):
    from argus.tools.subagent import _llm
    monkeypatch.setenv('ARGUS_SKILL_SUPERVISOR_BACKEND', 'codex')
    monkeypatch.setenv('ARGUS_SKILL_SUPERVISOR_RUNNER_BIN', '/usr/bin/true')
    _llm._SUPERVISOR_BACKENDS.clear()
    yield
    _llm._SUPERVISOR_BACKENDS.clear()


def supervisor(wd):
    from argus.tools.subagent._llm import _run_supervisor_with_usage
    return _run_supervisor_with_usage('synthetic', 'gpt-5.4', str(wd),
        thread_id='synthetic-resume', run_label='synthetic-a3')


def snapshot(home):
    return {str(p.relative_to(home)): p.read_bytes()
            for p in home.rglob('*') if p.is_file()}


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor', 'resolver'])
@pytest.mark.parametrize('secret_source', ['env', 'vault'])
@pytest.mark.parametrize('fault', ['duplicate-key', 'missing-workdir'])
def test_ownership_diagnostic_and_traceback_safe_without_writes(
        tmp_path, monkeypatch, transport, entry, secret_source, fault):
    from argus.core.secret_guard import known_secret_values
    from argus.core.usage import UsageLedger
    from argus.adapters.agent_cli_backend import _exec_finalize, _accounting_admission
    home, owner, wd = register(tmp_path, sid='s-current')
    marker = 'SYNTHETIC-A3-OWNERSHIP-SECRET-123456789'
    if secret_source == 'env':
        monkeypatch.setenv('SYNTHETIC_API_KEY', marker)
    else:
        vault = tmp_path/'synthetic-vault.json'
        vault.write_text(json.dumps({'api_key': marker}))
        monkeypatch.setenv('ARGUS_SKILL_CAPABILITY_VAULT', str(vault))
    assert marker in known_secret_values()
    if fault == 'duplicate-key':
        (owner/'session.json').write_text('{"id":"s-current","'+marker+'":1,"'+marker+'":2}')
        expected = 'ValueError: duplicate object key:'
    else:
        wd = tmp_path/marker
        expected = 'FileNotFoundError:'
    for p in [home/'usage.jsonl', owner/'usage.jsonl', home/'cost-control.json']:
        p.write_bytes(b'synthetic damaged accounting\x00')
    before = snapshot(home)
    writes = []
    def unexpected(*args, **kwargs):
        writes.append('unexpected')
        raise AssertionError('ownership denial reached accounting')
    monkeypatch.setattr(UsageLedger, 'append', unexpected)
    monkeypatch.setattr(_exec_finalize, 'record_metric', unexpected)
    monkeypatch.setattr(_exec_finalize, 'finalize_result', unexpected)
    monkeypatch.setattr(_accounting_admission, 'accounting_admission', unexpected)
    with pytest.raises(Exception) as exc:
        if entry == 'resolver':
            resolve_dispatch_project(project=None, root=home, working_dir=str(wd), require_project=True)
        else:
            (review if entry == 'reviewer' else supervisor)(wd)
    diagnostic = str(exc.value)
    assert expected in diagnostic and 'execution ownership denied' in diagnostic
    assert '<REDACTED:known-secret>' in diagnostic and marker not in diagnostic
    assert marker not in ''.join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert not writes and not transport and snapshot(home) == before


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor'])
@pytest.mark.parametrize('stage', ['known-values', 'redact'])
def test_redaction_failure_denies_without_raw_traceback(tmp_path, monkeypatch, transport, entry, stage):
    from argus.core import secret_guard
    home, owner, wd = register(tmp_path, sid='s-current')
    marker = 'SYNTHETIC-A3-REDACTOR-SECRET-123456789'
    monkeypatch.setenv('SYNTHETIC_API_KEY', marker)
    (owner/'session.json').write_text('{"id":"s-current","'+marker+'":1,"'+marker+'":2}')
    before = snapshot(home)
    original_values = secret_guard.known_secret_values
    def fail(*args, **kwargs):
        # Backend construction also collects secrets; inject only at the
        # resolver's diagnostic boundary, not an unrelated constructor site.
        import inspect
        if any(f.function == 'resolve_dispatch_project' for f in inspect.stack()):
            raise RuntimeError('synthetic redactor failure '+marker)
        return original_values(*args, **kwargs)
    monkeypatch.setattr(secret_guard,
        'known_secret_values' if stage == 'known-values' else 'redact_secrets_text', fail)
    with pytest.raises(Exception) as exc:
        (review if entry == 'reviewer' else supervisor)(wd)
    assert 'secret redaction failed' in str(exc.value)
    assert marker not in ''.join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert not transport and snapshot(home) == before


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor'])
@pytest.mark.parametrize('stale_first', [False, True])
@pytest.mark.parametrize('stale_kind', ['deleted', 'dangling-workspace-alias', 'legacy-cwd'])
def test_unrelated_stale_discovery_order_alias_and_descendant(
        tmp_path, transport, entry, stale_first, stale_kind):
    def stale():
        old = tmp_path/'old-work'; old.mkdir()
        if stale_kind == 'dangling-workspace-alias':
            alias = tmp_path/'old-alias'; alias.symlink_to(old, target_is_directory=True)
            home, owner, _ = register(tmp_path, sid='s-obsolete', workdir=alias)
        else:
            home, owner, _ = register(tmp_path, sid='s-obsolete', workdir=old)
        if stale_kind == 'legacy-cwd':
            (owner/'session.json').write_text(json.dumps({'id': owner.name, 'cwd': str(old)}))
        old.rmdir()
        return owner, (owner/'session.json').read_bytes()
    if stale_first:
        old, raw = stale()
    home, owner, wd = register(tmp_path, sid='s-current')
    sub = wd/'paper'; sub.mkdir()
    if not stale_first:
        old, raw = stale()
    call = review if entry == 'reviewer' else supervisor
    assert call(sub)[0] and len(transport) == 1
    pause_api(home, owner)
    if entry == 'reviewer':
        with pytest.raises(ReviewerRunnerError, match='PAUSED'):
            call(sub)
    else:
        assert call(sub)[0] == []
    assert len(transport) == 1 and (old/'session.json').read_bytes() == raw


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor'])
@pytest.mark.parametrize('damage', ['current-json', 'unrelated-json', 'identity', 'fingerprint-stale', 'ambiguous'])
def test_stale_isolation_does_not_hide_corruption_or_ambiguity(tmp_path, transport, entry, damage):
    home, owner, wd = register(tmp_path, sid='s-current')
    _, stale, old = register(tmp_path, sid='s-stale', workdir=tmp_path/'old')
    old.rmdir()
    if damage in ('current-json', 'unrelated-json'):
        ((owner if damage == 'current-json' else stale)/'session.json').write_bytes(b'{bad')
    elif damage == 'identity':
        (owner/'session.json').write_text(json.dumps({'id':'wrong', 'workdir':str(wd)}))
    elif damage == 'fingerprint-stale':
        fingerprint = home/'projects'/project_fingerprint(wd).fingerprint
        fingerprint.mkdir()
        (fingerprint/'session.json').write_text(json.dumps({'id': fingerprint.name, 'workdir': str(old)}))
    else:
        register(tmp_path, sid='s-second', workdir=wd)
        pause_api(home, owner)  # never choose the arbitrary unpaused candidate
    before = snapshot(home)
    with pytest.raises(Exception) as exc:
        (review if entry == 'reviewer' else supervisor)(wd)
    assert 'execution ownership denied' in str(exc.value)
    if damage == 'ambiguous':
        assert 'ambiguous' in str(exc.value)
    assert not transport and snapshot(home) == before


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor'])
def test_canonical_inheritance_precedes_unrelated_corrupt_scan(tmp_path, monkeypatch, transport, entry):
    home, owner, wd = register(tmp_path, sid='s-current')
    _, old, obsolete = register(tmp_path, sid='s-old', workdir=tmp_path/'obsolete')
    obsolete.rmdir(); (old/'session.json').write_bytes(b'{bad')
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(owner))
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', owner.name)
    call = review if entry == 'reviewer' else supervisor
    assert call(wd)[0] and len(transport) == 1
    pause_api(home, owner)
    if entry == 'reviewer':
        with pytest.raises(ReviewerRunnerError, match='PAUSED'):
            call(wd)
    else:
        assert call(wd)[0] == []
    monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', 's-conflicting')
    with pytest.raises(Exception, match='conflicting inherited'):
        call(wd)
    assert len(transport) == 1 and (old/'session.json').read_bytes() == b'{bad'


@pytest.mark.parametrize('binding', ['explicit', 'inherited'])
def test_bound_actual_owner_missing_workdir_is_not_skipped(tmp_path, monkeypatch, binding):
    home, owner, wd = register(tmp_path, sid='s-current')
    wd.rmdir()
    if binding == 'inherited':
        monkeypatch.setenv('ARGUS_SKILL_SESSION_ROOT', str(owner))
        monkeypatch.setenv('ARGUS_SKILL_SESSION_ID', owner.name)
    with pytest.raises(DispatchOwnershipError, match='FileNotFoundError'):
        resolve_dispatch_project(project=owner if binding == 'explicit' else None,
            root=home, working_dir=str(tmp_path), require_project=True)


@pytest.mark.parametrize('error', [PermissionError(errno.EACCES, 'synthetic denied'),
                                  OSError(errno.EIO, 'synthetic I/O failure')])
def test_discovery_does_not_ignore_non_missing_io_failure(tmp_path, monkeypatch, error):
    home, owner, wd = register(tmp_path, sid='s-current')
    _, stale, old = register(tmp_path, sid='s-old', workdir=tmp_path/'old')
    original = Path.resolve
    def fault(self, *args, **kwargs):
        if self == old and kwargs.get('strict') is True:
            raise error
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', fault)
    with pytest.raises(DispatchOwnershipError, match='synthetic'):
        resolve_dispatch_project(project=None, root=home, working_dir=str(wd), require_project=True)


@pytest.mark.parametrize('entry', ['reviewer', 'supervisor'])
def test_missing_component_cannot_hide_potential_current_owner(tmp_path, transport, entry):
    home, owner, wd = register(tmp_path, sid='s-current')
    # Non-strict resolution looks like the current owner, but strict traversal
    # fails. It must not be silently discarded in favor of the healthy entry.
    suspect = home/'projects/s-suspect'; suspect.mkdir()
    (suspect/'session.json').write_text(json.dumps({
        'id': suspect.name, 'workdir': str(wd/'missing'/'..')}))
    with pytest.raises(Exception, match='FileNotFoundError'):
        (review if entry == 'reviewer' else supervisor)(wd)
    assert not transport
